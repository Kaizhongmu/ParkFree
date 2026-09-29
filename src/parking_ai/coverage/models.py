"""Provider-independent contracts for bounded, on-demand road coverage."""

from __future__ import annotations

from datetime import UTC
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from parking_ai.domain import (
    AvailabilityPrediction,
    Destination,
    EvidenceStoragePolicy,
    FreeState,
    GeoPoint,
    LegalState,
    ParkingSegment,
    UserProfile,
)
from parking_ai.domain.schemas import AwareDateTime
from parking_ai.gis.models import RoadFeature


class CoverageProviderMetadata(BaseModel):
    """Attribution and retention information for one road-data acquisition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    attribution: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=255)
    source_uri: str = Field(min_length=1, max_length=2_048)
    raw_storage_policy: EvidenceStoragePolicy
    normalized_storage_policy: EvidenceStoragePolicy
    retrieved_at: AwareDateTime


class CoverageAttemptOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class CoverageAttemptRole(StrEnum):
    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"


class CoverageProviderAttempt(BaseModel):
    """Sanitized provider outcome; error details never cross the API boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str = Field(min_length=1, max_length=128)
    role: CoverageAttemptRole
    outcome: CoverageAttemptOutcome


class RoadAcquisition(BaseModel):
    """Normalized roads plus provider tags retained outside the GIS core."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    roads: tuple[RoadFeature, ...]
    tags_by_feature_id: dict[str, dict[str, str]] = Field(default_factory=dict)
    metadata: CoverageProviderMetadata
    provider_attempts: tuple[CoverageProviderAttempt, ...] = ()
    cache_hit: bool = False

    @model_validator(mode="after")
    def validate_feature_references(self) -> RoadAcquisition:
        feature_ids = [road.feature_id for road in self.roads]
        if len(feature_ids) != len(set(feature_ids)):
            raise ValueError("road acquisition feature IDs must be unique")
        unknown_ids = set(self.tags_by_feature_id).difference(feature_ids)
        if unknown_ids:
            raise ValueError("road tags must only reference acquired feature IDs")
        return self


class RoadCoverageError(RuntimeError):
    """Base class for expected road-coverage acquisition failures."""


class RoadCoverageProviderError(RoadCoverageError):
    """The upstream road provider was unavailable or refused the request."""


class RoadCoverageResponseError(RoadCoverageError):
    """The upstream response could not be safely normalized."""


class RoadCoverageExhaustedError(RoadCoverageError):
    """All configured providers failed; only sanitized attempt metadata is retained."""

    def __init__(self, attempts: tuple[CoverageProviderAttempt, ...]) -> None:
        super().__init__("all configured road coverage providers failed")
        self.attempts = attempts


class RoadCoverageProvider(Protocol):
    def acquire(
        self,
        destination: Destination,
        max_walk_minutes: float,
    ) -> RoadAcquisition: ...


class DestinationTimezoneResolver(Protocol):
    def resolve(self, location: GeoPoint) -> str: ...


class OnDemandParkingStatus(StrEnum):
    PROVISIONAL_LEADS = "PROVISIONAL_LEADS"
    NO_CANDIDATES = "NO_CANDIDATES"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class OnDemandResearchMode(StrEnum):
    """How much request-time road context to collect before inference."""

    INSTANT = "INSTANT"
    RESEARCH = "RESEARCH"


class ResearchEnrichmentStatus(StrEnum):
    """Whether the enhanced road/parking-tag source actually contributed."""

    NOT_REQUESTED = "NOT_REQUESTED"
    APPLIED = "APPLIED"
    DEGRADED = "DEGRADED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    FAILED = "FAILED"


class OnDemandParkingCommand(BaseModel):
    """Strict provider-independent input to the on-demand discovery service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: GeoPoint
    destination_query: str = Field(min_length=2, max_length=255)
    destination_match_id: str = Field(min_length=1, max_length=64)
    research_mode: OnDemandResearchMode = OnDemandResearchMode.RESEARCH
    arrival_time: AwareDateTime
    arrival_time_was_now: bool
    parking_duration_minutes: int = Field(gt=0, le=1_440)
    free_only: bool
    max_walk_minutes: float = Field(gt=0, le=15)
    vehicle_profile: UserProfile
    max_candidates: int = Field(gt=0, le=20)

    @field_validator("destination_query")
    @classmethod
    def normalize_destination_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("destination query must contain at least two characters")
        return normalized

    @field_validator("destination_match_id")
    @classmethod
    def normalize_destination_match_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("destination match ID must not be blank")
        return normalized


