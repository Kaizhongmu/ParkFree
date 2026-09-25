import math
from datetime import date, datetime, time
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from parking_ai.domain.enums import (
    AvailabilityCapacitySource,
    AvailabilityObservationScope,
    AvailabilityReasonCode,
    AvailabilityTimeBucket,
    DayOfWeek,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalState,
    ParkingRuleType,
    PhysicalState,
    RegulationReasonCode,
    RouteOptimizationStrategy,
    SearchSessionStatus,
    SegmentSide,
)
from parking_ai.domain.evidence_policy import evidence_reliability_within_source_authority
from parking_ai.domain.geometry import GeoPoint, LineStringGeometry


def _new_id() -> str:
    return str(uuid4())


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value


AwareDateTime = Annotated[datetime, AfterValidator(_require_timezone)]
Probability = Annotated[float, Field(ge=0, le=1)]
NonNegativeFloat = Annotated[float, Field(ge=0)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DestinationAccessPoint(DomainModel):
    access_point_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    destination_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=255)
    location: GeoPoint


class Destination(DomainModel):
    destination_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    location: GeoPoint
    destination_type: str | None = Field(default=None, max_length=64)
    access_points: list[DestinationAccessPoint] = Field(default_factory=list)


class ParkingSegment(DomainModel):
    segment_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    geometry: LineStringGeometry
    street_name: str | None = Field(default=None, max_length=255)
    side: SegmentSide = SegmentSide.UNKNOWN
    length_m: float = Field(gt=0)
    estimated_capacity: float | None = Field(default=None, ge=0)
    road_type: str | None = Field(default=None, max_length=64)
    physical_state: PhysicalState = PhysicalState.UNKNOWN
    regulation_refs: list[str] = Field(default_factory=list)
    legal_state: LegalState = LegalState.UNKNOWN
    free_state: FreeState = FreeState.UNKNOWN
    legal_confidence: Probability = 0.0
    availability_probability: Probability | None = None
    availability_interval: tuple[Probability, Probability] | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    data_freshness: AwareDateTime

    @model_validator(mode="after")
    def validate_availability_interval(self) -> "ParkingSegment":
        if self.availability_interval is None:
            return self
        lower, upper = self.availability_interval
        if lower > upper:
            raise ValueError("availability interval lower bound cannot exceed upper bound")
        if (
            self.availability_probability is not None
            and not lower <= self.availability_probability <= upper
        ):
            raise ValueError("availability probability must fall within its interval")
        return self


class RuleException(DomainModel):
    exception_type: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ParkingRule(DomainModel):
    rule_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    segment_id: str = Field(min_length=1, max_length=64)
    rule_type: ParkingRuleType
    days: list[DayOfWeek] = Field(default_factory=list)
    start_time: time | None = None
    end_time: time | None = None
    effective_start_date: date | None = None
    effective_end_date: date | None = None
    max_duration_min: int | None = Field(default=None, gt=0)
    payment_required: bool | None = None
    permit_required: bool | None = None
    permit_type: str | None = Field(default=None, max_length=128)
    exceptions: list[RuleException] = Field(default_factory=list)
    source_evidence_id: str = Field(min_length=1, max_length=64)
    extraction_confidence: Probability

    @model_validator(mode="after")
    def validate_date_range(self) -> "ParkingRule":
        if (
            self.effective_start_date is not None
            and self.effective_end_date is not None
            and self.effective_start_date > self.effective_end_date
        ):
            raise ValueError("effective start date cannot be after effective end date")
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start time and end time must both be set or both be omitted")
        if self.start_time is not None and self.end_time is not None:
            if self.start_time.tzinfo is not None or self.end_time.tzinfo is not None:
                raise ValueError("rule wall times must not include timezone information")
            if self.start_time == self.end_time:
                raise ValueError("equal start and end times are ambiguous; omit both for all-day")
        if len(self.days) != len(set(self.days)):
            raise ValueError("rule days must not contain duplicates")
        return self


class NormalizedClaim(DomainModel):
    claim_type: str = Field(min_length=1, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)


