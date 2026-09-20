from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parking_ai.domain import (
    AvailabilityPrediction,
    Destination,
    FreeState,
    GeoPoint,
    LegalityEvaluation,
    LegalState,
    LineStringGeometry,
    ParkingSegment,
    RegulationReasonCode,
    SearchConstraints,
    UserProfile,
)
from parking_ai.orchestrator import (
    CandidateExclusionReason,
    DestinationSelector,
    IdempotencyConflictError,
    ParkingSearchCommand,
    ParkingSearchOrchestrator,
)
from parking_ai.orchestrator.schemas import ParkingSearchResponse, SearchExecution
from parking_ai.routing import FALLBACK_NODE_ID, ORIGIN_NODE_ID, build_synthetic_route_matrix

NOW = datetime(2026, 9, 20, 15, tzinfo=UTC)
ORIGIN = GeoPoint(latitude=32.84, longitude=-96.79)
DESTINATION = Destination(
    destination_id="destination-1",
    name="Fondren Library",
    location=GeoPoint(latitude=32.842, longitude=-96.784),
)


def segment(segment_id: str) -> ParkingSegment:
    return ParkingSegment(
        segment_id=segment_id,
        geometry=LineStringGeometry(coordinates=[(-96.784, 32.842), (-96.783, 32.843)]),
        street_name=f"Street {segment_id}",
        length_m=80,
        road_type="residential",
        data_freshness=NOW,
    )


class Resolver:
    def resolve_destination(
        self, *, query: str | None, destination_id: str | None
    ) -> Destination | None:
        if query == DESTINATION.name or destination_id == DESTINATION.destination_id:
            return DESTINATION
        return None


class CandidateService:
    def __init__(self, values: list[ParkingSegment]) -> None:
        self.values = values
        self.received_constraints: SearchConstraints | None = None

    def get_candidate_segments(
        self, destination: Destination, search_constraints: SearchConstraints
    ) -> list[ParkingSegment]:
        assert destination == DESTINATION
        self.received_constraints = search_constraints
        return list(reversed(self.values))


class LegalityService:
    def __init__(self, states: dict[str, tuple[LegalState, FreeState]]) -> None:
        self.states = states
        self.calls: list[str] = []

    def evaluate_legality(
        self, candidate: ParkingSegment, user_profile: UserProfile, datetime: datetime
    ) -> LegalityEvaluation:
        self.calls.append(candidate.segment_id)
        legal, free = self.states[candidate.segment_id]
        return LegalityEvaluation(
            evaluation_id=f"eval-{candidate.segment_id}",
            segment_id=candidate.segment_id,
            legal_state=legal,
            free_state=free,
            confidence=0.9 if legal is not LegalState.UNKNOWN else 0.0,
            evidence_refs=[f"evidence-{candidate.segment_id}"],
            reason_codes=[
                RegulationReasonCode.INSUFFICIENT_EVIDENCE
                if legal is LegalState.UNKNOWN
                else RegulationReasonCode.NO_PAYMENT_REQUIRED
            ],
            evaluated_at=datetime,
            rule_engine_version="rules-v1",
        )


class AvailabilityService:
    model_version = "availability-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def predict_availability(
        self, candidate: ParkingSegment, context: object
    ) -> AvailabilityPrediction:
        self.calls.append(candidate.segment_id)
        return AvailabilityPrediction(
            prediction_id=f"pred-{candidate.segment_id}",
            segment_id=candidate.segment_id,
            probability=0.5,
            interval=(0.3, 0.7),
            model_version=self.model_version,
            predicted_at=NOW,
            target_window_seconds=90,
        )


class MatrixProvider:
    def build_route_matrix(
        self,
        origin: GeoPoint,
        candidates: list[ParkingSegment],
        destination: Destination,
    ):
        ids = [candidate.segment_id for candidate in candidates]
        edges: dict[tuple[str, str], float] = {(ORIGIN_NODE_ID, FALLBACK_NODE_ID): 300.0}
        for index, segment_id in enumerate(ids):
            edges[(ORIGIN_NODE_ID, segment_id)] = 30.0 + index
            edges[(segment_id, FALLBACK_NODE_ID)] = 120.0
            for other_id in ids:
                if other_id != segment_id:
                    edges[(segment_id, other_id)] = 20.0
        return build_synthetic_route_matrix(
            ids,
            edges,
            origin=origin,
            destination=destination,
            fallback_location=GeoPoint(latitude=32.845, longitude=-96.78),
        )