class CoverageSummary(BaseModel):
    """Bounded acquisition facts returned without provider-specific payloads.

    ``tagged_road_count`` counts roads with at least one ``parking:*`` source tag, not ordinary
    road classification or provenance tags.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: CoverageProviderMetadata
    road_count: int = Field(ge=0)
    tagged_road_count: int = Field(ge=0)
    provider_attempts: tuple[CoverageProviderAttempt, ...] = ()
    cache_hit: bool


class OnDemandParkingResponse(BaseModel):
    """Provisional candidate leads; this is deliberately not a legal parking plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: OnDemandParkingStatus
    research_mode: OnDemandResearchMode = OnDemandResearchMode.RESEARCH
    enrichment_status: ResearchEnrichmentStatus = ResearchEnrichmentStatus.NOT_CONFIGURED
    destination: Destination
    resolved_arrival_time: AwareDateTime
    candidate_segments: tuple[ParkingSegment, ...]
    availability_predictions: tuple[AvailabilityPrediction, ...] = ()
    availability_assumption: Literal["CONDITIONAL_ON_LEGAL_AND_USABLE_CURB"] | None = None
    calibration_status: Literal["UNCALIBRATED_HEURISTIC"] | None = None
    destination_timezone: str | None = Field(default=None, min_length=1, max_length=128)
    coverage: CoverageSummary | None
    provider_attempts: tuple[CoverageProviderAttempt, ...] = ()
    warnings: tuple[str, ...]
    attribution: tuple[str, ...]

    @model_validator(mode="after")
    def validate_status(self) -> OnDemandParkingResponse:
        if (
            self.research_mode is OnDemandResearchMode.INSTANT
            and self.enrichment_status is not ResearchEnrichmentStatus.NOT_REQUESTED
        ):
            raise ValueError("instant responses cannot claim research enrichment")
        if (
            self.research_mode is OnDemandResearchMode.RESEARCH
            and self.enrichment_status is ResearchEnrichmentStatus.NOT_REQUESTED
        ):
            raise ValueError("research responses must report enrichment status")
        if not self.provider_attempts:
            raise ValueError("on-demand responses must include provider attempts")
        if self.provider_attempts[0].role is not CoverageAttemptRole.PRIMARY or any(
            attempt.role is not CoverageAttemptRole.FALLBACK
            for attempt in self.provider_attempts[1:]
        ):
            raise ValueError("provider attempts must start with primary then contain fallbacks")
        primary_outcome = self.provider_attempts[0].outcome
        fallback_succeeded = any(
            attempt.role is CoverageAttemptRole.FALLBACK
            and attempt.outcome is CoverageAttemptOutcome.SUCCEEDED
            for attempt in self.provider_attempts[1:]
        )
        if (
            self.enrichment_status is ResearchEnrichmentStatus.APPLIED
            and primary_outcome is not CoverageAttemptOutcome.SUCCEEDED
        ):
            raise ValueError("applied research enrichment requires a successful primary source")
        if self.enrichment_status is ResearchEnrichmentStatus.DEGRADED and not fallback_succeeded:
            raise ValueError("degraded research enrichment requires a successful fallback source")
        if self.enrichment_status is ResearchEnrichmentStatus.FAILED and any(
            attempt.outcome is CoverageAttemptOutcome.SUCCEEDED
            for attempt in self.provider_attempts
        ):
            raise ValueError("failed research enrichment cannot include a successful source")
        if self.status is OnDemandParkingStatus.PROVIDER_UNAVAILABLE:
            if (
                self.coverage is not None
                or self.candidate_segments
                or self.availability_predictions
            ):
                raise ValueError("provider-unavailable responses cannot contain acquired coverage")
            if any(
                attempt.outcome is not CoverageAttemptOutcome.FAILED
                for attempt in self.provider_attempts
            ):
                raise ValueError("provider-unavailable responses require failed attempts")
        elif self.coverage is None:
            raise ValueError("completed acquisitions must include coverage metadata")
        elif self.provider_attempts != self.coverage.provider_attempts:
            raise ValueError("top-level provider attempts must match coverage metadata")
        elif all(
            attempt.outcome is CoverageAttemptOutcome.FAILED for attempt in self.provider_attempts
        ):
            raise ValueError("completed acquisitions require a non-failed provider attempt")
        if self.status is OnDemandParkingStatus.PROVISIONAL_LEADS:
            if not self.candidate_segments:
                raise ValueError("provisional-lead responses require candidate segments")
            if not any(
                attempt.outcome is CoverageAttemptOutcome.SUCCEEDED
                for attempt in self.provider_attempts
            ):
                raise ValueError("provisional leads require a successful provider attempt")
            for segment in self.candidate_segments:
                if (
                    segment.legal_state is not LegalState.UNKNOWN
                    or segment.free_state is not FreeState.UNKNOWN
                    or segment.legal_confidence != 0
                    or segment.regulation_refs
                    or segment.availability_probability is not None
                    or segment.availability_interval is not None
                ):
                    raise ValueError(
                        "provisional leads must not contain evaluated regulation "
                        "or availability state"
                    )
        elif self.candidate_segments:
            raise ValueError("only provisional-lead responses may contain candidate segments")

        prediction_ids = [prediction.segment_id for prediction in self.availability_predictions]
        if len(prediction_ids) != len(set(prediction_ids)):
            raise ValueError("availability predictions must have unique segment IDs")
        candidate_ids = {segment.segment_id for segment in self.candidate_segments}
        if set(prediction_ids).difference(candidate_ids):
            raise ValueError(
                "availability predictions must reference provisional candidate segments"
            )
        if self.availability_predictions:
            if self.status is not OnDemandParkingStatus.PROVISIONAL_LEADS:
                raise ValueError("availability predictions require provisional leads")
            if self.availability_assumption is None or self.destination_timezone is None:
                raise ValueError("availability predictions require an assumption and timezone")
            if len(self.availability_predictions) != len(self.candidate_segments):
                raise ValueError("every provisional candidate must have an availability prediction")
            if self.calibration_status != "UNCALIBRATED_HEURISTIC":
                raise ValueError("provisional availability predictions must declare calibration")
            predicted_at_values = {
                prediction.predicted_at for prediction in self.availability_predictions
            }
            if len(predicted_at_values) != 1:
                raise ValueError("one on-demand response must use one prediction timestamp")
            candidate_by_id = {segment.segment_id: segment for segment in self.candidate_segments}
            arrival_utc = self.resolved_arrival_time.astimezone(UTC)
            for prediction in self.availability_predictions:
                segment = candidate_by_id[prediction.segment_id]
                snapshot = prediction.feature_snapshot
                if snapshot is None or prediction.target_window_seconds != 90:
                    raise ValueError("provisional predictions require the V0 feature snapshot")
                if (
                    snapshot.arrival_time_utc.astimezone(UTC) != arrival_utc
                    or snapshot.local_timezone != self.destination_timezone
                    or snapshot.segment_length_m != segment.length_m
                    or snapshot.physical_state is not segment.physical_state
                ):
                    raise ValueError(
                        "provisional prediction features must match the response request "
                        "and segment"
                    )
        elif (
            self.availability_assumption is not None
            or self.destination_timezone is not None
            or self.calibration_status is not None
        ):
            raise ValueError("availability metadata requires predictions")
        return self


class SelectedDestinationNotFoundError(LookupError):
    """The canonical re-query no longer contains the browser-selected match."""
