import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from parking_ai.domain import (
    Destination,
    FreeState,
    GeoPoint,
    LegalState,
    LineStringGeometry,
    ParkingSegment,
    SearchConstraints,
    SegmentSide,
)
from parking_ai.gis.models import RoadFeature, SideReferenceDirection

EARTH_MEAN_RADIUS_M = 6_371_008.8
DEFAULT_WALKING_SPEED_M_PER_MIN = 80.0
COORDINATE_DECIMAL_PLACES = 7
DEFAULT_ELIGIBLE_ROAD_TYPES = frozenset(
    {"living_street", "residential", "secondary", "tertiary", "unclassified"}
)


@dataclass(frozen=True)
class _RankedSegment:
    distance_m: float
    segment: ParkingSegment


class CandidateSegmentConflictError(ValueError):
    """Raised when multiple road features claim one stable ID with conflicting data."""


def _haversine_distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    first_lon, first_lat = first
    second_lon, second_lat = second
    lat1 = math.radians(first_lat)
    lat2 = math.radians(second_lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second_lon - first_lon)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_MEAN_RADIUS_M * math.asin(min(1.0, math.sqrt(value)))


def geodesic_length_m(coordinates: Sequence[tuple[float, float]]) -> float:
    return sum(_haversine_distance_m(first, second) for first, second in pairwise(coordinates))


def _normalize_geometry_with_direction(
    coordinates: Iterable[tuple[float, float]],
) -> tuple[tuple[tuple[float, float], ...], bool]:
    rounded: list[tuple[float, float]] = []
    for longitude, latitude in coordinates:
        point = (
            round(longitude, COORDINATE_DECIMAL_PLACES),
            round(latitude, COORDINATE_DECIMAL_PLACES),
        )
        if not rounded or point != rounded[-1]:
            rounded.append(point)
    if len(rounded) < 2:
        raise ValueError("candidate segment geometry must contain two distinct points")
    forward = tuple(rounded)
    reverse = tuple(reversed(rounded))
    if reverse < forward:
        return reverse, True
    return forward, False


