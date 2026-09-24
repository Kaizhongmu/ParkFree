from __future__ import annotations

from datetime import UTC
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from parking_ai.domain import (
    AvailabilityPrediction,
    Destination,
    GeoPoint,
    LegalityEvaluation,
    LegalState,
    ParkingSegment,
    RouteFallback,
    RouteMatrix,
    SearchRoute,
    SearchSessionStatus,
    UserProfile,
)
from parking_ai.domain.schemas import AwareDateTime, DomainModel

SEARCH_SNAPSHOT_SCHEMA_VERSION = "search-session-snapshot-v1"


class DestinationSelector(DomainModel):
    query: str | None = Field(default=None, min_length=1, max_length=255)
    destination_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_exactly_one_selector(self) -> DestinationSelector:
        if (self.query is None) == (self.destination_id is None):
            raise ValueError("provide exactly one of destination query or destination_id")
        if self.query is not None and self.query != self.query.strip():
            raise ValueError("destination query must be trimmed")
        if self.destination_id is not None and self.destination_id != self.destination_id.strip():
            raise ValueError("destination_id must be trimmed")
        return self


class ParkingSearchCommand(DomainModel):
    origin: GeoPoint
    destination: DestinationSelector
    arrival_time: AwareDateTime
    arrival_time_was_now: bool = False
    parking_duration_minutes: int = Field(gt=0, le=1_440)
    free_only: bool = True
    max_walk_minutes: float = Field(gt=0, le=30)
    vehicle_profile: UserProfile
    max_candidates: int = Field(default=20, gt=0, le=20)

    @model_validator(mode="after")
    def validate_duration_matches_profile(self) -> ParkingSearchCommand:
        if self.vehicle_profile.requested_parking_duration_min != self.parking_duration_minutes:
            raise ValueError("vehicle profile duration must match parking_duration_minutes")
        return self


class CandidateExclusionReason(StrEnum):
    LEGALITY_NOT_LEGAL = "LEGALITY_NOT_LEGAL"
    PAYMENT_NOT_FREE = "PAYMENT_NOT_FREE"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"


class ParkingCandidateDecision(DomainModel):
    segment: ParkingSegment
    legality: LegalityEvaluation
    availability: AvailabilityPrediction | None = None
    eligible: bool
    exclusion_reason: CandidateExclusionReason | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> ParkingCandidateDecision:
        if self.legality.segment_id != self.segment.segment_id:
            raise ValueError("legality evaluation must target the decision segment")
        if (
            self.availability is not None
            and self.availability.segment_id != self.segment.segment_id
        ):
            raise ValueError("availability prediction must target the decision segment")
        if self.eligible:
            if self.exclusion_reason is not None:
                raise ValueError("eligible decisions must not have an exclusion reason")
            if self.legality.legal_state is not LegalState.LEGAL:
                raise ValueError("eligible decisions must be legally parkable")
            if self.availability is None:
                raise ValueError("eligible decisions require an availability prediction")
        elif self.exclusion_reason is None:
            raise ValueError("ineligible decisions require an exclusion reason")
        elif self.availability is not None:
            raise ValueError("ineligible decisions must not include availability predictions")
        return self


class SearchVersions(DomainModel):
    rule_engine: str = Field(min_length=1, max_length=128)
    availability_model: str = Field(min_length=1, max_length=128)
    route_matrix_id: str = Field(min_length=1, max_length=64)
    route_matrix_provider: str = Field(min_length=1, max_length=128)
    optimizer: str = Field(min_length=1, max_length=128)
    cost_model: str = Field(min_length=1, max_length=128)


