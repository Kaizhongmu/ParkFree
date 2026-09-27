from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from parking_ai.coverage import RoadCoverageProviderError, RoadCoverageResponseError
from parking_ai.coverage.overpass import (
    OSM_ELEMENT_ID_TAG,
    OSM_ELEMENT_TIMESTAMP_TAG,
    OSM_ELEMENT_VERSION_TAG,
    OverpassRoadCoverageProvider,
)
from parking_ai.domain import Destination, GeoPoint, SegmentSide
from parking_ai.gis.generator import geodesic_length_m


class FakeTransport:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        url: str,
        *,
        form: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        self.calls.append(
            {
                "url": url,
                "form": dict(form),
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.payloads.pop(0)


class FailingTransport:
    def post_json(
        self,
        url: str,
        *,
        form: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        del url, form, headers, timeout_seconds
        raise TimeoutError("secret upstream detail")


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def _destination(*, latitude: float = 32.842, longitude: float = -96.784) -> Destination:
    return Destination(
        destination_id="dest_test",
        name="Test destination",
        location=GeoPoint(latitude=latitude, longitude=longitude),
    )


def _node(node_id: int, longitude: float, latitude: float) -> dict[str, object]:
    return {"type": "node", "id": node_id, "lon": longitude, "lat": latitude}


def _way(
    way_id: int,
    node_ids: list[int],
    *,
    highway: str = "residential",
    name: str = "Mock Street",
    version: int = 3,
    timestamp: str = "2026-09-25T12:34:56Z",
    extra_tags: dict[str, str] | None = None,
) -> dict[str, object]:
    tags = {"highway": highway, "name": name}
    tags.update(extra_tags or {})
    return {
        "type": "way",
        "id": way_id,
        "nodes": node_ids,
        "tags": tags,
        "version": version,
        "timestamp": timestamp,
    }


def _valid_payload() -> dict[str, object]:
    return {
        "elements": [
            _way(20, [2, 3], name="Second Street", version=4),
            _node(3, -96.782, 32.842),
            _node(1, -96.784, 32.842),
            _way(10, [1, 2], name="First Street"),
            _node(2, -96.783, 32.842),
        ]
    }


def _provider(
    transport: FakeTransport | FailingTransport,
    **kwargs: Any,
) -> OverpassRoadCoverageProvider:
    return OverpassRoadCoverageProvider(
        transport,
        user_agent="ParkFree/0.1 test@example.com",
        endpoint="https://overpass.example.test/api/interpreter",
        wall_clock=lambda: datetime(2026, 9, 26, 15, 0, tzinfo=UTC),
        rate_interval_seconds=0,
        **kwargs,
    )


def test_normalizes_roads_splits_shared_nodes_and_retains_provenance() -> None:
    transport = FakeTransport([_valid_payload()])

    result = _provider(transport).acquire(_destination(), 8)

    assert [road.feature_id for road in result.roads] == ["osm_way_10", "osm_way_20"]
    assert result.roads[0].geometry.coordinates == [
        (-96.784, 32.842),
        (-96.783, 32.842),
    ]
    assert result.roads[0].break_indexes == frozenset({0, 1})
    assert result.roads[1].break_indexes == frozenset({0, 1})
    assert result.roads[0].street_name == "First Street"
    assert result.tags_by_feature_id["osm_way_10"] == {
        "highway": "residential",
        "name": "First Street",
        OSM_ELEMENT_ID_TAG: "10",
        OSM_ELEMENT_VERSION_TAG: "3",
        OSM_ELEMENT_TIMESTAMP_TAG: "2026-09-25T12:34:56Z",
    }
    assert result.metadata.provider_name == "Overpass API / OpenStreetMap"
    assert result.metadata.retrieved_at == datetime(2026, 9, 26, 15, 0, tzinfo=UTC)
    assert result.cache_hit is False

    call = transport.calls[0]
    assert call["url"] == "https://overpass.example.test/api/interpreter"
    assert call["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "User-Agent": "ParkFree/0.1 test@example.com",
    }
    query = str(call["form"])
    assert "around:640,32.8420000,-96.7840000" in query
    assert '["highway"="living_street"]' in query
    assert '["highway"="residential"]' in query
    assert '["highway"="secondary"]' in query
    assert '["highway"="tertiary"]' in query
    assert '["highway"="unclassified"]' in query
    assert "out meta qt" in query


def test_response_order_does_not_change_normalized_output() -> None:
    first_payload = _valid_payload()
    second_payload = {"elements": list(reversed(first_payload["elements"]))}  # type: ignore[arg-type]

    first = _provider(FakeTransport([first_payload])).acquire(_destination(), 8)
    second = _provider(FakeTransport([second_payload])).acquire(_destination(), 8)

    assert first.roads == second.roads
    assert first.tags_by_feature_id == second.tags_by_feature_id


def test_filters_noneligible_road_types() -> None:
    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.783, 32.842),
            _way(11, [1, 2], highway="motorway"),
        ]
    }

    result = _provider(FakeTransport([payload])).acquire(_destination(), 5)

    assert result.roads == ()
    assert result.tags_by_feature_id == {}


