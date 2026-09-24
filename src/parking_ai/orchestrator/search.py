from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import uuid4

from parking_ai.domain import (
    AvailabilityContext,
    Destination,
    FreeState,
    LegalState,
    ParkingSegment,
    RouteCandidateSnapshot,
    RouteMatrix,
    RouteOptimizationStrategy,
    SearchConstraints,
    SearchRoute,
)
from parking_ai.domain.interfaces import (
    AvailabilityService,
    CandidateSegmentService,
    LegalityService,
    RouteMatrixProvider,
    SearchRoutePlanner,
)
from parking_ai.orchestrator.schemas import (
    CandidateExclusionReason,
    ParkingCandidateDecision,
    ParkingSearchCommand,
    ParkingSearchResponse,
    SearchExecution,
    SearchVersions,
)
from parking_ai.routing import (
    DeterministicSearchRoutePlanner,
    RouteOptimizerConfig,
    RoutePlanningContext,
)


class DestinationNotFoundError(LookupError):
    """Raised when a configured destination cannot be resolved."""


class IdempotencyConflictError(ValueError):
    """Raised when one idempotency key is reused for a different request."""


class SearchUnavailableError(RuntimeError):
    """Raised when a required search dependency cannot safely serve a request."""


class SearchConfigurationError(SearchUnavailableError):
    """Raised when required Phase 6 runtime configuration is absent."""


class DestinationResolver(Protocol):
    def resolve_destination(
        self,
        *,
        query: str | None,
        destination_id: str | None,
    ) -> Destination | None: ...


class RegulationServiceFactory(Protocol):
    def __call__(self, candidate_segment_ids: Sequence[str]) -> LegalityService: ...


class SearchSessionRepository(Protocol):
    def get_replay(
        self,
        idempotency_key_hash: str,
        request_hash: str,
    ) -> ParkingSearchResponse | None: ...

    def save(self, execution: SearchExecution) -> None: ...


PlannerFactory = Callable[[RoutePlanningContext], SearchRoutePlanner]
Clock = Callable[[], dt.datetime]
SessionIdFactory = Callable[[], str]


