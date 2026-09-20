"""Transactional persistence for replayable Phase 6 search executions."""

from __future__ import annotations

from typing import cast

from geoalchemy2 import WKTElement
from geoalchemy2.elements import WKBElement
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from parking_ai.database.models import SearchRouteStepModel, SearchSessionModel
from parking_ai.domain import RouteMatrix, RouteOptimizerSnapshot, SearchRoute, SearchRouteStep
from parking_ai.orchestrator.schemas import (
    SEARCH_SNAPSHOT_SCHEMA_VERSION,
    ParkingCandidateDecision,
    ParkingSearchCommand,
    ParkingSearchResponse,
    SearchExecution,
)
from parking_ai.orchestrator.search import (
    IdempotencyConflictError,
    search_artifact_hash,
    search_request_hash,
)
from parking_ai.routing.matrix import (
    canonical_destination,
    route_matrix_content_id,
)

_DECISION_LIST_ADAPTER = TypeAdapter(list[ParkingCandidateDecision])


class SearchPersistenceError(RuntimeError):
    """Base error for a stored Phase 6 search execution."""


class SearchSessionConflictError(SearchPersistenceError):
    """Raised when a stable session or route identity is already used by another result."""


class ReplayIntegrityError(SearchPersistenceError):
    """Raised when stored replay data is incomplete, unsupported, or internally inconsistent."""