class Repository:
    def __init__(self) -> None:
        self.executions: list[SearchExecution] = []

    def get_replay(
        self, idempotency_key_hash: str, request_hash: str
    ) -> ParkingSearchResponse | None:
        for execution in self.executions:
            if execution.idempotency_key_hash == idempotency_key_hash:
                if execution.request_hash != request_hash:
                    raise IdempotencyConflictError
                return execution.response
        return None

    def save(self, execution: SearchExecution) -> None:
        self.executions.append(execution)


def command(*, free_only: bool = True, duration: int = 60) -> ParkingSearchCommand:
    return ParkingSearchCommand(
        origin=ORIGIN,
        destination=DestinationSelector(query=DESTINATION.name),
        arrival_time=NOW,
        parking_duration_minutes=duration,
        free_only=free_only,
        max_walk_minutes=8,
        vehicle_profile=UserProfile(requested_parking_duration_min=duration),
        max_candidates=20,
    )


def orchestrator(
    candidates: list[ParkingSegment],
    states: dict[str, tuple[LegalState, FreeState]],
) -> tuple[ParkingSearchOrchestrator, AvailabilityService, Repository]:
    legality = LegalityService(states)
    availability = AvailabilityService()
    repository = Repository()
    service = ParkingSearchOrchestrator(
        destination_resolver=Resolver(),
        candidate_service=CandidateService(candidates),
        regulation_service_factory=lambda _: legality,
        availability_service=availability,
        route_matrix_provider=MatrixProvider(),
        repository=repository,
        rule_engine_version="rules-v1",
        availability_model_version="availability-v1",
        clock=lambda: NOW,
        session_id_factory=lambda: "session-1",
    )
    return service, availability, repository


def test_search_filters_fail_closed_and_persists_complete_snapshot() -> None:
    candidates = [segment("free"), segment("paid"), segment("unknown"), segment("illegal")]
    original = [candidate.model_copy(deep=True) for candidate in candidates]
    service, availability, repository = orchestrator(
        candidates,
        {
            "free": (LegalState.LEGAL, FreeState.FREE),
            "paid": (LegalState.LEGAL, FreeState.PAID),
            "unknown": (LegalState.UNKNOWN, FreeState.UNKNOWN),
            "illegal": (LegalState.ILLEGAL, FreeState.UNKNOWN),
        },
    )

    response = service.search(command(), idempotency_key="retry-1")

    assert availability.calls == ["free"]
    assert [step.segment_id for step in response.route.steps] == ["free"]
    assert response.unknown_segment_ids == ["unknown"]
    decisions = {item.segment.segment_id: item for item in response.candidate_decisions}
    assert decisions["paid"].exclusion_reason is CandidateExclusionReason.PAYMENT_NOT_FREE
    assert decisions["unknown"].exclusion_reason is CandidateExclusionReason.LEGALITY_NOT_LEGAL
    assert len(repository.executions) == 1
    assert repository.executions[0].route_matrix.binding is not None
    assert repository.executions[0].response == response
    assert candidates == original


def test_non_free_search_allows_paid_but_never_unknown_payment() -> None:
    candidates = [segment("paid"), segment("unknown-payment")]
    service, availability, _ = orchestrator(
        candidates,
        {
            "paid": (LegalState.LEGAL, FreeState.PAID),
            "unknown-payment": (LegalState.LEGAL, FreeState.UNKNOWN),
        },
    )

    response = service.search(command(free_only=False))

    assert availability.calls == ["paid"]
    assert [step.segment_id for step in response.route.steps] == ["paid"]


def test_same_idempotency_key_replays_and_conflicting_request_fails() -> None:
    candidate = segment("free")
    service, availability, repository = orchestrator(
        [candidate], {"free": (LegalState.LEGAL, FreeState.FREE)}
    )

    first = service.search(command(), idempotency_key="retry-1")
    second = service.search(command(), idempotency_key="retry-1")

    assert second == first
    assert availability.calls == ["free"]
    assert len(repository.executions) == 1
    with pytest.raises(IdempotencyConflictError):
        service.search(command(duration=61), idempotency_key="retry-1")


def test_empty_candidate_set_returns_persisted_fallback_only_route() -> None:
    service, availability, repository = orchestrator([], {})

    response = service.search(command())

    assert response.route.steps == []
    assert response.route.success_probability == 0
    assert response.route.failure_probability == 1
    assert availability.calls == []
    assert len(repository.executions) == 1
