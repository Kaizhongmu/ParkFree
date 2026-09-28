from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

import certifi
from pydantic import BaseModel, ConfigDict, Field

from parking_ai.domain import EvidenceStoragePolicy, GeoPoint
from parking_ai.geocoding.models import (
    GeocodingMatch,
    GeocodingProviderMetadata,
    GeocodingRequest,
    GeocodingResult,
    geocoding_status,
    normalized_geocoding_cache_key,
    require_aware_wall_clock,
    stable_geocoding_match_id,
)

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_PROVIDER_VERSION = "nominatim-jsonv2-v1"
NOMINATIM_ATTRIBUTION = "© OpenStreetMap contributors"
NOMINATIM_LICENSE = "Open Data Commons Open Database License (ODbL) 1.0"
MIN_REQUEST_INTERVAL_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 10.0
MAX_RESULT_LIMIT = 10
MAX_CACHE_TTL_SECONDS = 86_400.0
MAX_CACHE_ENTRIES = 1_024
MAX_RESPONSE_BYTES = 1_000_000


class GeocodingError(RuntimeError):
    """Base class for typed geocoding failures."""


class GeocodingProviderError(GeocodingError):
    """The provider could not return a successful response."""


class GeocodingResponseError(GeocodingError):
    """The provider response failed strict schema or US-boundary validation."""


class GeocodingTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object: ...


