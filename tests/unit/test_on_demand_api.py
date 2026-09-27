from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from parking_ai.api.on_demand import ON_DEMAND_HANDLER_STATE_KEY
from parking_ai.config import Settings
from parking_ai.coverage import (
    OnDemandParkingCommand,
    OnDemandParkingResponse,
    OnDemandParkingStatus,
    SelectedDestinationNotFoundError,
)
from parking_ai.domain import Destination, GeoPoint
from parking_ai.geocoding import GeocodingProviderError
from parking_ai.main import create_app

NOW = datetime(2026, 9, 26, 17, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(environment="test", log_level="CRITICAL")


def _payload() -> dict[str, Any]:
    return {
        "origin": {"lat": 32.84, "lon": -96.78},
        "destination": {
            "query": "  The Village   Chase  ",
            "match_id": "geo_selected",
        },
        "arrival_time": "now",
        "parking_duration_minutes": 60,
        "free_only": True,
        "max_walk_minutes": 8.0,
        "vehicle_profile": {"type": "passenger", "permit_types": []},
        "max_candidates": 20,
    }


def _unavailable_response(command: OnDemandParkingCommand) -> OnDemandParkingResponse:
    return OnDemandParkingResponse(
        status=OnDemandParkingStatus.PROVIDER_UNAVAILABLE,
        destination=Destination(
            destination_id="ond_fixture",
            name="The Village Chase",
            location=GeoPoint(latitude=32.856698, longitude=-96.766458),
        ),
        resolved_arrival_time=command.arrival_time,
        candidate_segments=(),
        coverage=None,
        warnings=("Road coverage could not be acquired.",),
        attribution=("Fixture geocoder attribution",),
    )


def test_on_demand_api_builds_strict_command_and_resolves_now() -> None:
    received: list[OnDemandParkingCommand] = []

    def handler(command: OnDemandParkingCommand) -> OnDemandParkingResponse:
        received.append(command)
        return _unavailable_response(command)

    client = TestClient(
        create_app(
            _settings(),
            on_demand_parking_handler=handler,
            clock=lambda: NOW,
        )
    )
    response = client.post("/v1/parking/on-demand", json=_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "PROVIDER_UNAVAILABLE"
    assert len(received) == 1
    command = received[0]
    assert command.destination_query == "The Village Chase"
    assert command.destination_match_id == "geo_selected"
    assert command.arrival_time == NOW
    assert command.arrival_time_was_now is True
    assert command.origin == GeoPoint(latitude=32.84, longitude=-96.78)
    assert command.vehicle_profile.requested_parking_duration_min == 60


def test_on_demand_api_is_disabled_without_both_provider_configuration_values() -> None:
    client = TestClient(create_app(_settings()))

    response = client.post("/v1/parking/on-demand", json=_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "On-demand parking discovery is temporarily unavailable"}


def test_default_on_demand_runtime_requires_both_provider_user_agents() -> None:
    only_geocoder = create_app(
        Settings(
            environment="test",
            log_level="CRITICAL",
            nominatim_user_agent="ParkFree/0.1 (tests@example.com)",
        )
    )
    assert getattr(only_geocoder.state, ON_DEMAND_HANDLER_STATE_KEY) is None

    configured = create_app(
        Settings(
            environment="test",
            log_level="CRITICAL",
            nominatim_user_agent="ParkFree/0.1 (tests@example.com)",
            overpass_user_agent="ParkFree/0.1 (tests@example.com)",
        )
    )
    assert callable(getattr(configured.state, ON_DEMAND_HANDLER_STATE_KEY))


def test_on_demand_api_rejects_invalid_input_before_calling_handler() -> None:
    calls = 0

    def handler(command: OnDemandParkingCommand) -> OnDemandParkingResponse:
        nonlocal calls
        calls += 1
        return _unavailable_response(command)

    client = TestClient(create_app(_settings(), on_demand_parking_handler=handler))
    payloads = []
    for field, value in (
        ("max_walk_minutes", 15.1),
        ("max_candidates", 21),
        ("arrival_time", "2026-09-26T12:00:00"),
        ("free_only", "true"),
    ):
        payload = _payload()
        payload[field] = value
        payloads.append(payload)
    extra = _payload()
    extra["browser_coordinates"] = {"lat": 1, "lon": 2}
    payloads.append(extra)
    short_query = _payload()
    short_query["destination"]["query"] = " x "
    payloads.append(short_query)

    for payload in payloads:
        assert client.post("/v1/parking/on-demand", json=payload).status_code == 422
    assert calls == 0


def test_on_demand_api_maps_stale_selection_and_provider_errors_without_leaks() -> None:
    def stale(_command: OnDemandParkingCommand) -> OnDemandParkingResponse:
        raise SelectedDestinationNotFoundError("private query details")

    stale_response = TestClient(create_app(_settings(), on_demand_parking_handler=stale)).post(
        "/v1/parking/on-demand", json=_payload()
    )
    assert stale_response.status_code == 409
    assert "private" not in stale_response.text

    def failed(_command: OnDemandParkingCommand) -> OnDemandParkingResponse:
        raise GeocodingProviderError("private provider URL")

    failed_response = TestClient(create_app(_settings(), on_demand_parking_handler=failed)).post(
        "/v1/parking/on-demand", json=_payload()
    )
    assert failed_response.status_code == 503
    assert "private" not in failed_response.text
