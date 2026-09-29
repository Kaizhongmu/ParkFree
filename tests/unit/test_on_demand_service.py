from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parking_ai.coverage import (
    CoverageAttemptOutcome,
    CoverageAttemptRole,
    CoverageProviderAttempt,
    CoverageProviderMetadata,
    OnDemandParkingCommand,
    OnDemandParkingResponse,
    OnDemandParkingService,
    OnDemandParkingStatus,
    OnDemandResearchMode,
    ResearchEnrichmentStatus,
    RoadAcquisition,
    RoadCoverageExhaustedError,
    RoadCoverageProviderError,
    SelectedDestinationNotFoundError,
)
from parking_ai.domain import (
    Destination,
    EvidenceStoragePolicy,
    FreeState,
    GeoPoint,
    LegalState,
    LineStringGeometry,
    UserProfile,
)
from parking_ai.geocoding import (
    GeocodingMatch,
    GeocodingProviderMetadata,
    GeocodingRequest,
    GeocodingResult,
    GeocodingStatus,
)
from parking_ai.gis.models import RoadFeature

NOW = datetime(2026, 9, 26, 16, tzinfo=UTC)
MATCH = GeocodingMatch(
    match_id="geo_selected",
    name="The Village Chase",
    formatted_address="The Village Chase, Dallas, Texas, United States",
    location=GeoPoint(latitude=32.856698, longitude=-96.766458),
    destination_type="residential",
    source_reference="fixture:place:1",
)


def _geocoding_result(*matches: GeocodingMatch) -> GeocodingResult:
    return GeocodingResult(
        status=(
            GeocodingStatus.NO_MATCH
            if not matches
            else GeocodingStatus.UNIQUE
            if len(matches) == 1
            else GeocodingStatus.AMBIGUOUS
        ),
        matches=matches,
        metadata=GeocodingProviderMetadata(
            provider_name="fixture-geocoder",
            provider_version="v1",
            attribution="Fixture geocoder attribution",
            license="fixture license",
            source_uri="fixture:geocoder",
            raw_storage_policy=EvidenceStoragePolicy.EPHEMERAL,
            normalized_storage_policy=EvidenceStoragePolicy.PERSIST,
            retrieved_at=NOW,
        ),
    )


def _acquisition(*roads: RoadFeature) -> RoadAcquisition:
    return RoadAcquisition(
        roads=roads,
        tags_by_feature_id={road.feature_id: {"highway": road.road_type} for road in roads},
        metadata=CoverageProviderMetadata(
            provider_name="fixture-roads",
            provider_version="v1",
            attribution="Fixture road attribution",
            license="fixture license",
            source_uri="fixture:roads",
            raw_storage_policy=EvidenceStoragePolicy.EPHEMERAL,
            normalized_storage_policy=EvidenceStoragePolicy.PERSIST,
            retrieved_at=NOW,
        ),
    )


def _road(feature_id: str = "road-1") -> RoadFeature:
    return RoadFeature(
        feature_id=feature_id,
        geometry=LineStringGeometry(coordinates=[(-96.7665, 32.8566), (-96.7665, 32.8570)]),
        street_name="Amesbury Drive",
        road_type="residential",
    )


def _command(
    *,
    match_id: str = MATCH.match_id,
    research_mode: OnDemandResearchMode = OnDemandResearchMode.RESEARCH,
) -> OnDemandParkingCommand:
    return OnDemandParkingCommand(
        origin=GeoPoint(latitude=32.84, longitude=-96.78),
        destination_query="The Village Chase",
        destination_match_id=match_id,
        research_mode=research_mode,
        arrival_time=NOW,
        arrival_time_was_now=True,
        parking_duration_minutes=60,
        free_only=True,
        max_walk_minutes=8,
        vehicle_profile=UserProfile(requested_parking_duration_min=60),
        max_candidates=20,
    )


class _Geocoder:
    def __init__(self, result: GeocodingResult) -> None:
        self.result = result
        self.requests: list[GeocodingRequest] = []

    def geocode(self, request: GeocodingRequest) -> GeocodingResult:
        self.requests.append(request)
        return self.result


class _RoadProvider:
    def __init__(self, acquisition: RoadAcquisition) -> None:
        self.acquisition = acquisition
        self.calls: list[tuple[str, float]] = []

    def acquire(self, destination: Destination, max_walk_minutes: float) -> RoadAcquisition:
        self.calls.append((destination.destination_id, max_walk_minutes))
        return self.acquisition


