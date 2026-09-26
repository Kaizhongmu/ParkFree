from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from parking_ai.config import Settings
from parking_ai.domain import Destination, GeoPoint, RouteFallback, SearchRoute
from parking_ai.main import create_app
from parking_ai.orchestrator.schemas import (
    ParkingSearchCommand,
    ParkingSearchResponse,
    SearchVersions,
)
from parking_ai.orchestrator.search import (
    DestinationNotFoundError,
    IdempotencyConflictError,
    SearchUnavailableError,
)

FIXED_NOW = datetime(2026, 9, 20, 15, 30, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(environment="test", log_level="CRITICAL")


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "origin": {"lat": 32.842, "lon": -96.784},
        "destination": {"query": "Fondren Library"},
        "arrival_time": "now",
        "parking_duration_minutes": 90,
        "free_only": True,
        "max_walk_minutes": 8,
        "vehicle_profile": {"type": "passenger", "permit_types": [" SMU-A "]},
        "max_candidates": 20,
    }
    payload.update(overrides)
    return payload


def _response() -> ParkingSearchResponse:
    fallback = RouteFallback(
        fallback_id="fallback-garage",
        description="Use the guaranteed parking garage",
        guarantees_parking=True,
    )
    route = SearchRoute(
        route_id="route-1",
        session_id="session-1",
        steps=[],
        expected_time_to_park_min=5,
        success_probability=0,
        fallback_description=fallback.description,
        optimizer_version="optimizer-v1",
        route_matrix_version="matrix-1",
        route_matrix_provider_version="fixture-v1",
        cost_model_version="cost-v1",
    )
    return ParkingSearchResponse(
        session_id="session-1",
        resolved_arrival_time=FIXED_NOW,
        destination=Destination(
            destination_id="smu-fondren",
            name="Fondren Library",
            location=GeoPoint(latitude=32.842, longitude=-96.784),
        ),
        route=route,
        fallback=fallback,
        candidate_decisions=[],
        versions=SearchVersions(
            rule_engine="rules-v1",
            availability_model="availability-v1",
            route_matrix_id="matrix-1",
            route_matrix_provider="fixture-v1",
            optimizer="optimizer-v1",
            cost_model="cost-v1",
        ),
    )


def _client(
    handler: Callable[[ParkingSearchCommand, str | None], ParkingSearchResponse] | None,
) -> TestClient:
    app = create_app(_settings(), search_handler=handler, clock=lambda: FIXED_NOW)
    return TestClient(app, raise_server_exceptions=False)


def test_search_translates_request_and_forwards_idempotency_key() -> None:
    received: list[tuple[ParkingSearchCommand, str | None]] = []

    def handler(command: ParkingSearchCommand, key: str | None) -> ParkingSearchResponse:
        received.append((command, key))
        return _response()

    response = _client(handler).post(
        "/v1/parking/search",
        json=_payload(),
        headers={"Idempotency-Key": "request-123"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-1"
    command, key = received[0]
    assert command.origin == GeoPoint(latitude=32.842, longitude=-96.784)
    assert command.destination.query == "Fondren Library"
    assert command.arrival_time == FIXED_NOW
    assert command.arrival_time_was_now is True
    assert command.parking_duration_minutes == 90
    assert command.vehicle_profile.requested_parking_duration_min == 90
    assert command.vehicle_profile.permit_types == ["SMU-A"]
    assert key == "request-123"


def test_search_preserves_explicit_aware_arrival() -> None:
    received: list[ParkingSearchCommand] = []

    def handler(command: ParkingSearchCommand, _key: str | None) -> ParkingSearchResponse:
        received.append(command)
        return _response()

    response = _client(handler).post(
        "/v1/parking/search",
        json=_payload(arrival_time="2026-11-01T01:30:00-05:00"),
    )

    assert response.status_code == 200
    assert received[0].arrival_time.isoformat() == "2026-11-01T01:30:00-05:00"
    assert received[0].arrival_time_was_now is False


@pytest.mark.parametrize(
    "payload",
    [
        _payload(origin={"lat": 91, "lon": -96.784}),
        _payload(destination={"query": "Fondren", "destination_id": "smu-fondren"}),
        _payload(destination={}),
        _payload(arrival_time="2026-09-20T15:30:00"),
        _payload(parking_duration_minutes=0),
        _payload(parking_duration_minutes=1.5),
        _payload(free_only=1),
        _payload(max_walk_minutes="8"),
        _payload(max_candidates=21),
        _payload(vehicle_profile={"type": "passenger", "permit_types": [" "]}),
        _payload(
            vehicle_profile={
                "type": "passenger",
                "permit_types": ["SMU-A", "smu-a"],
            }
        ),
        _payload(vehicle_profile={"type": "passenger", "permit_types": ["x" * 129]}),
        _payload(
            vehicle_profile={
                "type": "passenger",
                "permit_types": [f"permit-{index}" for index in range(33)],
            }
        ),
        _payload(unexpected=True),
    ],
)
def test_search_rejects_invalid_or_extra_input(payload: dict[str, Any]) -> None:
    called = False

    def handler(_command: ParkingSearchCommand, _key: str | None) -> ParkingSearchResponse:
        nonlocal called
        called = True
        return _response()

    response = _client(handler).post("/v1/parking/search", json=payload)

    assert response.status_code == 422
    assert called is False


@pytest.mark.parametrize("key", [" leading", "trailing ", "x" * 129])
def test_search_rejects_invalid_idempotency_key(key: str) -> None:
    response = _client(lambda _command, _key: _response()).post(
        "/v1/parking/search",
        json=_payload(),
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 422


def test_openapi_exposes_permit_input_bounds() -> None:
    schemas = _client(None).get("/openapi.json").json()["components"]["schemas"]
    permit_schema = schemas["VehicleProfileRequest"]["properties"]["permit_types"]

    assert permit_schema["maxItems"] == 32
    assert permit_schema["items"]["maxLength"] == 128


def test_search_without_configured_handler_is_safely_unavailable() -> None:
    response = _client(None).post("/v1/parking/search", json=_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "Parking search is temporarily unavailable"}


def test_default_database_handler_fails_closed_without_guaranteed_fallback() -> None:
    settings = Settings(
        environment="test",
        log_level="CRITICAL",
        database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
    )
    client = TestClient(create_app(settings, clock=lambda: FIXED_NOW))

    response = client.post("/v1/parking/search", json=_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "Parking search is temporarily unavailable"}


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (DestinationNotFoundError("private destination details"), 404, "Destination was not found"),
        (
            IdempotencyConflictError("private hash details"),
            409,
            "Idempotency key conflicts with an existing search",
        ),
        (
            SearchUnavailableError("private provider details"),
            503,
            "Parking search is temporarily unavailable",
        ),
        (
            RuntimeError("private unexpected details"),
            503,
            "Parking search is temporarily unavailable",
        ),
        (
            ValueError("private corrupted provenance details"),
            503,
            "Parking search is temporarily unavailable",
        ),
    ],
)
def test_search_maps_failures_without_leaking_exception_details(
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    def handler(_command: ParkingSearchCommand, _key: str | None) -> ParkingSearchResponse:
        raise error

    response = _client(handler).post("/v1/parking/search", json=_payload())

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "private" not in response.text


def test_search_rejects_invalid_handler_response_without_leaking_details() -> None:
    def handler(_command: ParkingSearchCommand, _key: str | None) -> ParkingSearchResponse:
        return cast(ParkingSearchResponse, {"secret": "private invalid response"})

    response = _client(handler).post("/v1/parking/search", json=_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "Parking search is temporarily unavailable"}
    assert "private" not in response.text