class Evidence(DomainModel):
    evidence_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    source_type: EvidenceSourceType
    source_uri_or_identifier: str = Field(min_length=1, max_length=2048)
    publisher: str | None = Field(default=None, max_length=255)
    published_at: AwareDateTime | None = None
    observed_at: AwareDateTime | None = None
    retrieved_at: AwareDateTime
    raw_storage_policy: EvidenceStoragePolicy
    segment_ids: list[str] = Field(default_factory=list)
    normalized_claims: list[NormalizedClaim] = Field(default_factory=list)
    reliability_tier: EvidenceReliabilityTier
    extractor_version: str | None = Field(default=None, max_length=128)
    content_hash: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_integrity(self) -> "Evidence":
        if self.published_at is not None and self.published_at > self.retrieved_at:
            raise ValueError("published_at cannot be after retrieved_at")
        if self.observed_at is not None and self.observed_at > self.retrieved_at:
            raise ValueError("observed_at cannot be after retrieved_at")
        if not evidence_reliability_within_source_authority(
            self.source_type,
            self.reliability_tier,
        ):
            raise ValueError("evidence reliability tier exceeds its source authority ceiling")
        return self


class LegalityEvaluation(DomainModel):
    evaluation_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    segment_id: str = Field(min_length=1, max_length=64)
    legal_state: LegalState
    free_state: FreeState
    max_duration_min: int | None = Field(default=None, gt=0)
    confidence: Probability
    evidence_refs: list[str] = Field(default_factory=list)
    reason_codes: list[RegulationReasonCode] = Field(min_length=1)
    evaluated_at: AwareDateTime
    rule_engine_version: str = Field(min_length=1, max_length=128)


class AvailabilityObservationSummary(DomainModel):
    segment_id: str = Field(min_length=1, max_length=64)
    scope: AvailabilityObservationScope = AvailabilityObservationScope.SEGMENT_TIME_BUCKET
    time_bucket: AvailabilityTimeBucket
    aggregation_version: str = Field(default="segment-time-bucket-v1", min_length=1, max_length=128)
    successes: int = Field(ge=0)
    trials: int = Field(ge=0)
    target_window_seconds: int = Field(default=90, gt=0)
    as_of: AwareDateTime

    @model_validator(mode="after")
    def validate_counts(self) -> "AvailabilityObservationSummary":
        if self.successes > self.trials:
            raise ValueError("availability successes cannot exceed trials")
        return self


class AvailabilityFeatureSnapshot(DomainModel):
    feature_schema_version: str = Field(min_length=1, max_length=128)
    arrival_time_utc: AwareDateTime
    local_timezone: str = Field(min_length=1, max_length=128)
    local_weekday: DayOfWeek
    local_hour: int = Field(ge=0, le=23)
    local_utc_offset_minutes: int
    local_fold: int = Field(ge=0, le=1)
    time_bucket: AvailabilityTimeBucket
    search_window_seconds: int = Field(gt=0)
    segment_length_m: float = Field(gt=0)
    effective_capacity: float = Field(ge=0)
    capacity_source: AvailabilityCapacitySource
    road_type_bucket: str = Field(min_length=1, max_length=64)
    physical_state: PhysicalState
    observation_successes: int = Field(ge=0)
    observation_trials: int = Field(ge=0)
    observation_as_of: AwareDateTime | None = None
    observation_scope: AvailabilityObservationScope | None = None
    observation_aggregation_version: str | None = Field(default=None, min_length=1, max_length=128)
    prior_90_probability: Probability
    prior_strength: float = Field(gt=0)