class ParkingSearchOrchestrator:
    """Compose the deterministic Phase 0-5 services for one search request."""

    def __init__(
        self,
        *,
        destination_resolver: DestinationResolver,
        candidate_service: CandidateSegmentService,
        regulation_service_factory: RegulationServiceFactory,
        availability_service: AvailabilityService,
        route_matrix_provider: RouteMatrixProvider,
        repository: SearchSessionRepository,
        rule_engine_version: str,
        availability_model_version: str,
        availability_target_window_seconds: int = 90,
        planner_config: RouteOptimizerConfig | None = None,
        planner_factory: PlannerFactory | None = None,
        clock: Clock | None = None,
        session_id_factory: SessionIdFactory | None = None,
        warnings: Sequence[str] = (),
    ) -> None:
        if not rule_engine_version.strip() or not availability_model_version.strip():
            raise ValueError("search dependency versions must not be blank")
        if availability_target_window_seconds <= 0:
            raise ValueError("availability target window must be positive")
        self._destination_resolver = destination_resolver
        self._candidate_service = candidate_service
        self._regulation_service_factory = regulation_service_factory
        self._availability_service = availability_service
        self._route_matrix_provider = route_matrix_provider
        self._repository = repository
        self._rule_engine_version = rule_engine_version
        self._availability_model_version = availability_model_version
        self._availability_target_window_seconds = availability_target_window_seconds
        self._planner_config = planner_config or RouteOptimizerConfig(
            strategy=RouteOptimizationStrategy.GREEDY,
            local_search_seconds=float(availability_target_window_seconds),
        )
        if self._planner_config.candidate_limit < 1:
            raise ValueError("planner candidate limit must be positive")
        self._planner_factory = planner_factory or self._default_planner_factory
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self._session_id_factory = session_id_factory or (lambda: f"session_{uuid4().hex}")
        self._warnings = tuple(sorted(set(warnings)))

    def search(
        self,
        command: ParkingSearchCommand,
        idempotency_key: str | None = None,
    ) -> ParkingSearchResponse:
        request_hash = search_request_hash(command)
        idempotency_key_hash = _idempotency_key_hash(idempotency_key)
        if idempotency_key_hash is not None:
            replay = self._repository.get_replay(idempotency_key_hash, request_hash)
            if replay is not None:
                return replay

        if command.max_candidates > self._planner_config.candidate_limit:
            raise SearchConfigurationError(
                "request candidate limit exceeds the configured optimizer limit"
            )
        created_at = _aware_utc(self._clock(), "clock result")
        session_id = self._session_id_factory()
        destination = self._destination_resolver.resolve_destination(
            query=command.destination.query,
            destination_id=command.destination.destination_id,
        )
        if destination is None:
            raise DestinationNotFoundError("configured destination was not found")

        constraints = SearchConstraints(
            free_only=command.free_only,
            max_walk_minutes=command.max_walk_minutes,
            max_candidates=command.max_candidates,
        )
        candidates = self._candidate_service.get_candidate_segments(destination, constraints)
        candidates_by_id = _validated_candidates(candidates)
        ordered_candidates = [candidates_by_id[item] for item in sorted(candidates_by_id)]
        legality_service = self._regulation_service_factory(
            [candidate.segment_id for candidate in ordered_candidates]
        )

        decisions: list[ParkingCandidateDecision] = []
        eligible_segments: list[ParkingSegment] = []
        decision_snapshots: list[RouteCandidateSnapshot] = []
        for segment in ordered_candidates:
            legality = legality_service.evaluate_legality(
                segment,
                command.vehicle_profile,
                command.arrival_time,
            )
            if legality.segment_id != segment.segment_id:
                raise SearchUnavailableError(
                    "regulation service returned a mismatched segment evaluation"
                )
            if legality.rule_engine_version != self._rule_engine_version:
                raise SearchUnavailableError(
                    "regulation service returned an unexpected engine version"
                )
            if legality.evaluated_at.astimezone(dt.UTC) != command.arrival_time.astimezone(dt.UTC):
                raise SearchUnavailableError(
                    "regulation service returned an evaluation for a different instant"
                )
            exclusion = _exclusion_reason(
                legality.legal_state, legality.free_state, command.free_only
            )
            if exclusion is not None:
                decisions.append(
                    ParkingCandidateDecision(
                        segment=segment.model_copy(deep=True),
                        legality=legality,
                        eligible=False,
                        exclusion_reason=exclusion,
                    )
                )
                continue

            prediction = self._availability_service.predict_availability(
                segment,
                AvailabilityContext(
                    arrival_time=command.arrival_time,
                    search_window_seconds=self._availability_target_window_seconds,
                ),
            )
            if prediction.segment_id != segment.segment_id:
                raise SearchUnavailableError(
                    "availability service returned a mismatched segment prediction"
                )
            if prediction.model_version != self._availability_model_version:
                raise SearchUnavailableError(
                    "availability service returned an unexpected model version"
                )
            if prediction.target_window_seconds != self._availability_target_window_seconds:
                raise SearchUnavailableError(
                    "availability service returned an unexpected target window"
                )
            enriched = segment.model_copy(
                update={
                    "legal_state": legality.legal_state,
                    "free_state": legality.free_state,
                    "legal_confidence": legality.confidence,
                    "availability_probability": prediction.probability,
                    "availability_interval": prediction.interval,
                    "evidence_refs": sorted(legality.evidence_refs),
                },
                deep=True,
            )
            snapshot = RouteCandidateSnapshot.from_results(legality, prediction)
            eligible_segments.append(enriched)
            decision_snapshots.append(snapshot)
            decisions.append(
                ParkingCandidateDecision(
                    segment=segment.model_copy(deep=True),
                    legality=legality,
                    availability=prediction,
                    eligible=True,
                )
            )

        route_matrix = self._route_matrix_provider.build_route_matrix(
            command.origin,
            eligible_segments,
            destination,
        )
        if route_matrix.binding is None or route_matrix.matrix_id is None:
            raise SearchUnavailableError("route matrix provider returned an unbound matrix")
        context = RoutePlanningContext(
            session_id=session_id,
            rule_engine_version=self._rule_engine_version,
            availability_model_version=self._availability_model_version,
            availability_target_window_seconds=self._availability_target_window_seconds,
            require_free=command.free_only,
            candidate_snapshots=tuple(decision_snapshots),
        )
        planner = self._planner_factory(context)
        route = planner.plan_search_route(
            eligible_segments,
            command.origin,
            destination,
            route_matrix,
        )
        if route.optimizer_snapshot is None or route.cost_model_version is None:
            raise SearchUnavailableError("route planner omitted required Phase 6 diagnostics")
        _validate_planner_output(
            route,
            expected_session_id=session_id,
            expected_snapshots=decision_snapshots,
            route_matrix=route_matrix,
            context=context,
        )

        unknown_ids = sorted(
            decision.segment.segment_id
            for decision in decisions
            if decision.legality.legal_state is LegalState.UNKNOWN
        )
        warnings = list(self._warnings)
        if unknown_ids:
            warnings.append("VERIFY_SIGNAGE_FOR_UNKNOWN_SEGMENTS")
        response = ParkingSearchResponse(
            session_id=session_id,
            resolved_arrival_time=command.arrival_time,
            destination=destination,
            route=route,
            fallback=route_matrix.binding.fallback,
            candidate_decisions=decisions,
            unknown_segment_ids=unknown_ids,
            warnings=sorted(set(warnings)),
            versions=SearchVersions(
                rule_engine=self._rule_engine_version,
                availability_model=self._availability_model_version,
                route_matrix_id=route_matrix.matrix_id,
                route_matrix_provider=route_matrix.provider_version,
                optimizer=route.optimizer_version,
                cost_model=route.cost_model_version,
            ),
        )
        artifact_hash = search_artifact_hash(decisions, route_matrix, route, response)
        execution = SearchExecution(
            session_id=session_id,
            request_hash=request_hash,
            idempotency_key_hash=idempotency_key_hash,
            command=command,
            destination=destination,
            candidate_decisions=decisions,
            route_matrix=route_matrix,
            route=route,
            response=response,
            artifact_hash=artifact_hash,
            created_at=created_at,
        )
        self._repository.save(execution)
        return response

    def _default_planner_factory(self, context: RoutePlanningContext) -> SearchRoutePlanner:
        return DeterministicSearchRoutePlanner(context, config=self._planner_config)


