from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from parking_ai.domain import (
    AvailabilityEvaluationRecord,
    AvailabilityObservationSummary,
    AvailabilityPrediction,
    Evidence,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalityEvaluation,
    LegalState,
    LineStringGeometry,
    ParkingSegment,
    PhysicalState,
    RegulationReasonCode,
    RouteCandidateSnapshot,
    RouteOptimizationStrategy,
    RouteOptimizerSnapshot,
    SearchRoute,
    SearchRouteStep,
    SegmentSide,
)

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)


def valid_segment_data() -> dict[str, object]:
    return {
        "segment_id": "segment-1",
        "geometry": {
            "type": "LineString",
            "coordinates": [[-96.784, 32.842], [-96.783, 32.843]],
        },
        "street_name": "University Boulevard",
        "side": "LEFT",
        "length_m": 42.5,
        "estimated_capacity": 5,
        "road_type": "residential",
        "physical_state": "PARKABLE",
        "legal_state": "UNKNOWN",
        "free_state": "UNKNOWN",
        "legal_confidence": 0.0,
        "data_freshness": NOW,
    }


def test_valid_parking_segment() -> None:
    segment = ParkingSegment.model_validate(valid_segment_data())

    assert segment.segment_id == "segment-1"
    assert segment.side is SegmentSide.LEFT
    assert segment.physical_state is PhysicalState.PARKABLE
    assert segment.legal_state is LegalState.UNKNOWN
    assert segment.free_state is FreeState.UNKNOWN
    assert isinstance(segment.geometry, LineStringGeometry)


@pytest.mark.parametrize("probability", [-0.01, 1.01])
def test_availability_probability_must_be_between_zero_and_one(probability: float) -> None:
    with pytest.raises(ValidationError):
        AvailabilityPrediction(
            segment_id="segment-1",
            probability=probability,
            model_version="test-v0",
            predicted_at=NOW,
        )


@pytest.mark.parametrize(
    ("probability", "interval"),
    [(0.5, (0.7, 0.4)), (0.8, (0.2, 0.7)), (0.5, (-0.1, 0.8)), (0.5, (0.2, 1.1))],
)
def test_invalid_uncertainty_interval(probability: float, interval: tuple[float, float]) -> None:
    with pytest.raises(ValidationError):
        AvailabilityPrediction(
            segment_id="segment-1",
            probability=probability,
            interval=interval,
            model_version="test-v0",
            predicted_at=NOW,
        )


def test_availability_prediction_remains_backward_compatible() -> None:
    prediction = AvailabilityPrediction(
        segment_id="segment-1",
        probability=0.4,
        model_version="legacy-compatible-v0",
        predicted_at=NOW,
    )

    assert prediction.feature_snapshot is None
    assert prediction.uncertainty_method is None
    assert prediction.reason_codes == []


def test_availability_prediction_window_must_match_feature_snapshot() -> None:
    with pytest.raises(ValidationError, match="target window must match"):
        AvailabilityPrediction(
            segment_id="segment-1",
            probability=0.4,
            model_version="availability-v1",
            predicted_at=NOW,
            target_window_seconds=90,
            feature_snapshot={
                "feature_schema_version": "features-v1",
                "arrival_time_utc": NOW,
                "local_timezone": "America/Chicago",
                "local_weekday": "SAT",
                "local_hour": 7,
                "local_utc_offset_minutes": -300,
                "local_fold": 0,
                "time_bucket": "WEEKEND_DAY",
                "search_window_seconds": 60,
                "segment_length_m": 42.5,
                "effective_capacity": 1.0,
                "capacity_source": "EXPLICIT",
                "road_type_bucket": "local",
                "physical_state": "PARKABLE",
                "observation_successes": 0,
                "observation_trials": 0,
                "prior_90_probability": 0.2,
                "prior_strength": 6.0,
            },
        )


def test_observation_successes_cannot_exceed_trials() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        AvailabilityObservationSummary(
            segment_id="segment-1",
            time_bucket="WEEKDAY_PEAK",
            successes=3,
            trials=2,
            as_of=NOW,
        )


def test_evaluation_record_interval_must_contain_probability() -> None:
    with pytest.raises(ValidationError, match="must fall within"):
        AvailabilityEvaluationRecord(
            outcome_id="outcome-1",
            prediction_id="prediction-1",
            segment_id="segment-1",
            model_version="availability-v1",
            feature_schema_version="features-v1",
            target_window_seconds=90,
            probability=0.8,
            outcome=True,
            interval=(0.2, 0.7),
        )


def test_enum_validation_rejects_unknown_value() -> None:
    data = valid_segment_data()
    data["side"] = "CURBSIDE"

    with pytest.raises(ValidationError):
        ParkingSegment.model_validate(data)