class SQLAlchemySearchSessionRepository:
    """Store and replay complete search artifacts without committing the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_replay(
        self,
        idempotency_key_hash: str,
        request_hash: str,
    ) -> ParkingSearchResponse | None:
        """Return the validated response for a matching key, or detect key reuse.

        Only hashes are accepted and stored; the repository never receives or persists the raw
        idempotency key. A key with no row returns ``None``. Reusing a known key with a different
        normalized request hash fails closed.
        """

        _require_hash(idempotency_key_hash, "idempotency_key_hash")
        _require_hash(request_hash, "request_hash")
        row = self._session.scalar(
            select(SearchSessionModel).where(
                SearchSessionModel.idempotency_key_hash == idempotency_key_hash
            )
        )
        if row is None:
            return None
        if row.request_hash != request_hash:
            raise IdempotencyConflictError(
                "idempotency key is already associated with a different request"
            )
        return _validated_replay(row)

    def save(self, execution: SearchExecution) -> None:
        """Stage a complete replayable execution and its route steps.

        The caller owns the surrounding transaction. This method deliberately calls neither
        ``commit`` nor ``rollback`` and never updates ``street_segments``.
        """

        self._validate_execution(execution)
        if execution.idempotency_key_hash is not None:
            existing_for_key = self._session.scalar(
                select(SearchSessionModel).where(
                    SearchSessionModel.idempotency_key_hash == execution.idempotency_key_hash
                )
            )
            if existing_for_key is not None:
                self._accept_exact_repeat(existing_for_key, execution, idempotency=True)
                return

        existing_for_session = self._session.get(SearchSessionModel, execution.session_id)
        if existing_for_session is not None:
            self._accept_exact_repeat(existing_for_session, execution, idempotency=False)
            return

        existing_for_route = self._session.scalar(
            select(SearchSessionModel).where(
                SearchSessionModel.route_id == execution.route.route_id
            )
        )
        if existing_for_route is not None:
            raise SearchSessionConflictError("route ID is already associated with another session")

        session_model = _session_model(execution)
        session_model.route_steps.extend(_route_step_models(execution))
        self._session.add(session_model)
        self._session.flush()

    @staticmethod
    def _validate_execution(execution: SearchExecution) -> None:
        _require_hash(execution.request_hash, "request_hash")
        _require_hash(execution.artifact_hash, "artifact_hash")
        if execution.idempotency_key_hash is not None:
            _require_hash(execution.idempotency_key_hash, "idempotency_key_hash")
        route = execution.route
        matrix = execution.route_matrix
        if execution.snapshot_schema_version != SEARCH_SNAPSHOT_SCHEMA_VERSION:
            raise ReplayIntegrityError("unsupported search snapshot schema version")
        if search_request_hash(execution.command) != execution.request_hash:
            raise ReplayIntegrityError("request hash does not match the request snapshot")
        if (
            search_artifact_hash(
                execution.candidate_decisions,
                execution.route_matrix,
                execution.route,
                execution.response,
            )
            != execution.artifact_hash
        ):
            raise ReplayIntegrityError("artifact hash does not match the replay snapshots")
        if matrix.matrix_id is None or matrix.binding is None:
            raise ReplayIntegrityError("a replayable search requires a bound route matrix")
        if route.optimizer_snapshot is None:
            raise ReplayIntegrityError("a replayable search requires an optimizer snapshot")
        if route.route_matrix_version != matrix.matrix_id:
            raise ReplayIntegrityError("route and matrix snapshot IDs do not match")
        if route.route_matrix_provider_version != matrix.provider_version:
            raise ReplayIntegrityError("route and matrix provider versions do not match")
        if matrix.binding.origin != execution.command.origin:
            raise ReplayIntegrityError("matrix origin does not match the resolved request")
        if matrix.binding.destination != canonical_destination(execution.destination):
            raise ReplayIntegrityError("matrix destination does not match the resolved destination")
        if matrix.binding.fallback != execution.response.fallback:
            raise ReplayIntegrityError("matrix fallback does not match the response")
        eligible_decision_ids = [
            decision.segment.segment_id
            for decision in execution.candidate_decisions
            if decision.eligible
        ]
        if matrix.binding.candidate_segment_ids != eligible_decision_ids:
            raise ReplayIntegrityError("matrix candidates do not match eligible decision snapshots")

    @staticmethod
    def _accept_exact_repeat(
        existing: SearchSessionModel,
        execution: SearchExecution,
        *,
        idempotency: bool,
    ) -> None:
        if (
            existing.request_hash == execution.request_hash
            and existing.artifact_hash == execution.artifact_hash
            and existing.session_id == execution.session_id
        ):
            if _validated_replay(existing) != execution.response:
                raise ReplayIntegrityError(
                    "stored response does not match the repeated search execution"
                )
            return
        if idempotency and existing.request_hash != execution.request_hash:
            raise IdempotencyConflictError(
                "idempotency key is already associated with a different request"
            )
        error_type = IdempotencyConflictError if idempotency else SearchSessionConflictError
        raise error_type("stored search identity is already associated with a different result")


def _session_model(execution: SearchExecution) -> SearchSessionModel:
    response = execution.response
    route = execution.route
    optimizer = route.optimizer_snapshot
    if optimizer is None:  # Checked before construction; this keeps static typing explicit.
        raise ReplayIntegrityError("a replayable search requires an optimizer snapshot")
    return SearchSessionModel(
        session_id=execution.session_id,
        destination_id=execution.destination.destination_id,
        origin=_point_wkt(
            execution.command.origin.longitude,
            execution.command.origin.latitude,
        ),
        requested_arrival_time=execution.command.arrival_time,
        free_only=execution.command.free_only,
        max_walk_minutes=execution.command.max_walk_minutes,
        max_candidates=execution.command.max_candidates,
        candidate_segment_ids=[
            decision.segment.segment_id for decision in execution.candidate_decisions
        ],
        status=response.status,
        rule_engine_version=response.versions.rule_engine,
        availability_model_version=response.versions.availability_model,
        route_matrix_version=response.versions.route_matrix_id,
        optimizer_version=response.versions.optimizer,
        idempotency_key_hash=execution.idempotency_key_hash,
        request_hash=execution.request_hash,
        replayable=True,
        snapshot_schema_version=execution.snapshot_schema_version,
        request_snapshot=execution.command.model_dump(mode="json"),
        candidate_decisions_snapshot=[
            decision.model_dump(mode="json") for decision in execution.candidate_decisions
        ],
        route_matrix_snapshot=execution.route_matrix.model_dump(mode="json"),
        optimizer_snapshot=optimizer.model_dump(mode="json"),
        route_id=route.route_id,
        route_snapshot=route.model_dump(mode="json"),
        response_snapshot=response.model_dump(mode="json"),
        artifact_hash=execution.artifact_hash,
        route_matrix_provider_version=response.versions.route_matrix_provider,
        created_at=execution.created_at,
    )


def _route_step_models(execution: SearchExecution) -> list[SearchRouteStepModel]:
    snapshots = {
        snapshot.segment_id: snapshot for snapshot in execution.route.selected_candidate_snapshots
    }
    models: list[SearchRouteStepModel] = []
    for step in execution.route.steps:
        snapshot = snapshots.get(step.segment_id)
        if snapshot is None:
            raise ReplayIntegrityError(
                f"route step {step.segment_id!r} is missing its decision snapshot"
            )
        models.append(
            SearchRouteStepModel(
                route_step_id=step.route_step_id,
                session_id=execution.session_id,
                segment_id=step.segment_id,
                step_order=step.step_order,
                legal_state=step.legal_state,
                free_state=step.free_state,
                legal_confidence=step.legal_confidence,
                availability_probability=step.availability_probability,
                drive_eta_min=step.drive_eta_min,
                walk_min=step.walk_min,
                evidence_refs=list(step.evidence_refs),
                rule_engine_version=step.rule_engine_version,
                availability_model_version=step.availability_model_version,
                legality_evaluation_id=snapshot.legality_evaluation_id,
                availability_prediction_id=snapshot.availability_prediction_id,
                availability_target_window_seconds=(snapshot.availability_target_window_seconds),
            )
        )
    return models


def _validated_replay(row: SearchSessionModel) -> ParkingSearchResponse:
    if not row.replayable:
        raise ReplayIntegrityError("stored search session is not replayable")
    if row.snapshot_schema_version != SEARCH_SNAPSHOT_SCHEMA_VERSION:
        raise ReplayIntegrityError("stored search uses an unsupported snapshot schema version")
    required_snapshots = (
        row.request_snapshot,
        row.candidate_decisions_snapshot,
        row.route_matrix_snapshot,
        row.optimizer_snapshot,
        row.route_snapshot,
        row.response_snapshot,
    )
    if any(snapshot is None for snapshot in required_snapshots):
        raise ReplayIntegrityError("stored search session has incomplete replay snapshots")
    try:
        command = ParkingSearchCommand.model_validate(row.request_snapshot)
        decisions = _DECISION_LIST_ADAPTER.validate_python(row.candidate_decisions_snapshot)
        matrix = RouteMatrix.model_validate(row.route_matrix_snapshot)
        optimizer = RouteOptimizerSnapshot.model_validate(row.optimizer_snapshot)
        route = SearchRoute.model_validate(row.route_snapshot)
        response = ParkingSearchResponse.model_validate(row.response_snapshot)
    except ValidationError as error:
        raise ReplayIntegrityError("stored search snapshot failed schema validation") from error
    if matrix.matrix_id is None or matrix.binding is None:
        raise ReplayIntegrityError("stored route matrix is not request-bound")
    if route_matrix_content_id(matrix) != matrix.matrix_id:
        raise ReplayIntegrityError("stored route matrix content hash is invalid")
    if route.optimizer_snapshot != optimizer:
        raise ReplayIntegrityError("stored optimizer snapshots are inconsistent")
    if response.route != route or response.candidate_decisions != decisions:
        raise ReplayIntegrityError("stored response does not match decision and route snapshots")
    if matrix.binding.origin != command.origin:
        raise ReplayIntegrityError("stored route matrix origin does not match the request")
    if matrix.binding.destination != canonical_destination(response.destination):
        raise ReplayIntegrityError("stored route matrix destination does not match the response")
    if matrix.binding.fallback != response.fallback:
        raise ReplayIntegrityError("stored route matrix fallback does not match the response")
    eligible_ids = [decision.segment.segment_id for decision in decisions if decision.eligible]
    if matrix.binding.candidate_segment_ids != eligible_ids:
        raise ReplayIntegrityError("stored route matrix candidates do not match decisions")
    if row.request_hash is None or search_request_hash(command) != row.request_hash:
        raise ReplayIntegrityError("stored request hash is invalid")
    if (
        row.artifact_hash is None
        or search_artifact_hash(decisions, matrix, route, response) != row.artifact_hash
    ):
        raise ReplayIntegrityError("stored artifact hash is invalid")
    if (
        row.session_id != response.session_id
        or row.destination_id != response.destination.destination_id
        or row.requested_arrival_time != command.arrival_time
        or row.free_only != command.free_only
        or row.max_walk_minutes != command.max_walk_minutes
        or row.max_candidates != command.max_candidates
        or row.candidate_segment_ids != [decision.segment.segment_id for decision in decisions]
        or row.status != response.status
        or row.route_id != route.route_id
        or row.route_matrix_version != response.versions.route_matrix_id
        or row.route_matrix_provider_version != response.versions.route_matrix_provider
        or row.rule_engine_version != response.versions.rule_engine
        or row.availability_model_version != response.versions.availability_model
        or row.optimizer_version != response.versions.optimizer
    ):
        raise ReplayIntegrityError("stored search snapshots or versions are inconsistent")
    if route.route_matrix_version != matrix.matrix_id:
        raise ReplayIntegrityError("stored route and matrix IDs are inconsistent")
    if route.route_matrix_provider_version != matrix.provider_version:
        raise ReplayIntegrityError("stored route and matrix providers are inconsistent")
    if _stored_route_step_snapshot(row) != route.steps:
        raise ReplayIntegrityError("stored route-step rows do not match the route snapshot")
    return response


def _stored_route_step_snapshot(row: SearchSessionModel) -> list[SearchRouteStep]:
    """Hydrate route-step rows through the domain schema for integrity comparison."""

    return [
        SearchRouteStep(
            route_step_id=step.route_step_id,
            session_id=step.session_id,
            segment_id=step.segment_id,
            step_order=step.step_order,
            legal_state=step.legal_state,
            free_state=step.free_state,
            legal_confidence=step.legal_confidence,
            availability_probability=step.availability_probability,
            drive_eta_min=step.drive_eta_min,
            walk_min=step.walk_min,
            evidence_refs=step.evidence_refs,
            rule_engine_version=step.rule_engine_version,
            availability_model_version=step.availability_model_version,
        )
        for step in row.route_steps
    ]


def _point_wkt(longitude: float, latitude: float) -> WKBElement:
    return cast(WKBElement, WKTElement(f"POINT({longitude} {latitude})", srid=4326))


def _require_hash(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hexadecimal digest")
