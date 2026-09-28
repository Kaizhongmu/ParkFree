from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from parking_ai.coverage import (
    CoverageAttemptOutcome,
    CoverageAttemptRole,
    FailoverRoadCoverageProvider,
    RoadAcquisition,
    RoadCoverageExhaustedError,
    RoadCoverageProviderError,
    RoadCoverageResponseError,
)
from parking_ai.coverage.tigerweb import TIGERwebRoadCoverageProvider
from parking_ai.domain import Destination, GeoPoint, PhysicalState


class FakeTransport:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        self.calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.payloads.pop(0)


def _destination(*, latitude: float = 32.842, longitude: float = -96.784) -> Destination:
    return Destination(
        destination_id="dest_test",
        name="Test destination",
        location=GeoPoint(latitude=latitude, longitude=longitude),
    )


def _collection(*features: object, exceeded: bool = False) -> dict[str, object]:
    return {
        "type": "FeatureCollection",
        "features": list(features),
        "exceededTransferLimit": exceeded,
    }


def _feature(
    oid: int = 41,
    *,
    name: str | None = "Mock Street",
    mtfcc: str = "S1400",
    coordinates: object = [[-96.785, 32.842], [-96.783, 32.842]],
) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {"OID": oid, "NAME": name, "MTFCC": mtfcc},
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def _provider(transport: FakeTransport, **kwargs: object) -> TIGERwebRoadCoverageProvider:
    return TIGERwebRoadCoverageProvider(
        transport,
        endpoint="https://tiger.example.test/Transportation_LargeScale/MapServer",
        timeout_seconds=4,
        wall_clock=lambda: datetime(2026, 9, 27, 12, tzinfo=UTC),
        **kwargs,
    )


def test_tigerweb_normalizes_local_roads_with_bounded_queries_and_provenance() -> None:
    transport = FakeTransport([_collection(), _collection(), _collection(_feature())])
    provider = _provider(transport)

    result = provider.acquire(_destination(), 8)

    assert [road.feature_id for road in result.roads] == ["tiger_2_41"]
    assert result.roads[0].road_type == "residential"
    assert result.roads[0].physical_state is PhysicalState.UNKNOWN
    assert result.roads[0].parking_candidate is None
    assert result.tags_by_feature_id["tiger_2_41"] == {
        "_provider.tiger_layer": "2",
        "_provider.tiger_oid": "41",
        "_provider.tiger_mtfcc": "S1400",
    }
    assert result.metadata.provider_name == "U.S. Census Bureau TIGERweb"
    assert result.metadata.attribution == "Source: U.S. Census Bureau"
    assert result.metadata.retrieved_at == datetime(2026, 9, 27, 12, tzinfo=UTC)
    assert result.cache_hit is False

    assert len(transport.calls) == 3
    assert [str(call["url"]).split("/")[-2] for call in transport.calls] == ["0", "1", "2"]
    params = transport.calls[0]["params"]
    assert isinstance(params, dict)
    assert params["f"] == "geojson"
    assert params["geometryType"] == "esriGeometryEnvelope"
    assert params["inSR"] == "4326"
    assert params["outSR"] == "4326"
    assert params["outFields"] == "OID,NAME,MTFCC"
    assert params["geometry"].count(",") == 3
    assert transport.calls[0]["headers"] == {"Accept": "application/geo+json, application/json"}

    cached = provider.acquire(_destination(), 8)
    assert cached.cache_hit is True
    assert len(transport.calls) == 3


def test_tigerweb_primary_roads_are_retained_for_provenance_but_not_candidates() -> None:
    transport = FakeTransport([_collection(_feature(mtfcc="S1100")), _collection(), _collection()])

    result = _provider(transport).acquire(_destination(), 5)

    assert result.roads[0].road_type == "primary"
    assert result.roads[0].parking_candidate is False


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"type": "FeatureCollection"}, "features list"),
        (_collection(exceeded=True), "transfer limit"),
        (_collection(_feature(coordinates=[[999, 32.8], [999, 32.9]])), "geographic bounds"),
        (_collection(_feature(oid=0)), "positive numeric identifier"),
    ],
)
def test_tigerweb_rejects_partial_or_invalid_responses(payload: object, message: str) -> None:
    provider = _provider(FakeTransport([payload, _collection(), _collection()]))

    with pytest.raises(RoadCoverageResponseError, match=message):
        provider.acquire(_destination(), 5)


