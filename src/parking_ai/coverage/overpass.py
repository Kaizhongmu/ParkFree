from __future__ import annotations

import hashlib
import json
import math
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

import certifi
from pydantic import ValidationError

from parking_ai.coverage.models import (
    CoverageProviderMetadata,
    RoadAcquisition,
    RoadCoverageProviderError,
    RoadCoverageResponseError,
)
from parking_ai.domain import (
    Destination,
    EvidenceStoragePolicy,
    LineStringGeometry,
    PhysicalState,
    SegmentSide,
)
from parking_ai.gis.generator import DEFAULT_ELIGIBLE_ROAD_TYPES, DEFAULT_WALKING_SPEED_M_PER_MIN
from parking_ai.gis.models import RoadFeature, SideReferenceDirection

DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_PROVIDER_VERSION = "overpass-osm-json-v1"
OVERPASS_POLICY_VERSION = "road-coverage-v1"
OVERPASS_ATTRIBUTION = "© OpenStreetMap contributors"
OVERPASS_LICENSE = "Open Data Commons Open Database License (ODbL) 1.0"

MAX_TIMEOUT_SECONDS = 30.0
MAX_RADIUS_M = 5_000.0
MAX_RESPONSE_BYTES = 8_000_000
MAX_ELEMENTS = 75_000
MAX_ROADS = 5_000
MAX_NODES_PER_ROAD = 4_000
MAX_TAGS_PER_ROAD = 256
MAX_CACHE_TTL_SECONDS = 86_400.0
MAX_CACHE_ENTRIES = 512
MAX_RATE_INTERVAL_SECONDS = 60.0
MIN_USER_AGENT_LENGTH = 8

OSM_ELEMENT_ID_TAG = "_provider.osm_element_id"
OSM_ELEMENT_VERSION_TAG = "_provider.osm_element_version"
OSM_ELEMENT_TIMESTAMP_TAG = "_provider.osm_element_timestamp"

_ROAD_ACCESS_BLOCKERS = frozenset({"no", "private"})
_SIDE_POSITION_BLOCKERS = frozenset({"no", "no_parking", "no_standing", "no_stopping", "separate"})
_SIDE_RESTRICTION_BLOCKERS = frozenset(
    {"charging_only", "loading_only", "no_parking", "no_standing", "no_stopping"}
)


class OverpassTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        form: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object: ...


