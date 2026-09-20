from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from parking_ai.domain import (
    Destination,
    GeoPoint,
    RouteFallback,
    RouteMatrix,
    RouteMatrixBinding,
)

ORIGIN_NODE_ID = "origin"
FALLBACK_NODE_ID = "fallback"
SYNTHETIC_MATRIX_VERSION = "synthetic-route-matrix-v1"
MATRIX_SCHEMA_VERSION = "route-matrix-v1"


def build_synthetic_route_matrix(
    segment_ids: Sequence[str],
    directed_travel_time_seconds: Mapping[tuple[str, str], float],
    *,
    origin: GeoPoint,
    destination: Destination,
    fallback_id: str = "synthetic-guaranteed-fallback",
    fallback_description: str = "Proceed to the modeled guaranteed-parking fallback.",
    fallback_location: GeoPoint | None = None,
    routing_profile: str = "synthetic-driving",
    provider_version: str = SYNTHETIC_MATRIX_VERSION,
) -> RouteMatrix:
    """Build a deterministic directed matrix from explicit analytical test costs.

    The reserved origin node is added automatically. Missing directed edges remain absent and
    therefore represent unreachable travel; diagonal zeroes are added for every declared node.
    """

    normalized_ids = tuple(sorted(segment_ids))
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValueError("synthetic route matrix segment IDs must be unique")
    if any(not segment_id.strip() for segment_id in normalized_ids):
        raise ValueError("synthetic route matrix segment IDs must not be blank")
    reserved_ids = {ORIGIN_NODE_ID, FALLBACK_NODE_ID}
    collisions = reserved_ids.intersection(normalized_ids)
    if collisions:
        collision = min(collisions)
        raise ValueError(f"segment ID {collision!r} is reserved for route planning")

    node_ids = (ORIGIN_NODE_ID, *normalized_ids, FALLBACK_NODE_ID)
    known_nodes = set(node_ids)
    matrix: dict[str, dict[str, float]] = {source_id: {source_id: 0.0} for source_id in node_ids}
    for (source_id, target_id), seconds in sorted(directed_travel_time_seconds.items()):
        if source_id not in known_nodes or target_id not in known_nodes:
            raise ValueError(
                "synthetic route matrix edge references an unknown node: "
                f"{source_id!r} -> {target_id!r}"
            )
        if source_id == target_id and seconds != 0:
            raise ValueError("synthetic route matrix diagonal costs must be zero")
        matrix[source_id][target_id] = seconds

    binding = RouteMatrixBinding(
        matrix_schema_version=MATRIX_SCHEMA_VERSION,
        origin=origin,
        destination=_canonical_destination(destination),
        candidate_segment_ids=list(normalized_ids),
        fallback=RouteFallback(
            fallback_id=fallback_id,
            description=fallback_description,
            location=fallback_location,
        ),
        routing_profile=routing_profile,
    )
    matrix_id = _matrix_content_id(matrix, provider_version, binding)
    return RouteMatrix(
        travel_time_seconds=matrix,
        provider_version=provider_version,
        matrix_id=matrix_id,
        binding=binding,
    )


def route_matrix_content_id(route_matrix: RouteMatrix) -> str:
    """Recompute the stable snapshot ID for a bound route matrix."""

    if route_matrix.binding is None:
        raise ValueError("route matrix has no request binding")
    return _matrix_content_id(
        route_matrix.travel_time_seconds,
        route_matrix.provider_version,
        route_matrix.binding,
    )


def canonical_destination(destination: Destination) -> Destination:
    """Normalize access-point order for matrix binding and comparison."""

    return _canonical_destination(destination)


def _canonical_destination(destination: Destination) -> Destination:
    return destination.model_copy(
        update={
            "access_points": sorted(
                destination.access_points, key=lambda item: item.access_point_id
            )
        },
        deep=True,
    )


def _matrix_content_id(
    travel_time_seconds: Mapping[str, Mapping[str, float]],
    provider_version: str,
    binding: RouteMatrixBinding,
) -> str:
    payload = {
        "binding": binding.model_dump(mode="json"),
        "id_scheme_version": 1,
        "provider_version": provider_version,
        "travel_time_seconds": {
            source_id: dict(sorted(targets.items()))
            for source_id, targets in sorted(travel_time_seconds.items())
        },
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"matrix_{digest[:32]}"