def test_timezone_aware_datetime_is_required() -> None:
    data = valid_segment_data()
    data["data_freshness"] = datetime(2026, 9, 19, 12)

    with pytest.raises(ValidationError, match="timezone"):
        ParkingSegment.model_validate(data)


def test_evidence_tier_and_storage_policy_validation() -> None:
    evidence = Evidence(
        evidence_id="evidence-1",
        source_type=EvidenceSourceType.OFFICIAL_CODE,
        source_uri_or_identifier="city-code-section-1",
        retrieved_at=NOW,
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        reliability_tier=EvidenceReliabilityTier.A,
    )

    assert evidence.reliability_tier is EvidenceReliabilityTier.A
    assert evidence.raw_storage_policy is EvidenceStoragePolicy.REFERENCE_ONLY

    invalid_data = evidence.model_dump()
    invalid_data["reliability_tier"] = "E"
    invalid_data["raw_storage_policy"] = "CACHE_FOREVER"
    with pytest.raises(ValidationError) as exc_info:
        Evidence.model_validate(invalid_data)
    assert exc_info.value.error_count() == 2


@pytest.mark.parametrize("field_name", ["published_at", "observed_at"])
def test_evidence_timestamp_cannot_be_after_retrieval(field_name: str) -> None:
    data: dict[str, object] = {
        "evidence_id": "evidence-future-metadata",
        "source_type": EvidenceSourceType.OFFICIAL_CODE,
        "source_uri_or_identifier": "fixture://future-metadata",
        "retrieved_at": NOW,
        field_name: NOW + timedelta(seconds=1),
        "raw_storage_policy": EvidenceStoragePolicy.REFERENCE_ONLY,
        "reliability_tier": EvidenceReliabilityTier.A,
    }

    with pytest.raises(ValidationError, match=f"{field_name} cannot be after retrieved_at"):
        Evidence.model_validate(data)


@pytest.mark.parametrize(
    ("source_type", "tier"),
    [
        (EvidenceSourceType.UNIVERSITY, EvidenceReliabilityTier.A),
        (EvidenceSourceType.OSM, EvidenceReliabilityTier.A),
        (EvidenceSourceType.COMMUNITY, EvidenceReliabilityTier.A),
        (EvidenceSourceType.COMMUNITY, EvidenceReliabilityTier.B),
        (EvidenceSourceType.IMAGERY_INFERENCE, EvidenceReliabilityTier.C),
    ],
)
def test_evidence_rejects_tier_above_source_authority_ceiling(
    source_type: EvidenceSourceType,
    tier: EvidenceReliabilityTier,
) -> None:
    with pytest.raises(ValidationError, match="exceeds its source authority ceiling"):
        Evidence(
            evidence_id="evidence-elevated",
            source_type=source_type,
            source_uri_or_identifier="fixture://elevated",
            retrieved_at=NOW,
            raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
            reliability_tier=tier,
        )


@pytest.mark.parametrize(
    ("source_type", "tier"),
    [
        (EvidenceSourceType.OFFICIAL_CODE, EvidenceReliabilityTier.D),
        (EvidenceSourceType.OSM, EvidenceReliabilityTier.C),
        (EvidenceSourceType.COMMUNITY, EvidenceReliabilityTier.D),
        (EvidenceSourceType.IMAGERY_INFERENCE, EvidenceReliabilityTier.D),
    ],
)
def test_evidence_allows_tier_at_or_below_source_authority_ceiling(
    source_type: EvidenceSourceType,
    tier: EvidenceReliabilityTier,
) -> None:
    evidence = Evidence(
        evidence_id="evidence-conservative",
        source_type=source_type,
        source_uri_or_identifier="fixture://conservative",
        published_at=NOW,
        observed_at=NOW,
        retrieved_at=NOW,
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        reliability_tier=tier,
    )

    assert evidence.reliability_tier is tier


def route_step(**updates: object) -> SearchRouteStep:
    data: dict[str, object] = {
        "route_step_id": "step-1",
        "session_id": "session-1",
        "segment_id": "segment-1",
        "step_order": 0,
        "legal_state": LegalState.LEGAL,
        "free_state": FreeState.FREE,
        "legal_confidence": 0.9,
        "availability_probability": 0.5,
        "drive_eta_min": 1.0,
        "walk_min": 2.0,
        "rule_engine_version": "rules-v1",
    }
    data.update(updates)
    return SearchRouteStep.model_validate(data)