def _validated_candidates(candidates: Sequence[ParkingSegment]) -> dict[str, ParkingSegment]:
    by_id: dict[str, ParkingSegment] = {}
    for candidate in candidates:
        if not candidate.segment_id.strip() or candidate.segment_id != candidate.segment_id.strip():
            raise SearchUnavailableError("candidate service returned an invalid stable ID")
        if candidate.segment_id in by_id:
            raise SearchUnavailableError("candidate service returned duplicate stable IDs")
        by_id[candidate.segment_id] = candidate.model_copy(deep=True)
    return by_id


def _validate_planner_output(
    route: SearchRoute,
    *,
    expected_session_id: str,
    expected_snapshots: Sequence[RouteCandidateSnapshot],
    route_matrix: RouteMatrix,
    context: RoutePlanningContext,
) -> None:
    binding = route_matrix.binding
    if binding is None:
        raise SearchUnavailableError("route matrix provider returned an unbound matrix")
    expected_by_id = {snapshot.segment_id: snapshot for snapshot in expected_snapshots}
    selected: list[RouteCandidateSnapshot] = []
    for step in route.steps:
        expected = expected_by_id.get(step.segment_id)
        if expected is None:
            raise SearchUnavailableError("route planner selected an ineligible candidate")
        selected.append(expected)
    optimizer = route.optimizer_snapshot
    if (
        route.session_id != expected_session_id
        or route.selected_candidate_snapshots != selected
        or route.route_matrix_version != route_matrix.matrix_id
        or route.route_matrix_provider_version != route_matrix.provider_version
        or route.fallback_description != binding.fallback.description
        or optimizer is None
        or optimizer.rule_engine_version != context.rule_engine_version
        or optimizer.availability_model_version != context.availability_model_version
        or optimizer.availability_target_window_seconds
        != context.availability_target_window_seconds
        or optimizer.require_free is not context.require_free
    ):
        raise SearchUnavailableError("route planner returned inconsistent decision artifacts")


def _exclusion_reason(
    legal_state: LegalState,
    free_state: FreeState,
    free_only: bool,
) -> CandidateExclusionReason | None:
    if legal_state is not LegalState.LEGAL:
        return CandidateExclusionReason.LEGALITY_NOT_LEGAL
    if free_state is FreeState.UNKNOWN:
        return CandidateExclusionReason.PAYMENT_UNKNOWN
    if free_only and free_state is not FreeState.FREE:
        return CandidateExclusionReason.PAYMENT_NOT_FREE
    return None


def search_request_hash(command: ParkingSearchCommand) -> str:
    """Return the stable hash for the logical request represented by ``command``."""

    payload = command.model_dump(mode="json")
    if command.arrival_time_was_now:
        payload["arrival_time"] = "now"
    return _canonical_hash(payload)


def _idempotency_key_hash(idempotency_key: str | None) -> str | None:
    if idempotency_key is None:
        return None
    if not idempotency_key.strip() or idempotency_key != idempotency_key.strip():
        raise ValueError("idempotency key must be nonblank and trimmed")
    if len(idempotency_key) > 128:
        raise ValueError("idempotency key must not exceed 128 characters")
    return hashlib.sha256(idempotency_key.encode()).hexdigest()


def search_artifact_hash(
    decisions: Sequence[ParkingCandidateDecision],
    route_matrix: RouteMatrix,
    route: SearchRoute,
    response: ParkingSearchResponse,
) -> str:
    """Return the content hash covering every replayed decision artifact."""

    payload = {
        "candidate_decisions": [item.model_dump(mode="json") for item in decisions],
        "route_matrix": route_matrix.model_dump(mode="json"),
        "route": route.model_dump(mode="json"),
        "response": response.model_dump(mode="json"),
    }
    return _canonical_hash(payload)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _aware_utc(value: dt.datetime, label: str) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include timezone information")
    return value.astimezone(dt.UTC)


__all__ = [
    "DestinationNotFoundError",
    "DestinationResolver",
    "IdempotencyConflictError",
    "ParkingSearchOrchestrator",
    "RegulationServiceFactory",
    "SearchConfigurationError",
    "SearchSessionRepository",
    "SearchUnavailableError",
]
