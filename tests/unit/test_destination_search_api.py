from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from parking_ai.config import Settings
from parking_ai.domain import EvidenceStoragePolicy, GeoPoint
from parking_ai.geocoding import (
    GeocodingMatch,
    GeocodingProviderError,
    GeocodingProviderMetadata,
    GeocodingRequest,
    GeocodingResult,
    GeocodingStatus,
)
from parking_ai.main import create_app

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(environment="test", log_level="CRITICAL")


def _metadata() -> GeocodingProviderMetadata:
    return GeocodingProviderMetadata(
        provider_name="fixture-geocoder",
        provider_version="fixture-v1",
        attribution="© OpenStreetMap contributors",
        license="ODbL 1.0",
        source_uri="fixture:nominatim",
        raw_storage_policy=EvidenceStoragePolicy.EPHEMERAL,
        normalized_storage_policy=EvidenceStoragePolicy.PERSIST,
        retrieved_at=NOW,
    )


def _match(match_id: str, name: str, latitude: float, longitude: float) -> GeocodingMatch:
    return GeocodingMatch(
        match_id=match_id,
        name=name,
        formatted_address=f"{name}, United States",
        location=GeoPoint(latitude=latitude, longitude=longitude),
        destination_type="library",
        source_reference=f"fixture:{match_id}",
    )


def test_destination_search_returns_all_ambiguous_us_matches_without_selecting_one() -> None:
    received: list[GeocodingRequest] = []

    def handler(request: GeocodingRequest) -> GeocodingResult:
        received.append(request)
        matches = (
            _match("geo-dallas", "Central Library", 32.78, -96.8),
            _match("geo-austin", "Central Library", 30.27, -97.74),
        )
        return GeocodingResult(
            status=GeocodingStatus.AMBIGUOUS,
            matches=matches,
            metadata=_metadata(),
        )

    client = TestClient(create_app(_settings(), destination_search_handler=handler))
    response = client.post(
        "/v1/destinations/search",
        json={"query": "  Central   Library  "},
    )

    assert response.status_code == 200
    assert received == [GeocodingRequest(query="Central Library")]
    assert response.json()["status"] == "AMBIGUOUS"
    assert [item["match_id"] for item in response.json()["matches"]] == [
        "geo-dallas",
        "geo-austin",
    ]
    assert response.json()["metadata"]["attribution"] == "© OpenStreetMap contributors"


def test_destination_search_is_disabled_without_an_explicit_handler_or_user_agent() -> None:
    client = TestClient(create_app(_settings()))

    response = client.post("/v1/destinations/search", json={"query": "Seattle Center"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Destination search is temporarily unavailable"}


def test_destination_search_rejects_invalid_input_before_calling_provider() -> None:
    calls = 0

    def handler(_request: GeocodingRequest) -> GeocodingResult:
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not run")

    client = TestClient(create_app(_settings(), destination_search_handler=handler))
    invalid_payloads: list[dict[str, Any]] = [
        {"query": " "},
        {"query": "x"},
        {"query": "x" * 256},
        {"query": "Seattle", "limit": 10},
    ]

    for payload in invalid_payloads:
        assert client.post("/v1/destinations/search", json=payload).status_code == 422
    assert calls == 0


def test_destination_search_fails_closed_without_leaking_provider_details() -> None:
    def handler(_request: GeocodingRequest) -> GeocodingResult:
        raise GeocodingProviderError("private upstream URL and query")

    client = TestClient(create_app(_settings(), destination_search_handler=handler))
    response = client.post(
        "/v1/destinations/search",
        json={"query": "Seattle Center"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Destination search is temporarily unavailable"}
    assert "private" not in response.text


def test_parking_search_never_calls_destination_discovery() -> None:
    calls = 0

    def handler(_request: GeocodingRequest) -> GeocodingResult:
        nonlocal calls
        calls += 1
        raise AssertionError("parking search must not call geocoding")

    client = TestClient(create_app(_settings(), destination_search_handler=handler))
    response = client.post(
        "/v1/parking/search",
        json={
            "origin": {"lat": 32.842, "lon": -96.784},
            "destination": {"query": "Fondren Library Center"},
            "arrival_time": "now",
            "parking_duration_minutes": 60,
        },
    )

    assert response.status_code == 503
    assert calls == 0