class ParkingSearchResponse(DomainModel):
    session_id: str = Field(min_length=1, max_length=64)
    status: Literal[SearchSessionStatus.PLANNED] = SearchSessionStatus.PLANNED
    resolved_arrival_time: AwareDateTime
    destination: Destination
    route: SearchRoute
    fallback: RouteFallback
    candidate_decisions: list[ParkingCandidateDecision]
    unknown_segment_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    versions: SearchVersions
    replayable: Literal[True] = True

    @model_validator(mode="after")
    def validate_response_consistency(self) -> ParkingSearchResponse:
        if self.route.session_id != self.session_id:
            raise ValueError("route and response must use the same search session")
        decision_ids = [decision.segment.segment_id for decision in self.candidate_decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("candidate decision segment IDs must be unique")
        if decision_ids != sorted(decision_ids):
            raise ValueError("candidate decisions must be sorted by stable segment ID")
        expected_unknown = sorted(
            decision.segment.segment_id
            for decision in self.candidate_decisions
            if decision.legality.legal_state is LegalState.UNKNOWN
        )
        if self.unknown_segment_ids != expected_unknown:
            raise ValueError("unknown_segment_ids must exactly match unknown legality decisions")
        resolved_arrival_utc = self.resolved_arrival_time.astimezone(UTC)
        if any(
            decision.legality.evaluated_at.astimezone(UTC) != resolved_arrival_utc
            for decision in self.candidate_decisions
        ):
            raise ValueError(
                "candidate legality evaluations must match the resolved arrival instant"
            )
        if any(
            decision.legality.rule_engine_version != self.versions.rule_engine
            for decision in self.candidate_decisions
        ):
            raise ValueError("candidate rule-engine versions must match response versions")
        if any(
            decision.availability is not None
            and decision.availability.model_version != self.versions.availability_model
            for decision in self.candidate_decisions
        ):
            raise ValueError("candidate availability versions must match response versions")
        if self.route.route_matrix_version != self.versions.route_matrix_id:
            raise ValueError("route matrix ID must match response versions")
        if self.route.route_matrix_provider_version != self.versions.route_matrix_provider:
            raise ValueError("route matrix provider must match response versions")
        if self.route.optimizer_version != self.versions.optimizer:
            raise ValueError("optimizer version must match response versions")
        if self.route.cost_model_version != self.versions.cost_model:
            raise ValueError("cost model version must match response versions")
        optimizer = self.route.optimizer_snapshot
        if optimizer is not None and (
            optimizer.rule_engine_version != self.versions.rule_engine
            or optimizer.availability_model_version != self.versions.availability_model
        ):
            raise ValueError("optimizer dependency versions must match response versions")
        if self.route.fallback_description != self.fallback.description:
            raise ValueError("route fallback description must match the response fallback")
        return self


class SearchExecution(DomainModel):
    session_id: str = Field(min_length=1, max_length=64)
    request_hash: str = Field(min_length=64, max_length=64)
    idempotency_key_hash: str | None = Field(default=None, min_length=64, max_length=64)
    command: ParkingSearchCommand
    destination: Destination
    candidate_decisions: list[ParkingCandidateDecision]
    route_matrix: RouteMatrix
    route: SearchRoute
    response: ParkingSearchResponse
    artifact_hash: str = Field(min_length=64, max_length=64)
    snapshot_schema_version: str = Field(
        default=SEARCH_SNAPSHOT_SCHEMA_VERSION, min_length=1, max_length=128
    )
    created_at: AwareDateTime

    @model_validator(mode="after")
    def validate_execution(self) -> SearchExecution:
        if self.response.session_id != self.session_id or self.route.session_id != self.session_id:
            raise ValueError("execution artifacts must share one session ID")
        if self.response.destination != self.destination:
            raise ValueError("execution response must contain the resolved destination")
        if self.response.resolved_arrival_time.astimezone(
            UTC
        ) != self.command.arrival_time.astimezone(UTC):
            raise ValueError("execution response arrival must match the request command")
        if self.response.candidate_decisions != self.candidate_decisions:
            raise ValueError("execution response must contain the candidate decision snapshot")
        if self.response.route != self.route:
            raise ValueError("execution response must contain the route snapshot")
        if self.route_matrix.matrix_id != self.response.versions.route_matrix_id:
            raise ValueError("execution matrix ID must match the response")
        return self
