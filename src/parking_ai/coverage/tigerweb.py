"""Zero-key U.S. Census TIGERweb road-coverage fallback."""

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
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
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
from parking_ai.coverage.overpass import _clip_way_to_radius
from parking_ai.domain import (
    Destination,
    EvidenceStoragePolicy,
    LineStringGeometry,
    PhysicalState,
    SegmentSide,
)
from parking_ai.gis.generator import DEFAULT_WALKING_SPEED_M_PER_MIN
from parking_ai.gis.models import RoadFeature, SideReferenceDirection

DEFAULT_TIGERWEB_ENDPOINT = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/Transportation_LargeScale/MapServer"
)
TIGERWEB_PROVIDER_VERSION = "tigerweb-transportation-large-scale-geojson-v1"
TIGERWEB_ATTRIBUTION = "Source: U.S. Census Bureau"
TIGERWEB_LICENSE = "U.S. Census Bureau data (public domain)"

MAX_TIMEOUT_SECONDS = 30.0
MAX_RADIUS_M = 2_400.0
MAX_RESPONSE_BYTES = 8_000_000
MAX_FEATURES = 6_000
MAX_COORDINATES_PER_FEATURE = 8_000
MAX_CACHE_TTL_SECONDS = 86_400.0
MAX_CACHE_ENTRIES = 256
METRES_PER_LATITUDE_DEGREE = 111_320.0
_LAYERS = ((0, "primary"), (1, "secondary"), (2, "residential"))


class TIGERwebTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object: ...


class UrllibTIGERwebTransport:
    """Size-limited standard-library JSON transport; tests inject a fake."""

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
            raise RoadCoverageProviderError(
                f"TIGERweb returned HTTP status {error.code}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RoadCoverageProviderError("TIGERweb request failed") from error
        if len(body) > self._max_response_bytes:
            raise RoadCoverageProviderError("TIGERweb response exceeded the configured size limit")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RoadCoverageResponseError("TIGERweb returned invalid JSON") from error


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
        self._entries[key] = (now + self._ttl_seconds, acquisition)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class TIGERwebRoadCoverageProvider:
    """Fetch bounded nationwide road centerlines from the official Census service."""

    provider_name = "U.S. Census Bureau TIGERweb"

    def __init__(
        self,
        transport: TIGERwebTransport | None = None,
        *,
        endpoint: str = DEFAULT_TIGERWEB_ENDPOINT,
        timeout_seconds: float = 8.0,
        cache_ttl_seconds: float = 3_600.0,
        cache_max_entries: int = 64,
        max_radius_m: float = 1_200.0,
        max_features: int = 2_000,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout_seconds must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
            )
        if max_radius_m <= 0 or max_radius_m > MAX_RADIUS_M:
            raise ValueError(f"max_radius_m must be greater than zero and at most {MAX_RADIUS_M:g}")
        if max_features < 1 or max_features > MAX_FEATURES:
            raise ValueError(f"max_features must be between 1 and {MAX_FEATURES}")
        self._transport = transport or UrllibTIGERwebTransport()
        self._endpoint = _validate_endpoint(endpoint)
        self._timeout_seconds = timeout_seconds
        self._max_radius_m = max_radius_m
        self._max_features = max_features
        self._clock = clock
        self._wall_clock = wall_clock
        self._cache = _TTLAcquisitionCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )
        self._lock = threading.Lock()

    def acquire(self, destination: Destination, max_walk_minutes: float) -> RoadAcquisition:
        if not math.isfinite(max_walk_minutes) or max_walk_minutes <= 0:
            raise ValueError("max_walk_minutes must be a positive finite number")
        longitude = destination.location.longitude
        latitude = destination.location.latitude
        _validate_us_coordinate(longitude, latitude)
        radius_m = min(
            max_walk_minutes * DEFAULT_WALKING_SPEED_M_PER_MIN,
            self._max_radius_m,
        )
        cache_key = _cache_key(longitude, latitude, radius_m)
        with self._lock:
            cached = self._cache.get(cache_key, now=self._clock())
            if cached is not None:
                return cached
            features: list[tuple[int, str, object]] = []
            for bbox in _bounding_boxes(longitude, latitude, radius_m):
                for layer_id, road_type in _LAYERS:
                    try:
                        payload = self._transport.get_json(
                            f"{self._endpoint}/{layer_id}/query",
                            params=_query_params(bbox, max_features=self._max_features),
                            headers={"Accept": "application/geo+json, application/json"},
                            timeout_seconds=self._timeout_seconds,
                        )
                    except (RoadCoverageProviderError, RoadCoverageResponseError):
                        raise
                    except Exception as error:
                        raise RoadCoverageProviderError("TIGERweb request failed") from error
                    features.extend(
                        (layer_id, road_type, feature)
                        for feature in _bounded_features(payload, max_features=self._max_features)
                    )
                    if len(features) > self._max_features:
                        raise RoadCoverageResponseError(
                            "TIGERweb responses exceeded the combined feature limit"
                        )

            retrieved_at = self._wall_clock()
            acquisition = _normalize_features(
                features,
                endpoint=self._endpoint,
                retrieved_at=retrieved_at,
                center_longitude=longitude,
                center_latitude=latitude,
                radius_m=radius_m,
                max_features=self._max_features,
            )
            self._cache.put(cache_key, acquisition, now=self._clock())
            return acquisition