def test_on_demand_service_uses_distinct_provider_for_each_requested_mode() -> None:
    instant = _RoadProvider(_acquisition(_road("instant-road")))
    research = _RoadProvider(_acquisition(_road("research-road")))
    service = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        instant,
        research_road_provider=research,
    )

    instant_response = service.search(_command(research_mode=OnDemandResearchMode.INSTANT))
    research_response = service.search(_command(research_mode=OnDemandResearchMode.RESEARCH))

    assert len(instant.calls) == 1
    assert len(research.calls) == 1
    assert instant_response.research_mode is OnDemandResearchMode.INSTANT
    assert instant_response.enrichment_status is ResearchEnrichmentStatus.NOT_REQUESTED
    assert research_response.research_mode is OnDemandResearchMode.RESEARCH
    assert research_response.enrichment_status is ResearchEnrichmentStatus.APPLIED


class _TimezoneResolver:
    def __init__(self, timezone_name: str = "America/Chicago") -> None:
        self.timezone_name = timezone_name
        self.locations: list[GeoPoint] = []

    def resolve(self, location: GeoPoint) -> str:
        self.locations.append(location)
        return self.timezone_name


def test_on_demand_service_revalidates_match_and_returns_unknown_provisional_leads() -> None:
    geocoder = _Geocoder(_geocoding_result(MATCH))
    provider = _RoadProvider(_acquisition(_road()))
    service = OnDemandParkingService(geocoder, provider)

    first = service.search(_command())
    second = service.search(_command())

    assert geocoder.requests == [
        GeocodingRequest(query="The Village Chase", limit=10),
        GeocodingRequest(query="The Village Chase", limit=10),
    ]
    assert first.status is OnDemandParkingStatus.PROVISIONAL_LEADS
    assert first.research_mode is OnDemandResearchMode.RESEARCH
    assert first.enrichment_status is ResearchEnrichmentStatus.NOT_CONFIGURED
    assert first.destination.destination_id == second.destination.destination_id
    assert first.destination.destination_id.startswith("ond_")
    assert first.destination.access_points[0].destination_id == first.destination.destination_id
    assert len(first.candidate_segments) == 2
    assert all(segment.legal_state is LegalState.UNKNOWN for segment in first.candidate_segments)
    assert all(segment.free_state is FreeState.UNKNOWN for segment in first.candidate_segments)
    assert all(segment.legal_confidence == 0 for segment in first.candidate_segments)
    assert first.coverage is not None
    assert first.coverage.road_count == 1
    assert first.coverage.tagged_road_count == 0
    assert first.attribution == (
        "Fixture geocoder attribution",
        "Fixture road attribution",
    )
    assert "not verified legal or free parking" in first.warnings[0]


def test_on_demand_service_predicts_conditional_availability_in_destination_timezone() -> None:
    resolver = _TimezoneResolver()
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road())),
        timezone_resolver=resolver,
        prediction_clock=lambda: NOW,
    ).search(_command())

    assert resolver.locations == [MATCH.location]
    assert response.destination_timezone == "America/Chicago"
    assert response.availability_assumption == "CONDITIONAL_ON_LEGAL_AND_USABLE_CURB"
    assert response.calibration_status == "UNCALIBRATED_HEURISTIC"
    assert {prediction.segment_id for prediction in response.availability_predictions} == {
        segment.segment_id for segment in response.candidate_segments
    }
    assert all(
        prediction.feature_snapshot is not None
        and prediction.feature_snapshot.local_timezone == "America/Chicago"
        for prediction in response.availability_predictions
    )
    assert all(0 <= prediction.probability <= 1 for prediction in response.availability_predictions)
    assert "not a free-parking or legality probability" in response.warnings[-1]


def test_on_demand_service_uses_one_prediction_timestamp_for_the_response() -> None:
    clock_calls = 0

    def advancing_clock() -> datetime:
        nonlocal clock_calls
        value = NOW.replace(microsecond=clock_calls)
        clock_calls += 1
        return value

    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road())),
        timezone_resolver=_TimezoneResolver(),
        prediction_clock=advancing_clock,
    ).search(_command())

    assert clock_calls == 1
    assert {prediction.predicted_at for prediction in response.availability_predictions} == {NOW}


def test_on_demand_service_discards_tiny_clipping_fragments_before_truncation() -> None:
    tiny = RoadFeature(
        feature_id="tiny",
        geometry=LineStringGeometry(
            coordinates=[
                (-96.766458, 32.856698),
                (-96.766458, 32.856703),
            ]
        ),
        street_name="Boundary Fragment",
        road_type="residential",
    )
    usable = RoadFeature(
        feature_id="usable",
        geometry=LineStringGeometry(
            coordinates=[
                (-96.76630, 32.85660),
                (-96.76630, 32.85700),
            ]
        ),
        street_name="Usable Street",
        road_type="residential",
    )
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(tiny, usable)),
    ).search(_command().model_copy(update={"max_candidates": 1}))

    assert len(response.candidate_segments) == 1
    assert response.candidate_segments[0].street_name == "Usable Street"
    assert response.candidate_segments[0].length_m >= 6


