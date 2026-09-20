import math

import pytest
from pydantic import ValidationError

from parking_ai.domain import Destination, GeoPoint, RouteMatrix
from parking_ai.routing import (
    FALLBACK_NODE_ID,
    ORIGIN_NODE_ID,
    build_synthetic_route_matrix,
    route_matrix_content_id,
)

ORIGIN = GeoPoint(latitude=32.84, longitude=-96.79)
DESTINATION = Destination(
    destination_id="destination-1",
    name="Test destination",
    location=GeoPoint(latitude=32.842, longitude=-96.784),
)


def build(segment_ids: list[str], edges: dict[tuple[str, str], float]) -> RouteMatrix:
    return build_synthetic_route_matrix(
        segment_ids,
        edges,
        origin=ORIGIN,
        destination=DESTINATION,
    )


def test_synthetic_matrix_is_directed_canonical_and_has_zero_diagonals() -> None:
    matrix = build(
        ["segment-b", "segment-a"],
        {
            (ORIGIN_NODE_ID, "segment-a"): 10.0,
            ("segment-a", "segment-b"): 7.0,
        },
    )

    assert list(matrix.travel_time_seconds) == [
        ORIGIN_NODE_ID,
        "segment-a",
        "segment-b",
        FALLBACK_NODE_ID,
    ]
    assert matrix.travel_time_seconds["segment-a"]["segment-a"] == 0.0
    assert matrix.travel_time_seconds["segment-b"]["segment-b"] == 0.0
    assert "segment-a" not in matrix.travel_time_seconds["segment-b"]
    assert matrix.binding is not None
    assert matrix.binding.origin == ORIGIN
    assert matrix.binding.destination == DESTINATION
    assert matrix.matrix_id == route_matrix_content_id(matrix)


@pytest.mark.parametrize(
    "travel_time_seconds",
    [
        {},
        {" ": {"segment-a": 1.0}},
        {ORIGIN_NODE_ID: {" ": 1.0}},
        {ORIGIN_NODE_ID: {"segment-a": math.inf}},
        {ORIGIN_NODE_ID: {"segment-a": math.nan}},
        {ORIGIN_NODE_ID: {"segment-a": -1.0}},
    ],
)
def test_route_matrix_rejects_invalid_cost_graph(
    travel_time_seconds: dict[str, dict[str, float]],
) -> None:
    with pytest.raises(ValidationError):
        RouteMatrix(travel_time_seconds=travel_time_seconds, provider_version="test-v1")


def test_synthetic_matrix_rejects_duplicate_reserved_and_unknown_nodes() -> None:
    with pytest.raises(ValueError, match="unique"):
        build(["segment-a", "segment-a"], {})
    with pytest.raises(ValueError, match="reserved"):
        build([ORIGIN_NODE_ID], {})
    with pytest.raises(ValueError, match="reserved"):
        build([FALLBACK_NODE_ID], {})
    with pytest.raises(ValueError, match="unknown node"):
        build(["segment-a"], {(ORIGIN_NODE_ID, "segment-missing"): 3.0})


def test_synthetic_matrix_rejects_nonzero_diagonal() -> None:
    with pytest.raises(ValueError, match="diagonal"):
        build(["segment-a"], {("segment-a", "segment-a"): 1.0})


def test_matrix_id_changes_when_costs_change_under_same_provider_version() -> None:
    first = build(["segment-a"], {(ORIGIN_NODE_ID, "segment-a"): 10.0})
    second = build(["segment-a"], {(ORIGIN_NODE_ID, "segment-a"): 11.0})

    assert first.provider_version == second.provider_version
    assert first.matrix_id != second.matrix_id


@pytest.mark.parametrize("segment_id", [" ", " padded", "origin", "fallback"])
def test_route_matrix_binding_rejects_invalid_candidate_ids(segment_id: str) -> None:
    data = build(["segment-a"], {}).model_dump()
    data["binding"]["candidate_segment_ids"] = [segment_id]  # type: ignore[index]

    with pytest.raises(ValidationError, match="candidate IDs"):
        RouteMatrix.model_validate(data)
