from datetime import date, datetime, time
from typing import Annotated, Any
from uuid import uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from parking_ai.domain.enums import (
    DayOfWeek,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalState,
    ParkingRuleType,
    PhysicalState,
    SearchSessionStatus,
    SegmentSide,
)
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


class LegalityEvaluation(DomainModel):
    evaluation_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    segment_id: str = Field(min_length=1, max_length=64)
    legal_state: LegalState
    free_state: FreeState
    max_duration_min: int | None = Field(default=None, gt=0)
    confidence: Probability
    evidence_refs: list[str] = Field(min_length=1)
    reason_codes: list[str] = Field(min_length=1)
    evaluated_at: AwareDateTime
    rule_engine_version: str = Field(min_length=1, max_length=128)


class AvailabilityPrediction(DomainModel):
    prediction_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    segment_id: str = Field(min_length=1, max_length=64)
    probability: Probability
    interval: tuple[Probability, Probability] | None = None
    model_version: str = Field(min_length=1, max_length=128)
    predicted_at: AwareDateTime

    @model_validator(mode="after")
    def validate_interval(self) -> "AvailabilityPrediction":
        if self.interval is None:
            return self
        lower, upper = self.interval
        if lower > upper:
            raise ValueError("uncertainty interval lower bound cannot exceed upper bound")
        if not lower <= self.probability <= upper:
            raise ValueError("probability must fall within its uncertainty interval")
        return self


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


class SearchRoute(DomainModel):
    route_id: str = Field(default_factory=_new_id, min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    steps: list[SearchRouteStep]
    expected_time_to_park_min: NonNegativeFloat
    success_probability: Probability
    fallback_description: str | None = Field(default=None, max_length=500)
    optimizer_version: str = Field(min_length=1, max_length=128)


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


class RouteMatrix(DomainModel):
    travel_time_seconds: dict[str, dict[str, NonNegativeFloat]]
    provider_version: str = Field(min_length=1, max_length=128)


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