def test_marks_private_roads_as_non_candidates_without_dropping_provenance() -> None:
    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.783, 32.842),
            _way(11, [1, 2], extra_tags={"access": "private"}),
        ]
    }

    result = _provider(FakeTransport([payload])).acquire(_destination(), 5)

    assert result.roads[0].parking_candidate is False
    assert result.tags_by_feature_id["osm_way_11"]["access"] == "private"


def test_excludes_only_the_side_with_explicit_unconditional_no_parking() -> None:
    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.783, 32.842),
            _way(
                11,
                [1, 2],
                extra_tags={
                    "parking:left": "no",
                    "parking:left:restriction": "no_parking",
                    "parking:right": "lane",
                },
            ),
        ]
    }

    result = _provider(FakeTransport([payload])).acquire(_destination(), 5)

    assert result.roads[0].parking_candidate is True
    assert result.roads[0].candidate_sides == (SegmentSide.RIGHT,)


def test_legacy_parking_lane_prohibition_is_still_respected() -> None:
    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.783, 32.842),
            _way(
                11,
                [1, 2],
                extra_tags={
                    "parking:lane:left": "no_parking",
                    "parking:lane:right": "parallel",
                },
            ),
        ]
    }

    result = _provider(FakeTransport([payload])).acquire(_destination(), 5)

    assert result.roads[0].candidate_sides == (SegmentSide.RIGHT,)
    assert result.tags_by_feature_id["osm_way_11"]["parking:lane:left"] == "no_parking"


def test_cache_is_keyed_by_rounded_coordinate_radius_and_marks_hits() -> None:
    transport = FakeTransport([_valid_payload(), _valid_payload()])
    provider = _provider(transport)

    first = provider.acquire(_destination(), 8)
    cached = provider.acquire(_destination(latitude=32.8420001, longitude=-96.7840001), 8)
    changed_radius = provider.acquire(_destination(), 9)

    assert first.cache_hit is False
    assert cached.cache_hit is True
    assert changed_radius.cache_hit is False
    assert len(transport.calls) == 2


def test_cache_entry_expires_after_ttl() -> None:
    transport = FakeTransport([_valid_payload(), _valid_payload()])
    clock = FakeClock()
    provider = OverpassRoadCoverageProvider(
        transport,
        user_agent="ParkFree/0.1 test@example.com",
        endpoint="https://overpass.example.test/api/interpreter",
        clock=clock,
        sleep=clock.sleep,
        wall_clock=lambda: datetime(2026, 9, 26, 15, 0, tzinfo=UTC),
        rate_interval_seconds=0,
        cache_ttl_seconds=2,
    )

    provider.acquire(_destination(), 8)
    clock.value += 2
    refreshed = provider.acquire(_destination(), 8)

    assert refreshed.cache_hit is False
    assert len(transport.calls) == 2


def test_serializes_requests_and_applies_rate_gate() -> None:
    transport = FakeTransport([_valid_payload(), _valid_payload()])
    clock = FakeClock()
    provider = OverpassRoadCoverageProvider(
        transport,
        user_agent="ParkFree/0.1 test@example.com",
        endpoint="https://overpass.example.test/api/interpreter",
        clock=clock,
        sleep=clock.sleep,
        wall_clock=lambda: datetime(2026, 9, 26, 15, 0, tzinfo=UTC),
        rate_interval_seconds=2,
    )

    provider.acquire(_destination(), 8)
    provider.acquire(_destination(), 9)

    assert clock.sleeps == [2.0]


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/overpass",
        "https://user:password@example.com/api",
        "https://example.com/api?query=yes",
        "https://example.com/api#fragment",
        "https://example.com\\evil",
        "https://example.com/api\nInjected: true",
    ],
)
def test_rejects_unsafe_or_ambiguous_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        OverpassRoadCoverageProvider(
            FakeTransport([]),
            user_agent="ParkFree/0.1 test@example.com",
            endpoint=endpoint,
        )