def test_search_route_remains_backward_compatible_without_phase_five_diagnostics() -> None:
    route = SearchRoute(
        route_id="route-1",
        session_id="session-1",
        steps=[route_step()],
        expected_time_to_park_min=5.0,
        success_probability=0.5,
        optimizer_version="optimizer-v0",
    )

    assert route.failure_probability is None
    assert route.route_matrix_version is None
    assert route.optimizer_snapshot is None


@pytest.mark.parametrize(
    "steps",
    [
        [route_step(step_order=1)],
        [route_step(session_id="other-session")],
        [route_step(), route_step(route_step_id="step-2", step_order=1)],
    ],
)
def test_search_route_rejects_inconsistent_steps(steps: list[SearchRouteStep]) -> None:
    with pytest.raises(ValidationError):
        SearchRoute(
            route_id="route-1",
            session_id="session-1",
            steps=steps,
            expected_time_to_park_min=5.0,
            success_probability=0.5,
            optimizer_version="optimizer-v1",
        )


def test_search_route_probabilities_must_be_complements() -> None:
    with pytest.raises(ValidationError, match="sum to one"):
        SearchRoute(
            route_id="route-1",
            session_id="session-1",
            steps=[],
            expected_time_to_park_min=5.0,
            success_probability=0.5,
            failure_probability=0.4,
            optimizer_version="optimizer-v1",
        )


def test_route_candidate_snapshot_is_derived_from_versioned_results() -> None:
    legality = LegalityEvaluation(
        evaluation_id="legality-1",
        segment_id="segment-1",
        legal_state=LegalState.LEGAL,
        free_state=FreeState.FREE,
        confidence=0.9,
        evidence_refs=["evidence-1"],
        reason_codes=[RegulationReasonCode.TIME_LIMIT_APPLIES],
        evaluated_at=NOW,
        rule_engine_version="rules-v1",
    )
    availability = AvailabilityPrediction(
        prediction_id="prediction-1",
        segment_id="segment-1",
        probability=0.6,
        model_version="availability-v1",
        predicted_at=NOW,
        target_window_seconds=90,
    )

    snapshot = RouteCandidateSnapshot.from_results(legality, availability)

    assert snapshot.legality_evaluation_id == "legality-1"
    assert snapshot.availability_prediction_id == "prediction-1"
    assert snapshot.availability_target_window_seconds == 90


def phase_five_route() -> SearchRoute:
    step = route_step(
        evidence_refs=["evidence-1"],
        availability_model_version="availability-v1",
    )
    candidate_snapshot = RouteCandidateSnapshot(
        segment_id="segment-1",
        legality_evaluation_id="legality-1",
        legal_state=LegalState.LEGAL,
        free_state=FreeState.FREE,
        legal_confidence=0.9,
        evidence_refs=["evidence-1"],
        rule_engine_version="rules-v1",
        availability_prediction_id="prediction-1",
        availability_probability=0.5,
        availability_model_version="availability-v1",
        availability_target_window_seconds=90,
    )
    optimizer_snapshot = RouteOptimizerSnapshot(
        optimization_strategy=RouteOptimizationStrategy.GREEDY,
        optimizer_version="optimizer-v1",
        cost_model_version="cost-v1",
        rule_engine_version="rules-v1",
        availability_model_version="availability-v1",
        availability_target_window_seconds=90,
        require_free=True,
        local_search_seconds=90.0,
        fallback_service_seconds=300.0,
        walking_speed_m_per_min=80.0,
        beam_width=8,
        candidate_limit=20,
    )
    return SearchRoute(
        route_id="route-phase-five",
        session_id="session-1",
        steps=[step],
        expected_time_to_park_min=5.0,
        success_probability=0.5,
        failure_probability=0.5,
        optimizer_version="optimizer-v1",
        cost_model_version="cost-v1",
        optimization_strategy=RouteOptimizationStrategy.GREEDY,
        optimizer_snapshot=optimizer_snapshot,
        selected_candidate_snapshots=[candidate_snapshot],
    )


def test_phase_five_route_accepts_consistent_provenance_snapshots() -> None:
    route = phase_five_route()

    assert route.steps[0].segment_id == route.selected_candidate_snapshots[0].segment_id


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("selected_candidate_snapshots", 0, "segment_id"), "other", "order"),
        (
            ("selected_candidate_snapshots", 0, "availability_probability"),
            0.4,
            "decision values",
        ),
        (("optimizer_snapshot", "optimizer_version"), "other", "top-level"),
        (
            ("selected_candidate_snapshots", 0, "availability_target_window_seconds"),
            60,
            "rule, model, and window",
        ),
    ],
)
def test_phase_five_route_rejects_contradictory_provenance(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    data = phase_five_route().model_dump()
    target: object = data
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError, match=message):
        SearchRoute.model_validate(data)