class UrllibJsonTransport:
    """Small standard-library transport; callers inject fakes in tests."""

    def __init__(self, *, max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        if max_response_bytes < 1 or max_response_bytes > MAX_RESPONSE_BYTES:
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        self._max_response_bytes = max_response_bytes
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        query = urllib.parse.urlencode(sorted(params.items()))
        request = urllib.request.Request(f"{url}?{query}", headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
                context=self._ssl_context,
            ) as response:
                body = response.read(self._max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            raise GeocodingProviderError(f"Nominatim returned HTTP status {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GeocodingProviderError("Nominatim request failed") from error
        if len(body) > self._max_response_bytes:
            raise GeocodingProviderError("Nominatim response exceeded the configured size limit")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GeocodingResponseError("Nominatim returned invalid JSON") from error


class _NominatimAddress(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    country_code: str = Field(min_length=1)


class _NominatimItem(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    lat: str = Field(min_length=1)
    lon: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=1_024)
    name: str | None = Field(default=None, max_length=255)
    addresstype: str | None = Field(default=None, max_length=64)
    type: str | None = Field(default=None, max_length=64)
    osm_type: str = Field(min_length=1, max_length=32)
    osm_id: int | str
    address: _NominatimAddress


class _TTLResultCache:
    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        if ttl_seconds <= 0 or ttl_seconds > MAX_CACHE_TTL_SECONDS:
            raise ValueError(
                f"cache_ttl_seconds must be greater than zero and at most {MAX_CACHE_TTL_SECONDS:g}"
            )
        if max_entries < 1 or max_entries > MAX_CACHE_ENTRIES:
            raise ValueError(f"cache_max_entries must be between 1 and {MAX_CACHE_ENTRIES}")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, GeocodingResult]] = OrderedDict()

    def get(self, key: str, *, now: float) -> GeocodingResult | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        expires_at, result = cached
        if now >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return result.model_copy(update={"cache_hit": True}, deep=True)

    def put(self, key: str, result: GeocodingResult, *, now: float) -> None:
        expired = [
            entry_key for entry_key, (expires_at, _) in self._entries.items() if now >= expires_at
        ]
        for entry_key in expired:
            del self._entries[entry_key]
        self._entries[key] = (now + self._ttl_seconds, result)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class NominatimGeocoder:
    """US-only Nominatim adapter with deterministic normalization and bounded local caching."""

    def __init__(
        self,
        transport: GeocodingTransport | None = None,
        *,
        user_agent: str,
        search_url: str = NOMINATIM_SEARCH_URL,
        timeout_seconds: float = 3.0,
        cache_ttl_seconds: float = 3_600.0,
        cache_max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized_user_agent = " ".join(user_agent.split())
        if (
            len(normalized_user_agent) < 8
            or "/" not in normalized_user_agent
            or normalized_user_agent.casefold().startswith(("python", "curl", "wget"))
        ):
            raise ValueError("user_agent must identify the calling application and version")
        if "\r" in user_agent or "\n" in user_agent:
            raise ValueError("user_agent must not contain line breaks")
        if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout_seconds must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
            )
        self._transport = transport or UrllibJsonTransport()
        self._user_agent = normalized_user_agent
        self._search_url = _validate_search_url(search_url)
        self._timeout_seconds = timeout_seconds
        self._cache = _TTLResultCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_request_started_at: float | None = None

    def geocode(self, request: GeocodingRequest) -> GeocodingResult:
        cache_key = normalized_geocoding_cache_key(request)
        with self._lock:
            now = self._clock()
            cached = self._cache.get(cache_key, now=now)
            if cached is not None:
                return cached
            self._wait_for_rate_limit(now)
            self._last_request_started_at = self._clock()
            try:
                payload = self._transport.get_json(
                    self._search_url,
                    params=_request_params(request),
                    headers={
                        "Accept": "application/json",
                        "Accept-Language": request.language,
                        "User-Agent": self._user_agent,
                    },
                    timeout_seconds=self._timeout_seconds,
                )
            except GeocodingError:
                raise
            except Exception as error:
                raise GeocodingProviderError("Nominatim request failed") from error
            result = _normalize_response(
                payload,
                retrieved_at=self._wall_clock(),
                result_limit=request.limit,
                source_uri=self._search_url,
            )
            self._cache.put(cache_key, result, now=self._clock())
            return result

    def _wait_for_rate_limit(self, now: float) -> None:
        if self._last_request_started_at is None:
            return
        elapsed = now - self._last_request_started_at
        wait_seconds = MIN_REQUEST_INTERVAL_SECONDS - elapsed
        if wait_seconds > 0:
            self._sleep(wait_seconds)


def _request_params(request: GeocodingRequest) -> dict[str, str]:
    params = {
        "addressdetails": "1",
        "countrycodes": "us",
        "format": "jsonv2",
        "limit": str(min(request.limit, MAX_RESULT_LIMIT)),
        "q": request.query,
    }
    if request.viewbox is not None:
        bounds = request.viewbox
        params["viewbox"] = f"{bounds.west},{bounds.north},{bounds.east},{bounds.south}"
        if request.bounded:
            params["bounded"] = "1"
    return params


def _validate_search_url(value: str) -> str:
    if not value or len(value) > 2_048:
        raise ValueError("search_url must be a non-empty URL of at most 2048 characters")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("search_url must not contain whitespace or control characters")
    if "\\" in value:
        raise ValueError("search_url must not contain backslashes")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("search_url scheme must be http or https")
    if not parsed.netloc or parsed.hostname is None:
        raise ValueError("search_url must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("search_url must not include user information")
    if parsed.query or parsed.fragment:
        raise ValueError("search_url must not include a query or fragment")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("search_url contains an invalid port") from error
    return value


def _normalize_response(
    payload: object,
    *,
    retrieved_at: datetime,
    result_limit: int,
    source_uri: str,
) -> GeocodingResult:
    try:
        aware_retrieved_at = require_aware_wall_clock(retrieved_at).astimezone(UTC)
    except ValueError as error:
        raise GeocodingResponseError("geocoding wall clock returned an invalid datetime") from error
    if not isinstance(payload, list):
        raise GeocodingResponseError("Nominatim response must be a JSON list")
    if len(payload) > result_limit:
        raise GeocodingResponseError("Nominatim returned more matches than requested")
    matches_by_id: OrderedDict[str, GeocodingMatch] = OrderedDict()
    for raw_item in payload:
        try:
            item = _NominatimItem.model_validate(raw_item)
            if item.address.country_code.casefold() != "us":
                raise ValueError("Nominatim returned a result outside the United States")
            location = GeoPoint(latitude=float(item.lat), longitude=float(item.lon))
            name = " ".join((item.name or item.display_name.split(",", 1)[0]).split())
            formatted_address = " ".join(item.display_name.split())
            destination_type = item.addresstype or item.type
            if destination_type is not None:
                destination_type = " ".join(destination_type.split()).casefold() or None
            source_reference = f"nominatim:{item.osm_type.casefold()}:{item.osm_id}"
            match_id = stable_geocoding_match_id(
                name=name,
                formatted_address=formatted_address,
                location=location,
                destination_type=destination_type,
            )
            match = GeocodingMatch(
                match_id=match_id,
                name=name,
                formatted_address=formatted_address,
                location=location,
                destination_type=destination_type,
                source_reference=source_reference,
            )
        except (TypeError, ValueError) as error:
            raise GeocodingResponseError("Nominatim returned an invalid match") from error
        matches_by_id.setdefault(match.match_id, match)
    matches = tuple(matches_by_id.values())
    metadata = GeocodingProviderMetadata(
        provider_name="Nominatim",
        provider_version=NOMINATIM_PROVIDER_VERSION,
        attribution=NOMINATIM_ATTRIBUTION,
        license=NOMINATIM_LICENSE,
        source_uri=source_uri,
        raw_storage_policy=EvidenceStoragePolicy.EPHEMERAL,
        normalized_storage_policy=EvidenceStoragePolicy.PERSIST,
        retrieved_at=aware_retrieved_at,
    )
    return GeocodingResult(
        status=geocoding_status(matches),
        matches=matches,
        metadata=metadata,
    )