class AvailabilityPrediction(DomainModel):
    prediction_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    segment_id: str = Field(min_length=1, max_length=64)
    probability: Probability
    interval: tuple[Probability, Probability] | None = None
    model_version: str = Field(min_length=1, max_length=128)
    predicted_at: AwareDateTime
    target_window_seconds: int | None = Field(default=None, gt=0)
    feature_snapshot: AvailabilityFeatureSnapshot | None = None
    uncertainty_method: str | None = Field(default=None, min_length=1, max_length=128)
    reason_codes: list[AvailabilityReasonCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_prediction_consistency(self) -> "AvailabilityPrediction":
        if self.interval is not None:
            lower, upper = self.interval
            if lower > upper:
                raise ValueError("uncertainty interval lower bound cannot exceed upper bound")
            if not lower <= self.probability <= upper:
                raise ValueError("probability must fall within its uncertainty interval")
        if (
            self.target_window_seconds is not None
            and self.feature_snapshot is not None
            and self.target_window_seconds != self.feature_snapshot.search_window_seconds
        ):
            raise ValueError(
                "availability target window must match the feature snapshot search window"
            )
        return self


class RouteCandidateSnapshot(DomainModel):
    segment_id: str = Field(min_length=1, max_length=64)
    legality_evaluation_id: str = Field(min_length=1, max_length=64)
    legal_state: LegalState
    free_state: FreeState
    legal_confidence: Probability
    evidence_refs: list[str] = Field(default_factory=list)
    rule_engine_version: str = Field(min_length=1, max_length=128)
    availability_prediction_id: str = Field(min_length=1, max_length=64)
    availability_probability: Probability
    availability_model_version: str = Field(min_length=1, max_length=128)
    availability_target_window_seconds: int = Field(gt=0)

    @classmethod
    def from_results(
        cls,
        legality: LegalityEvaluation,
        availability: AvailabilityPrediction,
    ) -> "RouteCandidateSnapshot":
        if legality.segment_id != availability.segment_id:
            raise ValueError("legality and availability results must target the same segment")
        if availability.target_window_seconds is None:
            raise ValueError("availability result must declare its target window")
        return cls(
            segment_id=legality.segment_id,
            legality_evaluation_id=legality.evaluation_id,
            legal_state=legality.legal_state,
            free_state=legality.free_state,
            legal_confidence=legality.confidence,
            evidence_refs=legality.evidence_refs,
            rule_engine_version=legality.rule_engine_version,
            availability_prediction_id=availability.prediction_id,
            availability_probability=availability.probability,
            availability_model_version=availability.model_version,
            availability_target_window_seconds=availability.target_window_seconds,
        )


class SearchRouteStep(DomainModel):
    route_step_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    segment_id: str = Field(min_length=1, max_length=64)
    step_order: int = Field(ge=0)
    legal_state: LegalState
    free_state: FreeState
    legal_confidence: Probability
    availability_probability: Probability | None = None
    drive_eta_min: NonNegativeFloat
    walk_min: NonNegativeFloat
    evidence_refs: list[str] = Field(default_factory=list)
    rule_engine_version: str = Field(min_length=1, max_length=128)
    availability_model_version: str | None = Field(default=None, max_length=128)


class RouteOptimizerSnapshot(DomainModel):
    optimization_strategy: RouteOptimizationStrategy
    optimizer_version: str = Field(min_length=1, max_length=128)
    cost_model_version: str = Field(min_length=1, max_length=128)
    rule_engine_version: str = Field(min_length=1, max_length=128)
    availability_model_version: str = Field(min_length=1, max_length=128)
    availability_target_window_seconds: int = Field(gt=0)
    require_free: bool
    local_search_seconds: float = Field(gt=0)
    fallback_service_seconds: NonNegativeFloat
    walking_speed_m_per_min: float = Field(gt=0)
    beam_width: int = Field(gt=0)
    max_route_steps: int | None = Field(default=None, gt=0)
    candidate_limit: int = Field(gt=0)


class SearchRoute(DomainModel):
    route_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    steps: list[SearchRouteStep]
    expected_time_to_park_min: NonNegativeFloat
    success_probability: Probability
    fallback_description: str | None = Field(default=None, max_length=500)
    optimizer_version: str = Field(min_length=1, max_length=128)
    failure_probability: Probability | None = None
    fallback_time_min: NonNegativeFloat | None = None
    fallback_expected_contribution_min: NonNegativeFloat | None = None
    route_matrix_version: str | None = Field(default=None, min_length=1, max_length=128)
    route_matrix_provider_version: str | None = Field(default=None, min_length=1, max_length=128)
    cost_model_version: str | None = Field(default=None, min_length=1, max_length=128)
    optimization_strategy: RouteOptimizationStrategy | None = None
    optimizer_snapshot: RouteOptimizerSnapshot | None = None
    selected_candidate_snapshots: list[RouteCandidateSnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_route_consistency(self) -> "SearchRoute":
        if [step.step_order for step in self.steps] != list(range(len(self.steps))):
            raise ValueError("route step orders must be unique and contiguous from zero")
        if any(step.session_id != self.session_id for step in self.steps):
            raise ValueError("every route step must belong to the route session")
        segment_ids = [step.segment_id for step in self.steps]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("a route must not visit the same segment more than once")
        if self.failure_probability is not None and not math.isclose(
            self.success_probability + self.failure_probability,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("route success and failure probabilities must sum to one")
        snapshots = self.selected_candidate_snapshots
        if snapshots:
            if self.optimizer_snapshot is None:
                raise ValueError("selected candidate snapshots require an optimizer snapshot")
            if len(snapshots) != len(self.steps):
                raise ValueError("selected candidate snapshots must align with route steps")
            for step, snapshot in zip(self.steps, snapshots, strict=True):
                if step.segment_id != snapshot.segment_id:
                    raise ValueError("selected candidate snapshot order must match route steps")
                if (
                    step.legal_state is not snapshot.legal_state
                    or step.free_state is not snapshot.free_state
                    or not math.isclose(
                        step.legal_confidence,
                        snapshot.legal_confidence,
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    or step.evidence_refs != snapshot.evidence_refs
                    or step.rule_engine_version != snapshot.rule_engine_version
                    or step.availability_model_version != snapshot.availability_model_version
                    or step.availability_probability is None
                    or not math.isclose(
                        step.availability_probability,
                        snapshot.availability_probability,
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                ):
                    raise ValueError(
                        "route step decision values must match its selected candidate snapshot"
                    )
        elif self.steps and self.optimizer_snapshot is not None:
            raise ValueError("phase-five route steps require selected candidate snapshots")

        if self.optimizer_snapshot is not None:
            optimizer_snapshot = self.optimizer_snapshot
            if (
                self.optimization_strategy is None
                or self.cost_model_version is None
                or self.optimizer_version != optimizer_snapshot.optimizer_version
                or self.cost_model_version != optimizer_snapshot.cost_model_version
                or self.optimization_strategy is not optimizer_snapshot.optimization_strategy
            ):
                raise ValueError(
                    "optimizer snapshot must match the route's top-level optimizer metadata"
                )
            if any(
                snapshot.rule_engine_version != optimizer_snapshot.rule_engine_version
                or snapshot.availability_model_version
                != optimizer_snapshot.availability_model_version
                or snapshot.availability_target_window_seconds
                != optimizer_snapshot.availability_target_window_seconds
                for snapshot in snapshots
            ):
                raise ValueError(
                    "candidate snapshots must match the optimizer's rule, model, and window"
                )
        return self


class SearchConstraints(DomainModel):
    free_only: bool = True
    max_walk_minutes: float = Field(gt=0)
    max_candidates: int = Field(default=20, gt=0)


class UserProfile(DomainModel):
    vehicle_type: str = Field(default="passenger", min_length=1, max_length=64)
    permit_types: list[str] = Field(default_factory=list)
    requested_parking_duration_min: int | None = Field(default=None, gt=0)


class AvailabilityContext(DomainModel):
    arrival_time: AwareDateTime
    search_window_seconds: int = Field(default=90, gt=0)
    features: dict[str, bool | int | float | str | None] = Field(default_factory=dict)
    observation_summary: AvailabilityObservationSummary | None = None


class AvailabilityEvaluationRecord(DomainModel):
    outcome_id: str = Field(min_length=1, max_length=64)
    prediction_id: str = Field(min_length=1, max_length=64)
    segment_id: str = Field(min_length=1, max_length=64)
    model_version: str = Field(min_length=1, max_length=128)
    feature_schema_version: str = Field(min_length=1, max_length=128)
    target_window_seconds: int = Field(gt=0)
    probability: Probability
    outcome: bool
    interval: tuple[Probability, Probability] | None = None

    @model_validator(mode="after")
    def validate_evaluation_interval(self) -> "AvailabilityEvaluationRecord":
        if self.interval is None:
            return self
        lower, upper = self.interval
        if lower > upper:
            raise ValueError("evaluation interval lower bound cannot exceed upper bound")
        if not lower <= self.probability <= upper:
            raise ValueError("evaluation probability must fall within its interval")
        return self


class AvailabilityCalibrationBin(DomainModel):
    bin_index: int = Field(ge=0)
    lower_bound: Probability
    upper_bound: Probability
    count: int = Field(ge=0)
    mean_probability: Probability | None = None
    observed_rate: Probability | None = None


class AvailabilityEvaluationReport(DomainModel):
    sample_count: int = Field(ge=0)
    model_version: str | None = Field(default=None, min_length=1, max_length=128)
    feature_schema_version: str | None = Field(default=None, min_length=1, max_length=128)
    target_window_seconds: int | None = Field(default=None, gt=0)
    brier_score: Probability | None = None
    log_loss: NonNegativeFloat | None = None
    expected_calibration_error: Probability | None = None
    mean_probability: Probability | None = None
    observed_rate: Probability | None = None
    mean_interval_width: Probability | None = None
    calibration_bins: list[AvailabilityCalibrationBin] = Field(default_factory=list)


class RouteMatrix(DomainModel):
    travel_time_seconds: dict[str, dict[str, NonNegativeFloat]]
    provider_version: str = Field(min_length=1, max_length=128)
    matrix_id: str | None = Field(default=None, min_length=1, max_length=64)
    binding: "RouteMatrixBinding | None" = None

    @model_validator(mode="after")
    def validate_travel_times(self) -> "RouteMatrix":
        if (self.matrix_id is None) != (self.binding is None):
            raise ValueError(
                "route matrix ID and binding must either both be set or both be omitted"
            )
        if not self.travel_time_seconds:
            raise ValueError("route matrix must contain at least one source node")
        for source_id, targets in self.travel_time_seconds.items():
            if not source_id.strip():
                raise ValueError("route matrix source node IDs must not be blank")
            for target_id, seconds in targets.items():
                if not target_id.strip():
                    raise ValueError("route matrix target node IDs must not be blank")
                if not math.isfinite(seconds):
                    raise ValueError("route matrix travel times must be finite")
        return self


class RouteFallback(DomainModel):
    fallback_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    guarantees_parking: Literal[True] = True
    location: GeoPoint | None = None


class RouteMatrixBinding(DomainModel):
    matrix_schema_version: str = Field(min_length=1, max_length=128)
    origin: GeoPoint
    destination: Destination
    candidate_segment_ids: list[str]
    fallback: RouteFallback
    routing_profile: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_candidate_ids(self) -> "RouteMatrixBinding":
        if any(
            not segment_id.strip()
            or segment_id != segment_id.strip()
            or len(segment_id) > 64
            or segment_id in {"origin", "fallback"}
            for segment_id in self.candidate_segment_ids
        ):
            raise ValueError(
                "route matrix candidate IDs must be nonblank, trimmed, at most 64 characters, "
                "and not reserved"
            )
        if len(self.candidate_segment_ids) != len(set(self.candidate_segment_ids)):
            raise ValueError("route matrix candidate segment IDs must be unique")
        if self.candidate_segment_ids != sorted(self.candidate_segment_ids):
            raise ValueError("route matrix candidate segment IDs must be sorted")
        return self


class SearchSession(DomainModel):
    session_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    destination_id: str = Field(min_length=1, max_length=64)
    origin: GeoPoint
    requested_arrival_time: AwareDateTime
    constraints: SearchConstraints
    candidate_segment_ids: list[str] = Field(default_factory=list)
    status: SearchSessionStatus = SearchSessionStatus.CREATED
    rule_engine_version: str | None = Field(default=None, max_length=128)
    availability_model_version: str | None = Field(default=None, max_length=128)
    route_matrix_version: str | None = Field(default=None, max_length=128)
    optimizer_version: str | None = Field(default=None, max_length=128)
    created_at: AwareDateTime


class ParkingOutcome(DomainModel):
    outcome_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    segment_id: str | None = Field(default=None, max_length=64)
    success: bool
    occurred_at: AwareDateTime
    search_duration_seconds: int | None = Field(default=None, ge=0)
    route_step_order: int | None = Field(default=None, ge=0)