def _query_params(
    bbox: tuple[float, float, float, float],
    *,
    max_features: int,
) -> dict[str, str]:
    return {
        "f": "geojson",
        "geometry": ",".join(f"{value:.7f}" for value in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outFields": "OID,NAME,MTFCC",
        "outSR": "4326",
        "resultRecordCount": str(max_features),
        "returnGeometry": "true",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1",
    }


def _bounded_features(payload: object, *, max_features: int) -> list[object]:
    if not isinstance(payload, dict):
        raise RoadCoverageResponseError("TIGERweb response must be a GeoJSON object")
    response_properties = payload.get("properties")
    nested_limit = (
        response_properties.get("exceededTransferLimit")
        if isinstance(response_properties, dict)
        else None
    )
    if payload.get("exceededTransferLimit") is True or nested_limit is True:
        raise RoadCoverageResponseError("TIGERweb response exceeded its transfer limit")
    if payload.get("type") != "FeatureCollection":
        raise RoadCoverageResponseError("TIGERweb response must be a FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list):
        raise RoadCoverageResponseError("TIGERweb response must contain a features list")
    if len(features) > max_features:
        raise RoadCoverageResponseError("TIGERweb response exceeded the feature limit")
    return features


def _normalize_features(
    raw_features: Sequence[tuple[int, str, object]],
    *,
    endpoint: str,
    retrieved_at: datetime,
    center_longitude: float,
    center_latitude: float,
    radius_m: float,
    max_features: int,
) -> RoadAcquisition:
    if retrieved_at.utcoffset() is None:
        raise RoadCoverageResponseError("coverage wall clock must return an aware datetime")
    normalized: dict[
        tuple[int, str, int, int],
        tuple[str, str | None, str, tuple[tuple[float, float], ...]],
    ] = {}
    for layer_id, road_type, raw_feature in raw_features:
        if not isinstance(raw_feature, dict) or raw_feature.get("type") != "Feature":
            raise RoadCoverageResponseError("TIGERweb features must be GeoJSON Feature objects")
        properties = raw_feature.get("properties")
        geometry = raw_feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise RoadCoverageResponseError("TIGERweb feature properties and geometry are required")
        oid = _bounded_oid(properties.get("OID"))
        name_value = properties.get("NAME")
        if name_value is not None and not isinstance(name_value, str):
            raise RoadCoverageResponseError("TIGERweb road name must be a string or null")
        name = " ".join(name_value.split()) if name_value else None
        if name is not None and len(name) > 255:
            raise RoadCoverageResponseError("TIGERweb road name exceeded its length limit")
        mtfcc = properties.get("MTFCC")
        if not isinstance(mtfcc, str) or not mtfcc or len(mtfcc) > 32:
            raise RoadCoverageResponseError("TIGERweb MTFCC must be a bounded string")
        for part_index, coordinates in enumerate(_geometry_parts(geometry), start=1):
            synthetic_nodes = tuple(range(len(coordinates)))
            clipped = _clip_way_to_radius(
                synthetic_nodes,
                list(coordinates),
                center_longitude=center_longitude,
                center_latitude=center_latitude,
                radius_m=radius_m,
            )
            for clipped_index, (part_coordinates, _) in enumerate(clipped, start=1):
                key = (layer_id, oid, part_index, clipped_index)
                candidate = (road_type, name, mtfcc, part_coordinates)
                existing = normalized.get(key)
                if existing is not None and existing != candidate:
                    raise RoadCoverageResponseError(
                        "TIGERweb returned conflicting duplicate road features"
                    )
                normalized[key] = candidate

    roads: list[RoadFeature] = []
    tags_by_feature_id: dict[str, dict[str, str]] = {}
    for (layer_id, oid, part_index, clipped_index), (
        road_type,
        name,
        mtfcc,
        coordinates,
    ) in sorted(normalized.items()):
        feature_id = f"tiger_{layer_id}_{oid}"
        if part_index != 1 or clipped_index != 1:
            feature_id = f"{feature_id}_part_{part_index}_{clipped_index}"
        try:
            road = RoadFeature(
                feature_id=feature_id,
                geometry=LineStringGeometry(coordinates=coordinates),
                street_name=name,
                road_type=road_type,
                break_indexes=frozenset({0, len(coordinates) - 1}),
                candidate_sides=(SegmentSide.LEFT, SegmentSide.RIGHT),
                side_reference_direction=SideReferenceDirection.SOURCE_GEOMETRY,
                parking_candidate=False if layer_id == 0 else None,
                physical_state=PhysicalState.UNKNOWN,
            )
        except ValidationError as error:
            raise RoadCoverageResponseError("TIGERweb returned invalid road geometry") from error
        roads.append(road)
        if len(roads) > max_features:
            raise RoadCoverageResponseError("TIGERweb clipped roads exceeded the feature limit")
        tags_by_feature_id[feature_id] = {
            "_provider.tiger_layer": str(layer_id),
            "_provider.tiger_oid": str(oid),
            "_provider.tiger_mtfcc": mtfcc,
        }

    try:
        return RoadAcquisition(
            roads=tuple(roads),
            tags_by_feature_id=tags_by_feature_id,
            metadata=CoverageProviderMetadata(
                provider_name="U.S. Census Bureau TIGERweb",
                provider_version=TIGERWEB_PROVIDER_VERSION,
                attribution=TIGERWEB_ATTRIBUTION,
                license=TIGERWEB_LICENSE,
                source_uri=endpoint,
                raw_storage_policy=EvidenceStoragePolicy.EPHEMERAL,
                normalized_storage_policy=EvidenceStoragePolicy.PERSIST,
                retrieved_at=retrieved_at.astimezone(UTC),
            ),
        )
    except ValidationError as error:
        raise RoadCoverageResponseError("TIGERweb response normalization failed") from error


def _geometry_parts(
    geometry: Mapping[object, object],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    geometry_type = geometry.get("type")
    raw_coordinates = geometry.get("coordinates")
    if geometry_type == "LineString":
        raw_parts = [raw_coordinates]
    elif geometry_type == "MultiLineString":
        if not isinstance(raw_coordinates, list):
            raise RoadCoverageResponseError("TIGERweb MultiLineString coordinates must be a list")
        raw_parts = raw_coordinates
    else:
        raise RoadCoverageResponseError("TIGERweb road geometry must be a line")
    parts: list[tuple[tuple[float, float], ...]] = []
    coordinate_count = 0
    for raw_part in raw_parts:
        if not isinstance(raw_part, list) or len(raw_part) < 2:
            raise RoadCoverageResponseError("TIGERweb line must contain at least two coordinates")
        part: list[tuple[float, float]] = []
        for raw_coordinate in raw_part:
            if not isinstance(raw_coordinate, list) or len(raw_coordinate) != 2:
                raise RoadCoverageResponseError(
                    "TIGERweb coordinate must be a longitude/latitude pair"
                )
            longitude, latitude = raw_coordinate
            if (
                isinstance(longitude, bool)
                or not isinstance(longitude, (int, float))
                or isinstance(latitude, bool)
                or not isinstance(latitude, (int, float))
            ):
                raise RoadCoverageResponseError("TIGERweb coordinates must be numeric")
            coordinate = (float(longitude), float(latitude))
            if not all(math.isfinite(value) for value in coordinate) or not (
                -180 <= coordinate[0] <= 180 and -90 <= coordinate[1] <= 90
            ):
                raise RoadCoverageResponseError("TIGERweb coordinate is outside geographic bounds")
            part.append(coordinate)
        coordinate_count += len(part)
        if coordinate_count > MAX_COORDINATES_PER_FEATURE:
            raise RoadCoverageResponseError("TIGERweb feature exceeded the coordinate limit")
        if len(set(part)) < 2:
            raise RoadCoverageResponseError("TIGERweb line must contain distinct coordinates")
        parts.append(tuple(part))
    return tuple(parts)


def _bounded_oid(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,22}", value) or value == "0":
        raise RoadCoverageResponseError("TIGERweb OID must be a positive numeric identifier")
    return value


def _bounding_boxes(
    longitude: float,
    latitude: float,
    radius_m: float,
) -> tuple[tuple[float, float, float, float], ...]:
    latitude_delta = radius_m / METRES_PER_LATITUDE_DEGREE
    longitude_delta = radius_m / (
        METRES_PER_LATITUDE_DEGREE * max(math.cos(math.radians(latitude)), 0.01)
    )
    south, north = latitude - latitude_delta, latitude + latitude_delta
    west, east = longitude - longitude_delta, longitude + longitude_delta
    if west >= -180 and east <= 180:
        return ((west, south, east, north),)
    if west < -180:
        return ((-180, south, east, north), (west + 360, south, 180, north))
    return ((west, south, 180, north), (-180, south, east - 360, north))


def _validate_us_coordinate(longitude: float, latitude: float) -> None:
    longitude_in_us = -180 <= longitude <= -66 or 172 <= longitude <= 180
    if not (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and 18 <= latitude <= 72
        and longitude_in_us
    ):
        raise RoadCoverageResponseError("destination coordinate is outside supported US bounds")


def _validate_endpoint(value: str) -> str:
    if not value or len(value) > 2_048:
        raise ValueError("endpoint must be a non-empty URL of at most 2048 characters")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("endpoint must not contain whitespace or control characters")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not include user information")
    if parsed.query or parsed.fragment or "\\" in value:
        raise ValueError("endpoint must not include query, fragment, or backslashes")
    return value.rstrip("/")


def _cache_key(longitude: float, latitude: float, radius_m: float) -> str:
    payload = {
        "latitude": round(latitude, 5),
        "longitude": round(longitude, 5),
        "radius_m": round(radius_m, 1),
        "version": TIGERWEB_PROVIDER_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