def test_on_demand_service_counts_only_explicit_parking_tags() -> None:
    acquisition = _acquisition(_road()).model_copy(
        update={
            "tags_by_feature_id": {
                "road-1": {
                    "highway": "residential",
                    "name": "Amesbury Drive",
                    "parking:right": "lane",
                }
            }
        }
    )

    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(acquisition),
    ).search(_command())

    assert response.coverage is not None
    assert response.coverage.tagged_road_count == 1


def test_on_demand_service_does_not_trust_a_stale_or_fabricated_match_id() -> None:
    provider = _RoadProvider(_acquisition(_road()))
    service = OnDemandParkingService(_Geocoder(_geocoding_result(MATCH)), provider)

    with pytest.raises(SelectedDestinationNotFoundError):
        service.search(_command(match_id="geo_not_returned"))

    assert provider.calls == []


def test_on_demand_service_returns_explicit_provider_unavailable_without_candidates() -> None:
    class FailingProvider:
        def acquire(
            self,
            destination: Destination,
            max_walk_minutes: float,
        ) -> RoadAcquisition:
            raise RoadCoverageProviderError("private provider details")

    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        FailingProvider(),
    ).search(_command())

    assert response.status is OnDemandParkingStatus.PROVIDER_UNAVAILABLE
    assert response.coverage is None
    assert response.candidate_segments == ()
    assert "private" not in " ".join(response.warnings)


def test_on_demand_service_exposes_sanitized_attempts_when_provider_chain_is_exhausted() -> None:
    attempts = (
        CoverageProviderAttempt(
            provider_name="Overpass API / OpenStreetMap",
            role=CoverageAttemptRole.PRIMARY,
            outcome=CoverageAttemptOutcome.FAILED,
        ),
        CoverageProviderAttempt(
            provider_name="U.S. Census Bureau TIGERweb",
            role=CoverageAttemptRole.FALLBACK,
            outcome=CoverageAttemptOutcome.FAILED,
        ),
    )

    class ExhaustedProvider:
        def acquire(
            self,
            destination: Destination,
            max_walk_minutes: float,
        ) -> RoadAcquisition:
            raise RoadCoverageExhaustedError(attempts)

    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        ExhaustedProvider(),
    ).search(_command())

    assert response.status is OnDemandParkingStatus.PROVIDER_UNAVAILABLE
    assert response.provider_attempts == attempts
    assert "private" not in response.model_dump_json()


def test_research_mode_reports_degraded_only_after_a_successful_fallback() -> None:
    acquisition = _acquisition(_road()).model_copy(
        update={
            "provider_attempts": (
                CoverageProviderAttempt(
                    provider_name="Overpass API / OpenStreetMap",
                    role=CoverageAttemptRole.PRIMARY,
                    outcome=CoverageAttemptOutcome.FAILED,
                ),
                CoverageProviderAttempt(
                    provider_name="U.S. Census Bureau TIGERweb",
                    role=CoverageAttemptRole.FALLBACK,
                    outcome=CoverageAttemptOutcome.SUCCEEDED,
                ),
            )
        }
    )
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road("instant-road"))),
        research_road_provider=_RoadProvider(acquisition),
    ).search(_command(research_mode=OnDemandResearchMode.RESEARCH))

    assert response.enrichment_status is ResearchEnrichmentStatus.DEGRADED
    assert any("fell back to the fast road source" in warning for warning in response.warnings)


def test_research_mode_reports_failed_when_distinct_provider_chain_is_exhausted() -> None:
    attempts = (
        CoverageProviderAttempt(
            provider_name="Overpass API / OpenStreetMap",
            role=CoverageAttemptRole.PRIMARY,
            outcome=CoverageAttemptOutcome.FAILED,
        ),
        CoverageProviderAttempt(
            provider_name="U.S. Census Bureau TIGERweb",
            role=CoverageAttemptRole.FALLBACK,
            outcome=CoverageAttemptOutcome.FAILED,
        ),
    )

    class ExhaustedResearchProvider:
        def acquire(
            self,
            destination: Destination,
            max_walk_minutes: float,
        ) -> RoadAcquisition:
            raise RoadCoverageExhaustedError(attempts)

    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road("instant-road"))),
        research_road_provider=ExhaustedResearchProvider(),
    ).search(_command(research_mode=OnDemandResearchMode.RESEARCH))

    assert response.status is OnDemandParkingStatus.PROVIDER_UNAVAILABLE
    assert response.enrichment_status is ResearchEnrichmentStatus.FAILED


