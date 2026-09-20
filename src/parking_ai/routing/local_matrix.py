from __future__ import annotations

import math
from collections.abc import Sequence

from parking_ai.domain import Destination, GeoPoint, ParkingSegment, RouteMatrix
from parking_ai.routing.matrix import (
    FALLBACK_NODE_ID,
    ORIGIN_NODE_ID,
    build_synthetic_route_matrix,
)

LOCAL_MATRIX_PROVIDER_VERSION = "local-straight-line-matrix-v1"
DEFAULT_LOCAL_DRIVING_SPEED_M_PER_MIN = 400.0
EARTH_MEAN_RADIUS_M = 6_371_008.8


class LocalDeterministicRouteMatrixProvider:
    """Offline complete matrix using straight-line distance and a fixed driving speed."""

    def __init__(
        self,
        *,
        fallback_location: GeoPoint,
        fallback_id: str,
        fallback_description: str,
        driving_speed_m_per_min: float = DEFAULT_LOCAL_DRIVING_SPEED_M_PER_MIN,
        routing_profile: str = "local-straight-line-driving",
        provider_version: str = LOCAL_MATRIX_PROVIDER_VERSION,
    ) -> None:
        if driving_speed_m_per_min <= 0 or not math.isfinite(driving_speed_m_per_min):
            raise ValueError("driving speed must be finite and positive")
        if not fallback_id.strip():
            raise ValueError("fallback_id must not be blank")
        if not fallback_description.strip():
            raise ValueError("fallback_description must not be blank")
        if not routing_profile.strip():
            raise ValueError("routing_profile must not be blank")
        if not provider_version.strip():
            raise ValueError("provider_version must not be blank")
        self._fallback_location = fallback_location
        self._fallback_id = fallback_id
        self._fallback_description = fallback_description
        self._driving_speed_m_per_min = driving_speed_m_per_min
        self._routing_profile = routing_profile
        self._provider_version = provider_version

    def build_route_matrix(
        self,
        origin: GeoPoint,
        candidates: list[ParkingSegment],
        destination: Destination,
    ) -> RouteMatrix:
        segment_ids = [candidate.segment_id for candidate in candidates]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("route matrix candidates must have unique segment IDs")

        locations = {
            ORIGIN_NODE_ID: origin,
            FALLBACK_NODE_ID: self._fallback_location,
            **{
                candidate.segment_id: _segment_representative_point(candidate)
                for candidate in candidates
            },
        }
        directed_costs: dict[tuple[str, str], float] = {}
        for source_id, source in sorted(locations.items()):
            for target_id, target in sorted(locations.items()):
                if source_id == target_id:
                    continue
                distance_m = _haversine_distance_m(source, target)
                directed_costs[(source_id, target_id)] = (
                    distance_m / self._driving_speed_m_per_min * 60.0
                )

        return build_synthetic_route_matrix(
            segment_ids,
            directed_costs,
            origin=origin,
            destination=destination,
            fallback_id=self._fallback_id,
            fallback_description=self._fallback_description,
            fallback_location=self._fallback_location,
            routing_profile=self._routing_profile,
            provider_version=self._provider_version,
        )


def _segment_representative_point(segment: ParkingSegment) -> GeoPoint:
    coordinates: Sequence[tuple[float, float]] = segment.geometry.coordinates
    longitude = sum(point[0] for point in coordinates) / len(coordinates)
    latitude = sum(point[1] for point in coordinates) / len(coordinates)
    return GeoPoint(latitude=latitude, longitude=longitude)


def _haversine_distance_m(first: GeoPoint, second: GeoPoint) -> float:
    first_latitude = math.radians(first.latitude)
    second_latitude = math.radians(second.latitude)
    delta_latitude = second_latitude - first_latitude
    delta_longitude = math.radians(second.longitude - first.longitude)
    haversine = (
        math.sin(delta_latitude / 2.0) ** 2
        + math.cos(first_latitude)
        * math.cos(second_latitude)
        * math.sin(delta_longitude / 2.0) ** 2
    )
    return 2.0 * EARTH_MEAN_RADIUS_M * math.asin(min(1.0, math.sqrt(haversine)))