def test_rejects_regex_metacharacters_in_configured_road_types() -> None:
    with pytest.raises(ValueError, match="eligible road types"):
        _provider(FakeTransport([]), eligible_road_types=frozenset({"residential|motorway"}))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON object"),
        ({}, "elements list"),
        ({"elements": ["bad"]}, "JSON objects"),
        ({"elements": [{"type": "relation", "id": 1}]}, "unsupported element type"),
        (
            {
                "elements": [
                    _node(1, -96.784, 32.842),
                    _way(10, [1, 99]),
                ]
            },
            "missing node",
        ),
        (
            {
                "elements": [
                    _node(1, 181, 91),
                    _node(2, -96.783, 32.842),
                    _way(10, [1, 2]),
                ]
            },
            "outside geographic bounds",
        ),
        (
            {
                "elements": [
                    _node(1, -96.784, 32.842),
                    _node(2, -96.783, 32.842),
                    _way(10, [1, 2], timestamp="not-a-time"),
                ]
            },
            "timestamp is invalid",
        ),
    ],
)
def test_rejects_invalid_response_shapes(payload: object, message: str) -> None:
    with pytest.raises(RoadCoverageResponseError, match=message):
        _provider(FakeTransport([payload])).acquire(_destination(), 8)


def test_wraps_invalid_normalized_road_as_typed_response_error() -> None:
    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.783, 32.842),
            _way(10, [1, 2], name="x" * 256),
        ]
    }

    with pytest.raises(RoadCoverageResponseError, match="invalid road feature"):
        _provider(FakeTransport([payload])).acquire(_destination(), 8)


def test_rejects_per_road_tag_budget_overrun() -> None:
    tags = {f"tag_{index}": "value" for index in range(256)}
    tags["highway"] = "residential"
    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.783, 32.842),
            {
                "type": "way",
                "id": 10,
                "nodes": [1, 2],
                "tags": tags,
                "version": 1,
                "timestamp": "2026-09-25T12:34:56Z",
            },
        ]
    }

    with pytest.raises(RoadCoverageResponseError, match="tag limit"):
        _provider(FakeTransport([payload])).acquire(_destination(), 8)


def test_rejects_element_and_road_budget_overruns() -> None:
    with pytest.raises(RoadCoverageResponseError, match="element limit"):
        _provider(
            FakeTransport(
                [
                    {
                        "elements": [
                            _node(1, -96.784, 32.842),
                            _node(2, -96.783, 32.842),
                        ]
                    }
                ]
            ),
            max_elements=1,
        ).acquire(_destination(), 8)

    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.783, 32.842),
            _way(10, [1, 2]),
            _way(11, [1, 2]),
        ]
    }
    with pytest.raises(RoadCoverageResponseError, match="road limit"):
        _provider(FakeTransport([payload]), max_roads=1).acquire(_destination(), 8)


def test_rejects_destination_outside_supported_us_bounds_before_transport() -> None:
    transport = FakeTransport([])

    with pytest.raises(RoadCoverageResponseError, match="outside supported US bounds"):
        _provider(transport).acquire(_destination(latitude=51.5072, longitude=-0.1276), 8)

    assert transport.calls == []


def test_allows_valid_way_nodes_across_a_national_border() -> None:
    payload = {
        "elements": [
            _node(1, -122.75, 48.999),
            _node(2, -122.75, 49.001),
            _way(10, [1, 2]),
        ]
    }

    result = _provider(FakeTransport([payload])).acquire(
        _destination(latitude=48.999, longitude=-122.75),
        8,
    )

    assert len(result.roads) == 1
    assert result.roads[0].geometry.coordinates[-1] == (-122.75, 49.001)


def test_clips_intersecting_long_way_to_requested_walk_radius() -> None:
    payload = {
        "elements": [
            _node(1, -96.784, 32.842),
            _node(2, -96.684, 32.842),
            _way(10, [1, 2]),
        ]
    }

    result = _provider(FakeTransport([payload])).acquire(_destination(), 1)

    assert len(result.roads) == 1
    coordinates = result.roads[0].geometry.coordinates
    assert coordinates[0] == (-96.784, 32.842)
    assert coordinates[-1] != (-96.684, 32.842)
    assert geodesic_length_m(coordinates) == pytest.approx(80.0, abs=0.2)


def test_wraps_unexpected_transport_failure_without_leaking_details() -> None:
    with pytest.raises(RoadCoverageProviderError, match="Overpass request failed") as caught:
        _provider(FailingTransport()).acquire(_destination(), 8)

    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("minutes", [0, -1, float("inf"), float("nan")])
def test_rejects_invalid_walk_duration(minutes: float) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        _provider(FakeTransport([])).acquire(_destination(), minutes)


def test_caps_query_radius_at_configured_bound() -> None:
    transport = FakeTransport([{"elements": []}])
    provider = _provider(transport, max_radius_m=1_000)

    provider.acquire(_destination(), 15)

    assert "around:1000," in str(transport.calls[0]["form"])