def test_tigerweb_rejects_destinations_outside_supported_us_bounds() -> None:
    provider = _provider(FakeTransport([]))

    with pytest.raises(RoadCoverageResponseError, match="supported US bounds"):
        provider.acquire(_destination(latitude=10, longitude=10), 5)


class StubProvider:
    def __init__(self, result: RoadAcquisition | Exception) -> None:
        self.result = result
        self.calls = 0

    def acquire(self, destination: Destination, max_walk_minutes: float) -> RoadAcquisition:
        del destination, max_walk_minutes
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_failover_records_sanitized_attempts_when_tigerweb_succeeds() -> None:
    fallback_result = _provider(
        FakeTransport([_collection(), _collection(), _collection(_feature())])
    ).acquire(_destination(), 5)
    primary = StubProvider(RoadCoverageProviderError("private Overpass detail"))
    fallback = StubProvider(fallback_result)
    provider = FailoverRoadCoverageProvider(
        primary,
        fallback,
        primary_name="Overpass API / OpenStreetMap",
        fallback_name="U.S. Census Bureau TIGERweb",
    )

    result = provider.acquire(_destination(), 5)

    assert primary.calls == 1
    assert fallback.calls == 1
    assert [(attempt.role, attempt.outcome) for attempt in result.provider_attempts] == [
        (CoverageAttemptRole.PRIMARY, CoverageAttemptOutcome.FAILED),
        (CoverageAttemptRole.FALLBACK, CoverageAttemptOutcome.SUCCEEDED),
    ]
    assert "private" not in result.model_dump_json()


def test_failover_tries_fallback_when_primary_returns_no_roads() -> None:
    primary_result = _provider(
        FakeTransport([_collection(), _collection(), _collection()])
    ).acquire(_destination(), 5)
    fallback_result = _provider(
        FakeTransport([_collection(), _collection(), _collection(_feature())])
    ).acquire(_destination(), 5)
    primary = StubProvider(primary_result)
    fallback = StubProvider(fallback_result)
    provider = FailoverRoadCoverageProvider(
        primary,
        fallback,
        primary_name="Overpass API / OpenStreetMap",
        fallback_name="U.S. Census Bureau TIGERweb",
    )

    result = provider.acquire(_destination(), 5)

    assert primary.calls == 1
    assert fallback.calls == 1
    assert len(result.roads) == 1
    assert [attempt.outcome for attempt in result.provider_attempts] == [
        CoverageAttemptOutcome.EMPTY,
        CoverageAttemptOutcome.SUCCEEDED,
    ]


def test_failover_preserves_empty_primary_when_fallback_fails() -> None:
    primary_result = _provider(
        FakeTransport([_collection(), _collection(), _collection()])
    ).acquire(_destination(), 5)
    provider = FailoverRoadCoverageProvider(
        StubProvider(primary_result),
        StubProvider(RoadCoverageProviderError("private fallback")),
        primary_name="Overpass API / OpenStreetMap",
        fallback_name="U.S. Census Bureau TIGERweb",
    )

    result = provider.acquire(_destination(), 5)

    assert not result.roads
    assert [attempt.outcome for attempt in result.provider_attempts] == [
        CoverageAttemptOutcome.EMPTY,
        CoverageAttemptOutcome.FAILED,
    ]


def test_failover_reports_both_sanitized_attempts_when_all_providers_fail() -> None:
    provider = FailoverRoadCoverageProvider(
        StubProvider(RoadCoverageProviderError("private primary")),
        StubProvider(RoadCoverageProviderError("private fallback")),
        primary_name="Overpass API / OpenStreetMap",
        fallback_name="U.S. Census Bureau TIGERweb",
    )

    with pytest.raises(RoadCoverageExhaustedError) as caught:
        provider.acquire(_destination(), 5)

    assert [attempt.outcome for attempt in caught.value.attempts] == [
        CoverageAttemptOutcome.FAILED,
        CoverageAttemptOutcome.FAILED,
    ]
    assert "private" not in str(caught.value)