def test_research_mode_preserves_no_candidates_when_no_source_returns_roads() -> None:
    empty_acquisition = _acquisition().model_copy(
        update={
            "provider_attempts": (
                CoverageProviderAttempt(
                    provider_name="Overpass API / OpenStreetMap",
                    role=CoverageAttemptRole.PRIMARY,
                    outcome=CoverageAttemptOutcome.EMPTY,
                ),
                CoverageProviderAttempt(
                    provider_name="U.S. Census Bureau TIGERweb",
                    role=CoverageAttemptRole.FALLBACK,
                    outcome=CoverageAttemptOutcome.FAILED,
                ),
            )
        }
    )
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road("instant-road"))),
        research_road_provider=_RoadProvider(empty_acquisition),
    ).search(_command(research_mode=OnDemandResearchMode.RESEARCH))

    assert response.status is OnDemandParkingStatus.NO_CANDIDATES
    assert response.enrichment_status is ResearchEnrichmentStatus.FAILED
    assert response.coverage is not None
    assert response.coverage.road_count == 0


def test_on_demand_response_rejects_success_for_provider_unavailable() -> None:
    class FailingProvider:
        def acquire(
            self,
            destination: Destination,
            max_walk_minutes: float,
        ) -> RoadAcquisition:
            raise RoadCoverageProviderError("private provider details")

    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        FailingProvider(),
    ).search(_command())
    payload = response.model_dump(mode="python")
    payload["provider_attempts"][0]["outcome"] = CoverageAttemptOutcome.SUCCEEDED

    with pytest.raises(ValueError, match="require failed attempts"):
        OnDemandParkingResponse.model_validate(payload)


def test_on_demand_response_rejects_fallback_before_primary() -> None:
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road())),
    ).search(_command())
    payload = response.model_dump(mode="python")
    payload["provider_attempts"][0]["role"] = CoverageAttemptRole.FALLBACK
    payload["coverage"]["provider_attempts"][0]["role"] = CoverageAttemptRole.FALLBACK

    with pytest.raises(ValueError, match="start with primary"):
        OnDemandParkingResponse.model_validate(payload)


def test_on_demand_response_rejects_provisional_leads_without_success() -> None:
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road())),
    ).search(_command())
    payload = response.model_dump(mode="python")
    payload["provider_attempts"][0]["outcome"] = CoverageAttemptOutcome.EMPTY
    payload["coverage"]["provider_attempts"][0]["outcome"] = CoverageAttemptOutcome.EMPTY

    with pytest.raises(ValueError, match="successful provider"):
        OnDemandParkingResponse.model_validate(payload)


def test_on_demand_service_distinguishes_empty_snapshot_from_no_provider() -> None:
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition()),
    ).search(_command())

    assert response.status is OnDemandParkingStatus.NO_CANDIDATES
    assert response.coverage is not None
    assert response.coverage.road_count == 0
    assert response.candidate_segments == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("legal_state", LegalState.LEGAL),
        ("free_state", FreeState.FREE),
        ("legal_confidence", 0.25),
        ("regulation_refs", ["rule_unreviewed"]),
        ("availability_probability", 0.5),
        ("availability_interval", (0.2, 0.8)),
    ],
)
def test_on_demand_response_rejects_non_provisional_segment_state(
    field: str,
    value: object,
) -> None:
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road())),
    ).search(_command())
    payload = response.model_dump(mode="python")
    payload["candidate_segments"][0][field] = value

    with pytest.raises(ValueError, match="provisional leads"):
        OnDemandParkingResponse.model_validate(payload)


def test_on_demand_response_rejects_prediction_for_another_candidate_set() -> None:
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road())),
        timezone_resolver=_TimezoneResolver(),
        prediction_clock=lambda: NOW,
    ).search(_command())
    payload = response.model_dump(mode="python")
    payload["availability_predictions"][0]["segment_id"] = "seg_unrelated"

    with pytest.raises(ValueError, match="reference provisional candidate"):
        OnDemandParkingResponse.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("calibration_status",), None, "declare calibration"),
        (
            ("availability_predictions", 0, "target_window_seconds"),
            60,
            "feature snapshot",
        ),
        (
            ("availability_predictions", 0, "feature_snapshot", "local_timezone"),
            "America/New_York",
            "match the response",
        ),
        (
            ("availability_predictions", 0, "feature_snapshot", "segment_length_m"),
            999.0,
            "match the response",
        ),
    ],
)
def test_on_demand_response_rejects_inconsistent_prediction_metadata(
    path: tuple[str | int, ...],
    value: object,
    message: str,
) -> None:
    response = OnDemandParkingService(
        _Geocoder(_geocoding_result(MATCH)),
        _RoadProvider(_acquisition(_road())),
        timezone_resolver=_TimezoneResolver(),
        prediction_clock=lambda: NOW,
    ).search(_command())
    payload = response.model_dump(mode="python")
    target: object = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=message):
        OnDemandParkingResponse.model_validate(payload)
