from copy import deepcopy
from datetime import UTC, datetime

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from parking_ai.database.models import (
    ParkingSegmentModel,
    SearchRouteStepModel,
    SearchSessionModel,
)
from parking_ai.domain import (
    AvailabilityPrediction,
    AvailabilityReasonCode,
    FreeState,
    GeoPoint,
    LegalityEvaluation,
    LegalState,
    RegulationReasonCode,
    RouteCandidateSnapshot,
    SearchConstraints,
    UserProfile,
)
from parking_ai.gis import (
    DeterministicCandidateSegmentService,
    load_smu_gis_fixture,
    upsert_gis_slice,
)
from parking_ai.orchestrator.persistence import (
    ReplayIntegrityError,
    SQLAlchemySearchSessionRepository,
)
from parking_ai.orchestrator.schemas import (
    DestinationSelector,
    ParkingCandidateDecision,
    ParkingSearchCommand,
    ParkingSearchResponse,
    SearchExecution,
    SearchVersions,
)
from parking_ai.orchestrator.search import (
    IdempotencyConflictError,
    search_artifact_hash,
    search_request_hash,
)
from parking_ai.routing import (
    FALLBACK_NODE_ID,
    ORIGIN_NODE_ID,
    DeterministicSearchRoutePlanner,
    RouteOptimizerConfig,
    RoutePlanningContext,
    build_synthetic_route_matrix,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
ORIGIN = GeoPoint(latitude=32.843, longitude=-96.786)


def _execution(session: Session) -> SearchExecution:
    fixture = load_smu_gis_fixture()
    candidate_service = DeterministicCandidateSegmentService(
        fixture.osm.roads,
        evidence_id=fixture.osm.evidence.evidence_id,
        data_freshness=fixture.osm.observed_at,
    )
    segment = candidate_service.get_candidate_segments(
        fixture.destination,
        SearchConstraints(max_walk_minutes=30, max_candidates=1),
    )[0]
    upsert_gis_slice(session, fixture.destination, fixture.osm.evidence, [segment])
    session.flush()

    legality = LegalityEvaluation(
        evaluation_id="evaluation-phase6",
        segment_id=segment.segment_id,
        legal_state=LegalState.LEGAL,
        free_state=FreeState.FREE,
        confidence=0.95,
        evidence_refs=[fixture.osm.evidence.evidence_id],
        reason_codes=[RegulationReasonCode.NO_PAYMENT_REQUIRED],
        evaluated_at=NOW,
        rule_engine_version="rules-phase6-test",
    )
    availability = AvailabilityPrediction(
        prediction_id="prediction-phase6",
        segment_id=segment.segment_id,
        probability=0.9,
        model_version="availability-phase6-test",
        predicted_at=NOW,
        target_window_seconds=90,
        reason_codes=[AvailabilityReasonCode.HEURISTIC_PRIOR_ONLY],
    )
    decision = ParkingCandidateDecision(
        segment=segment,
        legality=legality,
        availability=availability,
        eligible=True,
    )
    snapshot = RouteCandidateSnapshot.from_results(legality, availability)
    enriched = segment.model_copy(
        update={
            "legal_state": legality.legal_state,
            "free_state": legality.free_state,
            "legal_confidence": legality.confidence,
            "availability_probability": availability.probability,
            "evidence_refs": legality.evidence_refs,
        },
        deep=True,
    )
    route_matrix = build_synthetic_route_matrix(
        [segment.segment_id],
        {
            (ORIGIN_NODE_ID, segment.segment_id): 10.0,
            (segment.segment_id, FALLBACK_NODE_ID): 60.0,
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1_000.0,
        },
        origin=ORIGIN,
        destination=fixture.destination,
        fallback_id="phase6-fallback",
        fallback_description="Use the guaranteed Phase 6 test fallback.",
        fallback_location=GeoPoint(latitude=32.84, longitude=-96.78),
        provider_version="matrix-phase6-test",
    )
    route = DeterministicSearchRoutePlanner(
        RoutePlanningContext(
            session_id="session-phase6",
            rule_engine_version=legality.rule_engine_version,
            availability_model_version=availability.model_version,
            candidate_snapshots=(snapshot,),
        ),
        config=RouteOptimizerConfig(fallback_service_seconds=0.0),
    ).plan_search_route([enriched], ORIGIN, fixture.destination, route_matrix)
    assert route.steps
    assert route_matrix.binding is not None

    command = ParkingSearchCommand(
        origin=ORIGIN,
        destination=DestinationSelector(destination_id=fixture.destination.destination_id),
        arrival_time=NOW,
        parking_duration_minutes=60,
        max_walk_minutes=30,
        vehicle_profile=UserProfile(requested_parking_duration_min=60),
        max_candidates=1,
    )
    versions = SearchVersions(
        rule_engine=legality.rule_engine_version,
        availability_model=availability.model_version,
        route_matrix_id=route_matrix.matrix_id,
        route_matrix_provider=route_matrix.provider_version,
        optimizer=route.optimizer_version,
        cost_model=route.cost_model_version,
    )
    response = ParkingSearchResponse(
        session_id=route.session_id,
        resolved_arrival_time=NOW,
        destination=fixture.destination,
        route=route,
        fallback=route_matrix.binding.fallback,
        candidate_decisions=[decision],
        versions=versions,
    )
    request_hash = search_request_hash(command)
    artifact_hash = search_artifact_hash([decision], route_matrix, route, response)
    return SearchExecution(
        session_id=route.session_id,
        request_hash=request_hash,
        idempotency_key_hash="b" * 64,
        command=command,
        destination=fixture.destination,
        candidate_decisions=[decision],
        route_matrix=route_matrix,
        route=route,
        response=response,
        artifact_hash=artifact_hash,
        created_at=NOW,
    )


def test_repository_persists_and_replays_once_without_mutating_segments(engine: Engine) -> None:
    with Session(engine) as session:
        transaction = session.begin()
        execution = _execution(session)
        repository = SQLAlchemySearchSessionRepository(session)

        repository.save(execution)
        assert session.in_transaction()
        repository.save(execution)

        stored = session.get(SearchSessionModel, execution.session_id)
        assert stored is not None
        assert stored.replayable is True
        assert stored.route_id == execution.route.route_id
        assert stored.response_snapshot == execution.response.model_dump(mode="json")
        assert session.scalar(select(func.count()).select_from(SearchSessionModel)) == 1
        assert session.scalar(select(func.count()).select_from(SearchRouteStepModel)) == 1
        step = session.scalar(select(SearchRouteStepModel))
        assert step is not None
        assert step.legality_evaluation_id == "evaluation-phase6"
        assert step.availability_prediction_id == "prediction-phase6"
        assert step.availability_target_window_seconds == 90

        persisted_segment = session.get(
            ParkingSegmentModel,
            execution.candidate_decisions[0].segment.segment_id,
        )
        assert persisted_segment is not None
        assert persisted_segment.legal_state is LegalState.UNKNOWN
        assert persisted_segment.free_state is FreeState.UNKNOWN
        assert persisted_segment.availability_probability is None

        replay = repository.get_replay("b" * 64, execution.request_hash)
        assert replay == execution.response
        with pytest.raises(IdempotencyConflictError, match="different request"):
            repository.get_replay("b" * 64, "d" * 64)
        transaction.rollback()


def test_database_rejects_incomplete_replayable_session(engine: Engine) -> None:
    with Session(engine) as session:
        transaction = session.begin()
        execution = _execution(session)
        incomplete = SearchSessionModel(
            session_id="session-incomplete",
            destination_id=execution.destination.destination_id,
            origin=WKTElement(
                f"POINT({execution.command.origin.longitude} {execution.command.origin.latitude})",
                srid=4326,
            ),
            requested_arrival_time=NOW,
            free_only=True,
            max_walk_minutes=8.0,
            max_candidates=1,
            candidate_segment_ids=[],
            replayable=True,
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(incomplete)
            session.flush()
        transaction.rollback()


def test_replay_rejects_valid_json_when_matrix_content_was_tampered(engine: Engine) -> None:
    with Session(engine) as session:
        transaction = session.begin()
        execution = _execution(session)
        repository = SQLAlchemySearchSessionRepository(session)
        repository.save(execution)

        stored = session.get(SearchSessionModel, execution.session_id)
        assert stored is not None
        tampered = deepcopy(stored.route_matrix_snapshot)
        assert tampered is not None
        candidate_id = execution.candidate_decisions[0].segment.segment_id
        tampered["travel_time_seconds"]["origin"][candidate_id] += 1.0
        stored.route_matrix_snapshot = tampered
        session.flush()

        with pytest.raises(ReplayIntegrityError, match="content hash"):
            repository.get_replay("b" * 64, execution.request_hash)
        transaction.rollback()