def normalize_geometry(
    coordinates: Iterable[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    geometry, _ = _normalize_geometry_with_direction(coordinates)
    return geometry


def _normalize_street_name(street_name: str | None) -> str | None:
    if street_name is None:
        return None
    normalized = " ".join(street_name.split())
    return normalized or None


def _normalize_road_type(road_type: str) -> str:
    return " ".join(road_type.split()).casefold()


def _road_order_key(road: RoadFeature) -> str:
    """Provide an input-order-independent traversal, including collision failures."""

    return json.dumps(road.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def stable_segment_id(
    geometry: Sequence[tuple[float, float]],
    street_name: str | None,
    road_type: str,
    side: SegmentSide,
) -> str:
    normalized_street_name = _normalize_street_name(street_name)
    payload = {
        "geometry": list(normalize_geometry(geometry)),
        "road_type": _normalize_road_type(road_type),
        "side": side.value,
        "street_name": normalized_street_name.casefold() if normalized_street_name else None,
        "version": 1,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"seg_{digest[:32]}"


def _split_feature(feature: RoadFeature) -> Iterable[tuple[tuple[float, float], ...]]:
    coordinates = feature.geometry.coordinates
    indexes = sorted({0, len(coordinates) - 1, *feature.break_indexes})
    for start, end in pairwise(indexes):
        if end > start:
            yield tuple(coordinates[start : end + 1])


def _canonical_side(side: SegmentSide, *, geometry_reversed: bool) -> SegmentSide:
    if not geometry_reversed:
        return side
    if side is SegmentSide.LEFT:
        return SegmentSide.RIGHT
    if side is SegmentSide.RIGHT:
        return SegmentSide.LEFT
    return side


def _projected_point_segment_distance_m(
    point: GeoPoint,
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    reference_latitude = math.radians(point.latitude)

    def project(coordinate: tuple[float, float]) -> tuple[float, float]:
        longitude, latitude = coordinate
        x = (
            math.radians(longitude - point.longitude)
            * EARTH_MEAN_RADIUS_M
            * math.cos(reference_latitude)
        )
        y = math.radians(latitude - point.latitude) * EARTH_MEAN_RADIUS_M
        return x, y

    start_x, start_y = project(start)
    end_x, end_y = project(end)
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    denominator = delta_x * delta_x + delta_y * delta_y
    if denominator == 0:
        return math.hypot(start_x, start_y)
    ratio = max(0.0, min(1.0, -(start_x * delta_x + start_y * delta_y) / denominator))
    return math.hypot(start_x + ratio * delta_x, start_y + ratio * delta_y)


def point_geometry_distance_m(point: GeoPoint, geometry: Sequence[tuple[float, float]]) -> float:
    """Return the deterministic local-projection distance from a point to a LineString."""

    return min(
        _projected_point_segment_distance_m(point, start, end) for start, end in pairwise(geometry)
    )


class DeterministicCandidateSegmentService:
    """Pure Phase 2 generator implementing the CandidateSegmentService protocol."""

    def __init__(
        self,
        roads: Sequence[RoadFeature],
        *,
        evidence_id: str,
        data_freshness: datetime,
        walking_speed_m_per_min: float = DEFAULT_WALKING_SPEED_M_PER_MIN,
        eligible_road_types: frozenset[str] = DEFAULT_ELIGIBLE_ROAD_TYPES,
    ) -> None:
        if data_freshness.utcoffset() is None:
            raise ValueError("data_freshness must be a timezone-aware datetime")
        if walking_speed_m_per_min <= 0:
            raise ValueError("walking speed must be positive")
        self._roads = tuple(roads)
        self._evidence_id = evidence_id
        self._data_freshness = data_freshness
        self._walking_speed_m_per_min = walking_speed_m_per_min
        self._eligible_road_types = frozenset(
            _normalize_road_type(road_type) for road_type in eligible_road_types
        )

    def _is_candidate(self, road: RoadFeature) -> bool:
        if road.parking_candidate is False:
            return False
        road_type = _normalize_road_type(road.road_type)
        return road.parking_candidate is True or road_type in self._eligible_road_types

    def _generate_all(self) -> list[ParkingSegment]:
        segments: dict[str, ParkingSegment] = {}
        source_feature_ids: dict[str, str] = {}
        for road in sorted(self._roads, key=_road_order_key):
            if not self._is_candidate(road):
                continue
            street_name = _normalize_street_name(road.street_name)
            road_type = _normalize_road_type(road.road_type)
            for raw_geometry in _split_feature(road):
                geometry, geometry_reversed = _normalize_geometry_with_direction(raw_geometry)
                length_m = geodesic_length_m(geometry)
                if length_m <= 0:
                    continue
                canonical_sides = road.candidate_sides
                if road.side_reference_direction is SideReferenceDirection.SOURCE_GEOMETRY:
                    canonical_sides = tuple(
                        _canonical_side(side, geometry_reversed=geometry_reversed)
                        for side in canonical_sides
                    )
                for side in sorted(canonical_sides, key=lambda value: value.value):
                    segment_id = stable_segment_id(geometry, street_name, road_type, side)
                    candidate = ParkingSegment(
                        segment_id=segment_id,
                        geometry=LineStringGeometry(coordinates=list(geometry)),
                        street_name=street_name,
                        side=side,
                        length_m=length_m,
                        road_type=road_type,
                        physical_state=road.physical_state,
                        legal_state=LegalState.UNKNOWN,
                        free_state=FreeState.UNKNOWN,
                        legal_confidence=0.0,
                        evidence_refs=[self._evidence_id],
                        data_freshness=self._data_freshness,
                    )
                    existing = segments.get(segment_id)
                    if existing is not None and existing != candidate:
                        first_feature_id = source_feature_ids[segment_id]
                        conflicting_feature_ids = sorted((first_feature_id, road.feature_id))
                        raise CandidateSegmentConflictError(
                            "stable segment ID collision has conflicting records: "
                            f"{segment_id} from features {conflicting_feature_ids[0]!r} and "
                            f"{conflicting_feature_ids[1]!r}"
                        )
                    segments[segment_id] = candidate
                    source_feature_ids[segment_id] = road.feature_id
        return sorted(segments.values(), key=lambda segment: segment.segment_id)

    def get_candidate_segments(
        self,
        destination: Destination,
        search_constraints: SearchConstraints,
    ) -> list[ParkingSegment]:
        access_points = [point.location for point in destination.access_points]
        if not access_points:
            access_points = [destination.location]
        maximum_distance_m = search_constraints.max_walk_minutes * self._walking_speed_m_per_min
        ranked: list[_RankedSegment] = []
        for segment in self._generate_all():
            distance_m = min(
                point_geometry_distance_m(point, segment.geometry.coordinates)
                for point in access_points
            )
            if distance_m <= maximum_distance_m:
                ranked.append(_RankedSegment(distance_m=distance_m, segment=segment))
        ranked.sort(key=lambda item: (round(item.distance_m, 6), item.segment.segment_id))
        return [item.segment for item in ranked[: search_constraints.max_candidates]]