class UrllibOverpassTransport:
    """Bounded standard-library HTTP transport; tests inject a fake transport."""

    def __init__(self, *, max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        if max_response_bytes < 1 or max_response_bytes > MAX_RESPONSE_BYTES:
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        self._max_response_bytes = max_response_bytes
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    def post_json(
        self,
        url: str,
        *,
        form: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        body = urllib.parse.urlencode(sorted(form.items())).encode("ascii")
        request_headers = dict(headers)
        request_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
                context=self._ssl_context,
            ) as response:
                raw_body = response.read(self._max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            raise RoadCoverageProviderError(
                f"Overpass returned HTTP status {error.code}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RoadCoverageProviderError("Overpass request failed") from error
        if len(raw_body) > self._max_response_bytes:
            raise RoadCoverageProviderError("Overpass response exceeded the configured size limit")
        try:
            return json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RoadCoverageResponseError("Overpass returned invalid JSON") from error


class _TTLAcquisitionCache:
    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        if ttl_seconds <= 0 or ttl_seconds > MAX_CACHE_TTL_SECONDS:
            raise ValueError(
                f"cache_ttl_seconds must be greater than zero and at most {MAX_CACHE_TTL_SECONDS:g}"
            )
        if max_entries < 1 or max_entries > MAX_CACHE_ENTRIES:
            raise ValueError(f"cache_max_entries must be between 1 and {MAX_CACHE_ENTRIES}")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, RoadAcquisition]] = OrderedDict()

    def get(self, key: str, *, now: float) -> RoadAcquisition | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        expires_at, acquisition = cached
        if now >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return acquisition.model_copy(update={"cache_hit": True}, deep=True)

    def put(self, key: str, acquisition: RoadAcquisition, *, now: float) -> None:
        expired = [
            entry_key for entry_key, (expires_at, _) in self._entries.items() if now >= expires_at
        ]
        for entry_key in expired:
            del self._entries[entry_key]
        self._entries[key] = (now + self._ttl_seconds, acquisition)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class OverpassRoadCoverageProvider:
    """Acquire a bounded OSM road snapshot without leaking provider formats downstream."""

    provider_name = "Overpass API / OpenStreetMap"

    def __init__(
        self,
        transport: OverpassTransport | None = None,
        *,
        user_agent: str,
        endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
        timeout_seconds: float = 15.0,
        rate_interval_seconds: float = 1.0,
        cache_ttl_seconds: float = 3_600.0,
        cache_max_entries: int = 128,
        max_radius_m: float = 2_400.0,
        max_elements: int = 30_000,
        max_roads: int = 2_000,
        eligible_road_types: frozenset[str] = DEFAULT_ELIGIBLE_ROAD_TYPES,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized_user_agent = " ".join(user_agent.split())
        if (
            len(normalized_user_agent) < MIN_USER_AGENT_LENGTH
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
        if rate_interval_seconds < 0 or rate_interval_seconds > MAX_RATE_INTERVAL_SECONDS:
            raise ValueError(
                "rate_interval_seconds must be non-negative and at most "
                f"{MAX_RATE_INTERVAL_SECONDS:g}"
            )
        if max_radius_m <= 0 or max_radius_m > MAX_RADIUS_M:
            raise ValueError(f"max_radius_m must be greater than zero and at most {MAX_RADIUS_M:g}")
        if max_elements < 1 or max_elements > MAX_ELEMENTS:
            raise ValueError(f"max_elements must be between 1 and {MAX_ELEMENTS}")
        if max_roads < 1 or max_roads > MAX_ROADS:
            raise ValueError(f"max_roads must be between 1 and {MAX_ROADS}")
        normalized_types = frozenset(_normalize_road_type(value) for value in eligible_road_types)
        if not normalized_types:
            raise ValueError("eligible_road_types must not be empty")

        self._transport = transport or UrllibOverpassTransport()
        self._user_agent = normalized_user_agent
        self._endpoint = _validate_endpoint(endpoint)
        self._timeout_seconds = timeout_seconds
        self._rate_interval_seconds = rate_interval_seconds
        self._max_radius_m = max_radius_m
        self._max_elements = max_elements
        self._max_roads = max_roads
        self._eligible_road_types = normalized_types
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._cache = _TTLAcquisitionCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )
        self._lock = threading.Lock()
        self._last_request_started_at: float | None = None

    def acquire(self, destination: Destination, max_walk_minutes: float) -> RoadAcquisition:
        if not math.isfinite(max_walk_minutes) or max_walk_minutes <= 0:
            raise ValueError("max_walk_minutes must be a positive finite number")
        center = destination.location
        _validate_us_coordinate(center.longitude, center.latitude, label="destination")
        requested_radius_m = max_walk_minutes * DEFAULT_WALKING_SPEED_M_PER_MIN
        radius_m = min(requested_radius_m, self._max_radius_m)
        cache_key = _cache_key(center.longitude, center.latitude, radius_m)

        with self._lock:
            now = self._clock()
            cached = self._cache.get(cache_key, now=now)
            if cached is not None:
                return cached
            self._wait_for_rate_limit(now)
            self._last_request_started_at = self._clock()
            query = _build_query(
                longitude=center.longitude,
                latitude=center.latitude,
                radius_m=radius_m,
                timeout_seconds=self._timeout_seconds,
                eligible_road_types=self._eligible_road_types,
            )
            try:
                payload = self._transport.post_json(
                    self._endpoint,
                    form={"data": query},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                        "User-Agent": self._user_agent,
                    },
                    timeout_seconds=self._timeout_seconds,
                )
            except (RoadCoverageProviderError, RoadCoverageResponseError):
                raise
            except Exception as error:
                raise RoadCoverageProviderError("Overpass request failed") from error
            acquisition = _normalize_response(
                payload,
                endpoint=self._endpoint,
                retrieved_at=self._wall_clock(),
                center_longitude=center.longitude,
                center_latitude=center.latitude,
                radius_m=radius_m,
                eligible_road_types=self._eligible_road_types,
                max_elements=self._max_elements,
                max_roads=self._max_roads,
            )
            self._cache.put(cache_key, acquisition, now=self._clock())
            return acquisition

    def _wait_for_rate_limit(self, now: float) -> None:
        if self._last_request_started_at is None:
            return
        wait_seconds = self._rate_interval_seconds - (now - self._last_request_started_at)
        if wait_seconds > 0:
            self._sleep(wait_seconds)


def _normalize_road_type(value: str) -> str:
    normalized = " ".join(value.split()).casefold()
    if not re.fullmatch(r"[a-z0-9_]{1,64}", normalized):
        raise ValueError(
            "eligible road types must use 1 to 64 lowercase letters, digits, or underscores"
        )
    return normalized


def _validate_endpoint(value: str) -> str:
    if not value or len(value) > 2_048:
        raise ValueError("endpoint must be a non-empty URL of at most 2048 characters")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("endpoint must not contain whitespace or control characters")
    if "\\" in value:
        raise ValueError("endpoint must not contain backslashes")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("endpoint scheme must be http or https")
    if not parsed.netloc or parsed.hostname is None:
        raise ValueError("endpoint must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not include user information")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint must not include a query or fragment")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("endpoint contains an invalid port") from error
    return value


def _validate_us_coordinate(longitude: float, latitude: float, *, label: str) -> None:
    _validate_global_coordinate(longitude, latitude, label=label)
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise RoadCoverageResponseError(f"{label} coordinate must be finite")
    longitude_in_us = -180.0 <= longitude <= -66.0 or 172.0 <= longitude <= 180.0
    if not (18.0 <= latitude <= 72.0 and longitude_in_us):
        raise RoadCoverageResponseError(f"{label} coordinate is outside supported US bounds")


def _validate_global_coordinate(longitude: float, latitude: float, *, label: str) -> None:
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise RoadCoverageResponseError(f"{label} coordinate must be finite")
    if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
        raise RoadCoverageResponseError(f"{label} coordinate is outside geographic bounds")


def _cache_key(longitude: float, latitude: float, radius_m: float) -> str:
    payload = {
        "latitude": round(latitude, 5),
        "longitude": round(longitude, 5),
        "policy_version": OVERPASS_POLICY_VERSION,
        "radius_m": round(radius_m, 1),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _build_query(
    *,
    longitude: float,
    latitude: float,
    radius_m: float,
    timeout_seconds: float,
    eligible_road_types: frozenset[str],
) -> str:
    overpass_timeout = max(1, math.ceil(timeout_seconds))
    road_queries = "".join(
        f"way(around:{math.ceil(radius_m)},{latitude:.7f},{longitude:.7f})"
        f'["highway"="{road_type}"];'
        for road_type in sorted(eligible_road_types)
    )
    return f"[out:json][timeout:{overpass_timeout}];({road_queries});(._;>;);out meta qt;"


def _require_int(value: object, *, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RoadCoverageResponseError(f"{label} must be an integer of at least {minimum}")
    return value


def _require_tags(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RoadCoverageResponseError("Overpass way tags must be an object")
    if len(value) > MAX_TAGS_PER_ROAD:
        raise RoadCoverageResponseError("Overpass way exceeded the tag limit")
    tags: dict[str, str] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str) or not isinstance(raw_value, str):
            raise RoadCoverageResponseError("Overpass tag names and values must be strings")
        if not key or len(key) > 255 or len(raw_value) > 2_048:
            raise RoadCoverageResponseError("Overpass tag name or value exceeds its bound")
        tags[key] = raw_value
    return tags


def _require_timestamp(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise RoadCoverageResponseError("Overpass way timestamp must be a bounded string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RoadCoverageResponseError("Overpass way timestamp is invalid") from error
    if parsed.utcoffset() is None:
        raise RoadCoverageResponseError("Overpass way timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parking_candidate_sides(
    tags: Mapping[str, str],
) -> tuple[tuple[SegmentSide, ...], bool]:
    """Apply only explicit, unconditional OSM exclusions; absence remains unknown."""

    if any(
        tags.get(key, "").strip().casefold() in _ROAD_ACCESS_BLOCKERS
        for key in ("access", "vehicle", "motor_vehicle", "motorcar")
    ):
        return (SegmentSide.LEFT, SegmentSide.RIGHT), False

    candidate_sides: list[SegmentSide] = []
    for side, side_name in (
        (SegmentSide.LEFT, "left"),
        (SegmentSide.RIGHT, "right"),
    ):
        position = tags.get(f"parking:{side_name}", tags.get("parking:both", ""))
        if not position:
            position = tags.get(
                f"parking:lane:{side_name}",
                tags.get("parking:lane:both", ""),
            )
        restriction = tags.get(
            f"parking:{side_name}:restriction",
            tags.get("parking:both:restriction", ""),
        )
        if not restriction:
            restriction = tags.get(
                f"parking:condition:{side_name}",
                tags.get("parking:condition:both", ""),
            )
        access = tags.get(
            f"parking:{side_name}:access",
            tags.get("parking:both:access", ""),
        )
        if position.strip().casefold() in _SIDE_POSITION_BLOCKERS:
            continue
        if restriction.strip().casefold() in _SIDE_RESTRICTION_BLOCKERS:
            continue
        if access.strip().casefold() in _ROAD_ACCESS_BLOCKERS:
            continue
        candidate_sides.append(side)

    if not candidate_sides:
        return (SegmentSide.LEFT, SegmentSide.RIGHT), False
    return tuple(candidate_sides), True


def _wrapped_longitude_delta(start: float, end: float) -> float:
    return (end - start + 180.0) % 360.0 - 180.0


def _interpolate_coordinate(
    start: tuple[float, float],
    end: tuple[float, float],
    ratio: float,
) -> tuple[float, float]:
    longitude = start[0] + _wrapped_longitude_delta(start[0], end[0]) * ratio
    if longitude > 180.0:
        longitude -= 360.0
    elif longitude < -180.0:
        longitude += 360.0
    return longitude, start[1] + (end[1] - start[1]) * ratio


def _project_from_center_m(
    coordinate: tuple[float, float],
    *,
    center_longitude: float,
    center_latitude: float,
) -> tuple[float, float]:
    reference_latitude = math.radians(center_latitude)
    return (
        math.radians(_wrapped_longitude_delta(center_longitude, coordinate[0]))
        * 6_371_008.8
        * math.cos(reference_latitude),
        math.radians(coordinate[1] - center_latitude) * 6_371_008.8,
    )


def _clip_segment_to_radius(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    center_longitude: float,
    center_latitude: float,
    radius_m: float,
) -> tuple[float, float] | None:
    start_x, start_y = _project_from_center_m(
        start,
        center_longitude=center_longitude,
        center_latitude=center_latitude,
    )
    end_x, end_y = _project_from_center_m(
        end,
        center_longitude=center_longitude,
        center_latitude=center_latitude,
    )
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    a = delta_x * delta_x + delta_y * delta_y
    if a == 0:
        return None
    b = 2.0 * (start_x * delta_x + start_y * delta_y)
    c = start_x * start_x + start_y * start_y - radius_m * radius_m
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0:
        return None
    root = math.sqrt(max(0.0, discriminant))
    first = (-b - root) / (2.0 * a)
    second = (-b + root) / (2.0 * a)
    enter = max(0.0, min(first, second))
    exit_ = min(1.0, max(first, second))
    if exit_ - enter <= 1e-12:
        return None
    return enter, exit_


def _clip_way_to_radius(
    node_ids: tuple[int, ...],
    coordinates: list[tuple[float, float]],
    *,
    center_longitude: float,
    center_latitude: float,
    radius_m: float,
) -> list[tuple[tuple[tuple[float, float], ...], tuple[int | None, ...]]]:
    parts: list[tuple[tuple[tuple[float, float], ...], tuple[int | None, ...]]] = []
    current_coordinates: list[tuple[float, float]] = []
    current_node_ids: list[int | None] = []

    def finish_current() -> None:
        nonlocal current_coordinates, current_node_ids
        if len(current_coordinates) >= 2 and len(set(current_coordinates)) >= 2:
            parts.append((tuple(current_coordinates), tuple(current_node_ids)))
        current_coordinates = []
        current_node_ids = []

    for index in range(len(coordinates) - 1):
        start = coordinates[index]
        end = coordinates[index + 1]
        clipped = _clip_segment_to_radius(
            start,
            end,
            center_longitude=center_longitude,
            center_latitude=center_latitude,
            radius_m=radius_m,
        )
        if clipped is None:
            finish_current()
            continue
        enter, exit_ = clipped
        clipped_start = start if enter <= 1e-12 else _interpolate_coordinate(start, end, enter)
        clipped_end = end if exit_ >= 1.0 - 1e-12 else _interpolate_coordinate(start, end, exit_)
        start_node_id = node_ids[index] if enter <= 1e-12 else None
        end_node_id = node_ids[index + 1] if exit_ >= 1.0 - 1e-12 else None
        if current_coordinates and current_coordinates[-1] == clipped_start:
            if current_coordinates[-1] != clipped_end:
                current_coordinates.append(clipped_end)
                current_node_ids.append(end_node_id)
        else:
            finish_current()
            current_coordinates = [clipped_start, clipped_end]
            current_node_ids = [start_node_id, end_node_id]
        if exit_ < 1.0 - 1e-12:
            finish_current()
    finish_current()
    return parts


def _normalize_response(
    payload: object,
    *,
    endpoint: str,
    retrieved_at: datetime,
    center_longitude: float,
    center_latitude: float,
    radius_m: float,
    eligible_road_types: frozenset[str],
    max_elements: int,
    max_roads: int,
) -> RoadAcquisition:
    if retrieved_at.utcoffset() is None:
        raise RoadCoverageResponseError("coverage wall clock must return an aware datetime")
    if not isinstance(payload, dict):
        raise RoadCoverageResponseError("Overpass response must be a JSON object")
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise RoadCoverageResponseError("Overpass response must contain an elements list")
    if len(elements) > max_elements:
        raise RoadCoverageResponseError("Overpass response exceeded the element limit")

    nodes: dict[int, tuple[float, float]] = {}
    raw_ways: dict[int, tuple[tuple[int, ...], dict[str, str], int, str]] = {}
    for element in elements:
        if not isinstance(element, dict):
            raise RoadCoverageResponseError("Overpass elements must be JSON objects")
        element_type = element.get("type")
        if element_type == "node":
            node_id = _require_int(element.get("id"), label="Overpass node id")
            longitude = element.get("lon")
            latitude = element.get("lat")
            if (
                isinstance(longitude, bool)
                or not isinstance(longitude, (int, float))
                or isinstance(latitude, bool)
                or not isinstance(latitude, (int, float))
            ):
                raise RoadCoverageResponseError("Overpass node coordinates must be numeric")
            coordinate = (float(longitude), float(latitude))
            # The selected destination is constrained to the supported US area, but an OSM way
            # near a national border can legitimately include a node just across that border.
            _validate_global_coordinate(*coordinate, label="Overpass node")
            existing = nodes.get(node_id)
            if existing is not None and existing != coordinate:
                raise RoadCoverageResponseError("Overpass returned conflicting duplicate nodes")
            nodes[node_id] = coordinate
        elif element_type == "way":
            way_id = _require_int(element.get("id"), label="Overpass way id")
            tags = _require_tags(element.get("tags"))
            road_type_raw = tags.get("highway")
            if road_type_raw is None:
                continue
            road_type = " ".join(road_type_raw.split()).casefold()
            if road_type not in eligible_road_types:
                continue
            raw_node_ids = element.get("nodes")
            if not isinstance(raw_node_ids, list):
                raise RoadCoverageResponseError("Overpass way nodes must be a list")
            if len(raw_node_ids) < 2 or len(raw_node_ids) > MAX_NODES_PER_ROAD:
                raise RoadCoverageResponseError("Overpass way node count is outside its bound")
            node_ids = tuple(
                _require_int(node_id, label="Overpass way node id") for node_id in raw_node_ids
            )
            version = _require_int(element.get("version"), label="Overpass way version")
            timestamp = _require_timestamp(element.get("timestamp"))
            candidate = (node_ids, tags, version, timestamp)
            existing_way = raw_ways.get(way_id)
            if existing_way is not None and existing_way != candidate:
                raise RoadCoverageResponseError("Overpass returned conflicting duplicate ways")
            raw_ways[way_id] = candidate
        else:
            raise RoadCoverageResponseError("Overpass returned an unsupported element type")

    if len(raw_ways) > max_roads:
        raise RoadCoverageResponseError("Overpass response exceeded the road limit")

    node_reference_counts: Counter[int] = Counter()
    for node_ids, _, _, _ in raw_ways.values():
        node_reference_counts.update(set(node_ids))

    roads: list[RoadFeature] = []
    tags_by_feature_id: dict[str, dict[str, str]] = {}
    for way_id in sorted(raw_ways):
        node_ids, tags, version, timestamp = raw_ways[way_id]
        try:
            coordinates = [nodes[node_id] for node_id in node_ids]
        except KeyError as error:
            raise RoadCoverageResponseError(
                f"Overpass way references missing node {error.args[0]}"
            ) from error
        if len(set(coordinates)) < 2:
            raise RoadCoverageResponseError("Overpass road geometry must contain distinct points")
        road_type = " ".join(tags["highway"].split()).casefold()
        candidate_sides, parking_candidate = _parking_candidate_sides(tags)
        clipped_parts = _clip_way_to_radius(
            node_ids,
            coordinates,
            center_longitude=center_longitude,
            center_latitude=center_latitude,
            radius_m=radius_m,
        )
        for part_index, (part_coordinates, part_node_ids) in enumerate(clipped_parts, start=1):
            feature_id = f"osm_way_{way_id}"
            if len(clipped_parts) > 1:
                feature_id = f"{feature_id}_part_{part_index}"
            break_indexes = frozenset(
                index
                for index, node_id in enumerate(part_node_ids)
                if index in {0, len(part_node_ids) - 1}
                or (node_id is not None and node_reference_counts[node_id] > 1)
            )
            try:
                road = RoadFeature(
                    feature_id=feature_id,
                    geometry=LineStringGeometry(coordinates=part_coordinates),
                    street_name=tags.get("name"),
                    road_type=road_type,
                    break_indexes=break_indexes,
                    candidate_sides=candidate_sides,
                    side_reference_direction=SideReferenceDirection.SOURCE_GEOMETRY,
                    parking_candidate=parking_candidate,
                    physical_state=PhysicalState.UNKNOWN,
                )
            except ValidationError as error:
                raise RoadCoverageResponseError(
                    "Overpass returned an invalid road feature"
                ) from error
            roads.append(road)
            if len(roads) > max_roads:
                raise RoadCoverageResponseError("Overpass response exceeded the clipped-road limit")
            provenance_tags = dict(sorted(tags.items()))
            provenance_tags[OSM_ELEMENT_ID_TAG] = str(way_id)
            provenance_tags[OSM_ELEMENT_VERSION_TAG] = str(version)
            provenance_tags[OSM_ELEMENT_TIMESTAMP_TAG] = timestamp
            tags_by_feature_id[feature_id] = provenance_tags

    try:
        metadata = CoverageProviderMetadata(
            provider_name="Overpass API / OpenStreetMap",
            provider_version=OVERPASS_PROVIDER_VERSION,
            attribution=OVERPASS_ATTRIBUTION,
            license=OVERPASS_LICENSE,
            source_uri=endpoint,
            retrieved_at=retrieved_at.astimezone(UTC),
            raw_storage_policy=EvidenceStoragePolicy.EPHEMERAL,
            normalized_storage_policy=EvidenceStoragePolicy.PERSIST,
        )
        return RoadAcquisition(
            roads=tuple(roads),
            tags_by_feature_id=tags_by_feature_id,
            metadata=metadata,
        )
    except ValidationError as error:
        raise RoadCoverageResponseError("Overpass response normalization failed") from error
