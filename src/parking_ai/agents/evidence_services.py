"""Bounded, provider-independent AI evidence extraction services.

The services in this module validate untrusted extractor output and produce evidence and
rule *proposals*.  They deliberately do not evaluate legality, write persistence records, or
modify deterministic-core results.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from parking_ai.domain.enums import (
    DayOfWeek,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    ParkingRuleType,
)
from parking_ai.domain.schemas import Evidence, NormalizedClaim, ParkingRule, RuleException


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value


AwareDateTime = Annotated[datetime, AfterValidator(_require_timezone)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class ExtractionModel(BaseModel):
    """Strict base model for all Phase 8 service-boundary data."""

    model_config = ConfigDict(extra="forbid")


class EvidenceServiceKind(StrEnum):
    REGULATION = "REGULATION"
    COMMUNITY = "COMMUNITY"
    VISION = "VISION"


class ExtractionDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    QUARANTINED = "QUARANTINED"


class ReviewReason(StrEnum):
    LOW_AUTHORITY_SOURCE = "LOW_AUTHORITY_SOURCE"
    LOW_EXTRACTION_CONFIDENCE = "LOW_EXTRACTION_CONFIDENCE"
    HUMAN_VERIFICATION_REQUIRED = "HUMAN_VERIFICATION_REQUIRED"
    INVALID_EXTRACTOR_OUTPUT = "INVALID_EXTRACTOR_OUTPUT"
    EXTRACTOR_VERSION_MISMATCH = "EXTRACTOR_VERSION_MISMATCH"
    ADAPTER_FAILURE = "ADAPTER_FAILURE"


class ApprovalScope(StrEnum):
    EVIDENCE_ONLY = "EVIDENCE_ONLY"
    EVIDENCE_AND_RULES = "EVIDENCE_AND_RULES"


class CommunityClaimType(StrEnum):
    FREE_AFTER = "FREE_AFTER"
    LOW_AVAILABILITY = "LOW_AVAILABILITY"
    HIGH_AVAILABILITY = "HIGH_AVAILABILITY"
    ENFORCEMENT_REPORT = "ENFORCEMENT_REPORT"
    PARKING_RESTRICTION = "PARKING_RESTRICTION"
    OTHER = "OTHER"


class VisionPhysicalClaimType(StrEnum):
    CURB_MAY_BE_PARKABLE = "CURB_MAY_BE_PARKABLE"
    CURB_MAY_NOT_BE_PARKABLE = "CURB_MAY_NOT_BE_PARKABLE"
    CAPACITY_ESTIMATE = "CAPACITY_ESTIMATE"
    SIGN_PRESENT = "SIGN_PRESENT"
    OTHER = "OTHER"


class SourceMaterial(ExtractionModel):
    """Provider-independent source snapshot supplied to an extractor adapter."""

    source_type: EvidenceSourceType
    source_uri_or_identifier: str = Field(min_length=1, max_length=2048)
    publisher: str | None = Field(default=None, max_length=255)
    published_at: AwareDateTime | None = None
    observed_at: AwareDateTime | None = None
    retrieved_at: AwareDateTime
    raw_storage_policy: EvidenceStoragePolicy
    segment_ids: list[str] = Field(min_length=1)
    content: str = Field(min_length=1, max_length=1_000_000)

    @model_validator(mode="after")
    def validate_source(self) -> SourceMaterial:
        normalized_ids = [segment_id.strip() for segment_id in self.segment_ids]
        if any(not segment_id or len(segment_id) > 64 for segment_id in normalized_ids):
            raise ValueError("segment IDs must be nonblank and at most 64 characters")
        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("segment IDs must be unique")
        if self.published_at is not None and self.published_at > self.retrieved_at:
            raise ValueError("published_at cannot be after retrieved_at")
        if self.observed_at is not None and self.observed_at > self.retrieved_at:
            raise ValueError("observed_at cannot be after retrieved_at")
        self.segment_ids = sorted(normalized_ids)
        return self


class ExtractionRequest(ExtractionModel):
    """Typed request passed to every provider-specific extractor adapter."""

    schema_version: Literal["phase8-extraction-request-v1"] = "phase8-extraction-request-v1"
    service_kind: EvidenceServiceKind
    source: SourceMaterial


class ExtractedRegulationClaim(ExtractionModel):
    """A normalized rule proposal emitted by a regulation or sign extractor."""

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
    extraction_confidence: Probability

    @model_validator(mode="after")
    def validate_schedule(self) -> ExtractedRegulationClaim:
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time and end_time must both be set or both be omitted")
        if self.start_time is not None and self.end_time is not None:
            if self.start_time.tzinfo is not None or self.end_time.tzinfo is not None:
                raise ValueError("rule wall times must not include timezone information")
            if self.start_time == self.end_time:
                raise ValueError("equal start and end times are ambiguous")
        if len(self.days) != len(set(self.days)):
            raise ValueError("rule days must not contain duplicates")
        if (
            self.effective_start_date is not None
            and self.effective_end_date is not None
            and self.effective_start_date > self.effective_end_date
        ):
            raise ValueError("effective start date cannot be after effective end date")
        for exception in self.exceptions:
            _validate_extracted_exception(exception)
        self.days = sorted(self.days, key=lambda day: day.value)
        return self


class CommunityClaim(ExtractionModel):
    claim_type: CommunityClaimType
    location_text: str = Field(min_length=1, max_length=500)
    statement: str = Field(min_length=1, max_length=2000)
    local_time: time | None = None
    condition: str | None = Field(default=None, max_length=500)
    extraction_confidence: Probability

    @model_validator(mode="after")
    def validate_local_time(self) -> CommunityClaim:
        if self.local_time is not None and self.local_time.tzinfo is not None:
            raise ValueError("community local_time must not include timezone information")
        return self


class VisionPhysicalClaim(ExtractionModel):
    claim_type: VisionPhysicalClaimType
    estimated_capacity: int | None = Field(default=None, ge=0)
    observation: str = Field(min_length=1, max_length=1000)
    extraction_confidence: Probability

    @model_validator(mode="after")
    def validate_capacity(self) -> VisionPhysicalClaim:
        if (
            self.claim_type is VisionPhysicalClaimType.CAPACITY_ESTIMATE
            and self.estimated_capacity is None
        ):
            raise ValueError("capacity claims require estimated_capacity")
        if (
            self.claim_type is not VisionPhysicalClaimType.CAPACITY_ESTIMATE
            and self.estimated_capacity is not None
        ):
            raise ValueError("estimated_capacity is only valid for capacity claims")
        return self


class RegulationExtractorResponse(ExtractionModel):
    schema_version: Literal["phase8-regulation-response-v1"]
    extractor_version: str = Field(min_length=1, max_length=128)
    claims: list[ExtractedRegulationClaim] = Field(min_length=1)


class CommunityExtractorResponse(ExtractionModel):
    schema_version: Literal["phase8-community-response-v1"]
    extractor_version: str = Field(min_length=1, max_length=128)
    claims: list[CommunityClaim] = Field(min_length=1)


class VisionExtractorResponse(ExtractionModel):
    schema_version: Literal["phase8-vision-response-v1"]
    extractor_version: str = Field(min_length=1, max_length=128)
    sign_claims: list[ExtractedRegulationClaim] = Field(default_factory=list)
    physical_claims: list[VisionPhysicalClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_claim(self) -> VisionExtractorResponse:
        if not self.sign_claims and not self.physical_claims:
            raise ValueError("vision response must include at least one claim")
        return self


class RegulationExtractorAdapter(Protocol):
    extractor_version: str

    def extract(self, request: ExtractionRequest) -> object: ...


class CommunityExtractorAdapter(Protocol):
    extractor_version: str

    def extract(self, request: ExtractionRequest) -> object: ...


class VisionExtractorAdapter(Protocol):
    extractor_version: str

    def extract(self, request: ExtractionRequest) -> object: ...


class EvidenceExtractionResult(ExtractionModel):
    """Validated result envelope; proposals require caller-controlled review/persistence."""

    result_id: str = Field(min_length=1, max_length=64)
    service_kind: EvidenceServiceKind
    disposition: ExtractionDisposition
    extractor_version: str = Field(min_length=1, max_length=128)
    evidence: Evidence | None = None
    proposed_rules: list[ParkingRule] = Field(default_factory=list)
    review_reasons: list[ReviewReason] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_disposition(self) -> EvidenceExtractionResult:
        if self.disposition is ExtractionDisposition.QUARANTINED:
            if self.evidence is not None or self.proposed_rules:
                raise ValueError("quarantined output cannot expose evidence or rule proposals")
            if not self.validation_errors:
                raise ValueError("quarantined output must include validation error codes")
        else:
            if self.evidence is None:
                raise ValueError("non-quarantined output requires validated evidence")
            if self.validation_errors:
                raise ValueError("validated output cannot include validation errors")
        if self.disposition is ExtractionDisposition.REVIEW_REQUIRED and not self.review_reasons:
            raise ValueError("review-required output must include a review reason")
        if self.disposition is ExtractionDisposition.ACCEPTED and self.review_reasons:
            raise ValueError("accepted output cannot include review reasons")
        return self


class ApprovedEvidenceBundle(ExtractionModel):
    """Human-reviewed material that is eligible for persistence."""

    approval_id: str = Field(min_length=1, max_length=64)
    source_result_id: str = Field(min_length=1, max_length=64)
    scope: ApprovalScope
    reviewer_id: str = Field(min_length=1, max_length=128)
    reviewed_at: AwareDateTime
    evidence: Evidence
    rules: list[ParkingRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self) -> ApprovedEvidenceBundle:
        if self.scope is ApprovalScope.EVIDENCE_ONLY and self.rules:
            raise ValueError("evidence-only approval cannot publish parking rules")
        if self.scope is ApprovalScope.EVIDENCE_AND_RULES and not self.rules:
            raise ValueError("rule approval must include at least one parking rule")
        if any(rule.source_evidence_id != self.evidence.evidence_id for rule in self.rules):
            raise ValueError("approved rules must reference the approved evidence")
        return self


_RELIABILITY_BY_SOURCE: dict[EvidenceSourceType, EvidenceReliabilityTier] = {
    EvidenceSourceType.OFFICIAL_CODE: EvidenceReliabilityTier.A,
    EvidenceSourceType.OFFICIAL_GIS: EvidenceReliabilityTier.A,
    EvidenceSourceType.VERIFIED_SIGN: EvidenceReliabilityTier.A,
    EvidenceSourceType.UNIVERSITY: EvidenceReliabilityTier.B,
    EvidenceSourceType.OSM: EvidenceReliabilityTier.B,
    EvidenceSourceType.COMMUNITY: EvidenceReliabilityTier.C,
    EvidenceSourceType.WEB: EvidenceReliabilityTier.C,
    EvidenceSourceType.IMAGERY_INFERENCE: EvidenceReliabilityTier.D,
}

_REGULATION_SOURCE_TYPES = frozenset(
    {
        EvidenceSourceType.OFFICIAL_CODE,
        EvidenceSourceType.OFFICIAL_GIS,
        EvidenceSourceType.UNIVERSITY,
        EvidenceSourceType.OSM,
        EvidenceSourceType.WEB,
    }
)

_PERSISTABLE_RAW_SOURCE_TYPES = frozenset(
    {
        EvidenceSourceType.OFFICIAL_CODE,
        EvidenceSourceType.OFFICIAL_GIS,
        EvidenceSourceType.UNIVERSITY,
        EvidenceSourceType.OSM,
    }
)


class RegulationEvidenceService:
    def __init__(
        self,
        adapter: RegulationExtractorAdapter,
        *,
        minimum_confidence: float = 0.8,
    ) -> None:
        self._adapter = adapter
        _validate_adapter_version(adapter.extractor_version)
        self._minimum_confidence = _validate_threshold(minimum_confidence)

    def extract(self, source: SourceMaterial) -> EvidenceExtractionResult:
        _require_source_type(source, _REGULATION_SOURCE_TYPES, EvidenceServiceKind.REGULATION)
        _require_storage_policy(source)
        request = ExtractionRequest(service_kind=EvidenceServiceKind.REGULATION, source=source)
        raw, failure = _call_adapter(self._adapter, request, EvidenceServiceKind.REGULATION)
        if failure is not None:
            return failure
        try:
            response = RegulationExtractorResponse.model_validate(raw)
        except ValidationError as error:
            return _quarantined_validation_result(
                EvidenceServiceKind.REGULATION, self._adapter.extractor_version, source, error
            )
        mismatch = _version_mismatch_result(
            EvidenceServiceKind.REGULATION, self._adapter.extractor_version, source, response
        )
        if mismatch is not None:
            return mismatch
        return _validated_result(
            kind=EvidenceServiceKind.REGULATION,
            source=source,
            extractor_version=response.extractor_version,
            claim_models=response.claims,
            rule_claims=response.claims,
            minimum_confidence=self._minimum_confidence,
            force_human_review=True,
        )


class CommunityEvidenceService:
    def __init__(
        self,
        adapter: CommunityExtractorAdapter,
        *,
        minimum_confidence: float = 0.8,
    ) -> None:
        self._adapter = adapter
        _validate_adapter_version(adapter.extractor_version)
        self._minimum_confidence = _validate_threshold(minimum_confidence)

    def extract(self, source: SourceMaterial) -> EvidenceExtractionResult:
        _require_source_type(
            source, frozenset({EvidenceSourceType.COMMUNITY}), EvidenceServiceKind.COMMUNITY
        )
        _require_storage_policy(source)
        request = ExtractionRequest(service_kind=EvidenceServiceKind.COMMUNITY, source=source)
        raw, failure = _call_adapter(self._adapter, request, EvidenceServiceKind.COMMUNITY)
        if failure is not None:
            return failure
        try:
            response = CommunityExtractorResponse.model_validate(raw)
        except ValidationError as error:
            return _quarantined_validation_result(
                EvidenceServiceKind.COMMUNITY, self._adapter.extractor_version, source, error
            )
        mismatch = _version_mismatch_result(
            EvidenceServiceKind.COMMUNITY, self._adapter.extractor_version, source, response
        )
        if mismatch is not None:
            return mismatch
        return _validated_result(
            kind=EvidenceServiceKind.COMMUNITY,
            source=source,
            extractor_version=response.extractor_version,
            claim_models=response.claims,
            rule_claims=[],
            minimum_confidence=self._minimum_confidence,
            force_human_review=True,
        )


class VisionEvidenceService:
    def __init__(
        self,
        adapter: VisionExtractorAdapter,
        *,
        minimum_confidence: float = 0.8,
    ) -> None:
        self._adapter = adapter
        _validate_adapter_version(adapter.extractor_version)
        self._minimum_confidence = _validate_threshold(minimum_confidence)

    def extract(self, source: SourceMaterial) -> EvidenceExtractionResult:
        _require_source_type(
            source, frozenset({EvidenceSourceType.IMAGERY_INFERENCE}), EvidenceServiceKind.VISION
        )
        _require_storage_policy(source)
        request = ExtractionRequest(service_kind=EvidenceServiceKind.VISION, source=source)
        raw, failure = _call_adapter(self._adapter, request, EvidenceServiceKind.VISION)
        if failure is not None:
            return failure
        try:
            response = VisionExtractorResponse.model_validate(raw)
        except ValidationError as error:
            return _quarantined_validation_result(
                EvidenceServiceKind.VISION, self._adapter.extractor_version, source, error
            )
        mismatch = _version_mismatch_result(
            EvidenceServiceKind.VISION, self._adapter.extractor_version, source, response
        )
        if mismatch is not None:
            return mismatch
        claims: list[ExtractionModel] = [*response.sign_claims, *response.physical_claims]
        return _validated_result(
            kind=EvidenceServiceKind.VISION,
            source=source,
            extractor_version=response.extractor_version,
            claim_models=claims,
            rule_claims=response.sign_claims,
            minimum_confidence=self._minimum_confidence,
            force_human_review=True,
        )


def _validate_threshold(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError("minimum confidence must be between zero and one")
    return value


def _validate_extracted_exception(exception: RuleException) -> None:
    exception_type = exception.exception_type.upper()
    parameters = exception.parameters
    if exception_type in {"PERMIT", "PERMIT_TYPE"}:
        if set(parameters) != {"permit_type"}:
            raise ValueError("permit exceptions require only permit_type")
        permit_type = parameters["permit_type"]
        if not isinstance(permit_type, str) or not permit_type.strip():
            raise ValueError("permit exception permit_type must be a nonblank string")
        return
    if exception_type == "VEHICLE_TYPE":
        if not parameters or set(parameters) - {"vehicle_type", "vehicle_types"}:
            raise ValueError(
                "vehicle exceptions require vehicle_type or vehicle_types and no other fields"
            )
        vehicle_type = parameters.get("vehicle_type")
        vehicle_types = parameters.get("vehicle_types")
        one_valid = isinstance(vehicle_type, str) and bool(vehicle_type.strip())
        many_valid = (
            isinstance(vehicle_types, list)
            and bool(vehicle_types)
            and all(isinstance(item, str) and item.strip() for item in vehicle_types)
        )
        if not one_valid and not many_valid:
            raise ValueError("vehicle exception values must contain nonblank strings")
        if vehicle_type is not None and not one_valid:
            raise ValueError("vehicle_type must be a nonblank string")
        if vehicle_types is not None and not many_valid:
            raise ValueError("vehicle_types must be a nonempty list of nonblank strings")
        return
    raise ValueError("unsupported extracted rule exception type")


def _validate_adapter_version(value: str) -> None:
    if not value or value != value.strip() or len(value) > 128:
        raise ValueError("extractor version must be nonblank, trimmed, and at most 128 characters")


def _require_storage_policy(source: SourceMaterial) -> None:
    if (
        source.raw_storage_policy is EvidenceStoragePolicy.PERSIST
        and source.source_type not in _PERSISTABLE_RAW_SOURCE_TYPES
    ):
        raise ValueError(
            f"raw {source.source_type.value} content cannot use the PERSIST storage policy"
        )


def _require_source_type(
    source: SourceMaterial,
    allowed: frozenset[EvidenceSourceType],
    kind: EvidenceServiceKind,
) -> None:
    if source.source_type not in allowed:
        expected = ", ".join(sorted(item.value for item in allowed))
        raise ValueError(f"{kind.value} extraction requires one of these source types: {expected}")


def _call_adapter(
    adapter: RegulationExtractorAdapter | CommunityExtractorAdapter | VisionExtractorAdapter,
    request: ExtractionRequest,
    kind: EvidenceServiceKind,
) -> tuple[object, EvidenceExtractionResult | None]:
    try:
        return adapter.extract(request), None
    except Exception as error:  # adapters are an explicit untrusted failure boundary
        error_code = f"adapter:{type(error).__name__}"
        return None, _quarantined_result(
            kind=kind,
            extractor_version=adapter.extractor_version,
            source=request.source,
            reason=ReviewReason.ADAPTER_FAILURE,
            errors=[error_code],
        )


def _version_mismatch_result(
    kind: EvidenceServiceKind,
    adapter_version: str,
    source: SourceMaterial,
    response: RegulationExtractorResponse | CommunityExtractorResponse | VisionExtractorResponse,
) -> EvidenceExtractionResult | None:
    if response.extractor_version == adapter_version:
        return None
    return _quarantined_result(
        kind=kind,
        extractor_version=adapter_version,
        source=source,
        reason=ReviewReason.EXTRACTOR_VERSION_MISMATCH,
        errors=["extractor_version:mismatch"],
    )


def _quarantined_validation_result(
    kind: EvidenceServiceKind,
    extractor_version: str,
    source: SourceMaterial,
    error: ValidationError,
) -> EvidenceExtractionResult:
    errors = sorted(
        {
            f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        }
    )
    return _quarantined_result(
        kind=kind,
        extractor_version=extractor_version,
        source=source,
        reason=ReviewReason.INVALID_EXTRACTOR_OUTPUT,
        errors=errors,
    )


def _quarantined_result(
    *,
    kind: EvidenceServiceKind,
    extractor_version: str,
    source: SourceMaterial,
    reason: ReviewReason,
    errors: list[str],
) -> EvidenceExtractionResult:
    payload = {
        "kind": kind.value,
        "source_type": source.source_type.value,
        "source_identifier": source.source_uri_or_identifier,
        "content_hash": _content_hash(source.content),
        "extractor_version": extractor_version,
        "reason": reason.value,
        "errors": errors,
    }
    return EvidenceExtractionResult(
        result_id=_stable_id("extract_", payload),
        service_kind=kind,
        disposition=ExtractionDisposition.QUARANTINED,
        extractor_version=extractor_version,
        review_reasons=[reason],
        validation_errors=errors,
    )


def _validated_result(
    *,
    kind: EvidenceServiceKind,
    source: SourceMaterial,
    extractor_version: str,
    claim_models: Sequence[ExtractionModel],
    rule_claims: Sequence[ExtractedRegulationClaim],
    minimum_confidence: float,
    force_human_review: bool,
) -> EvidenceExtractionResult:
    sorted_claim_payloads = sorted(
        (claim.model_dump(mode="json") for claim in claim_models), key=_canonical_json
    )
    content_hash = _content_hash(source.content)
    identity_payload = {
        "schema_version": "phase8-evidence-identity-v1",
        "kind": kind.value,
        "source_type": source.source_type.value,
        "source_identifier": source.source_uri_or_identifier,
        "publisher": source.publisher,
        "published_at": (
            source.published_at.isoformat() if source.published_at is not None else None
        ),
        "observed_at": (source.observed_at.isoformat() if source.observed_at is not None else None),
        "retrieved_at": source.retrieved_at.isoformat(),
        "raw_storage_policy": source.raw_storage_policy.value,
        "segment_ids": source.segment_ids,
        "content_hash": content_hash,
        "extractor_version": extractor_version,
        "claims": sorted_claim_payloads,
    }
    evidence_id = _stable_id("evidence_ai_", identity_payload)
    normalized_claims = [
        NormalizedClaim(
            claim_type=_claim_type(kind, payload),
            attributes={key: value for key, value in payload.items() if key != "claim_type"},
        )
        for payload in sorted_claim_payloads
    ]
    evidence = Evidence(
        evidence_id=evidence_id,
        source_type=source.source_type,
        source_uri_or_identifier=source.source_uri_or_identifier,
        publisher=source.publisher,
        published_at=source.published_at,
        observed_at=source.observed_at,
        retrieved_at=source.retrieved_at,
        raw_storage_policy=source.raw_storage_policy,
        segment_ids=source.segment_ids,
        normalized_claims=normalized_claims,
        reliability_tier=_RELIABILITY_BY_SOURCE[source.source_type],
        extractor_version=extractor_version,
        content_hash=content_hash,
    )
    proposed_rules = _build_rule_proposals(rule_claims, source.segment_ids, evidence_id)

    reasons: set[ReviewReason] = set()
    if evidence.reliability_tier in {
        EvidenceReliabilityTier.C,
        EvidenceReliabilityTier.D,
    }:
        reasons.add(ReviewReason.LOW_AUTHORITY_SOURCE)
    if force_human_review:
        reasons.add(ReviewReason.HUMAN_VERIFICATION_REQUIRED)
    if any(_claim_confidence(claim) < minimum_confidence for claim in claim_models):
        reasons.add(ReviewReason.LOW_EXTRACTION_CONFIDENCE)
    disposition = (
        ExtractionDisposition.REVIEW_REQUIRED if reasons else ExtractionDisposition.ACCEPTED
    )
    result_payload = {
        "evidence_id": evidence_id,
        "disposition": disposition.value,
        "rule_ids": [rule.rule_id for rule in proposed_rules],
        "review_reasons": sorted(reason.value for reason in reasons),
    }
    return EvidenceExtractionResult(
        result_id=_stable_id("extract_", result_payload),
        service_kind=kind,
        disposition=disposition,
        extractor_version=extractor_version,
        evidence=evidence,
        proposed_rules=proposed_rules,
        review_reasons=sorted(reasons, key=lambda reason: reason.value),
    )


def approve_extraction(
    result: EvidenceExtractionResult,
    *,
    reviewer_id: str,
    reviewed_at: datetime,
    scope: ApprovalScope = ApprovalScope.EVIDENCE_ONLY,
) -> ApprovedEvidenceBundle:
    """Create a review-provenance-bearing bundle eligible for database persistence.

    AI output cannot approve itself. Rule publication is limited to the regulation service;
    community and imagery results remain evidence-only even after review. Promoting a vision
    result to ``VERIFIED_SIGN`` requires a separately sourced trusted evidence record.
    """

    reviewer_id = reviewer_id.strip()
    if not reviewer_id or len(reviewer_id) > 128:
        raise ValueError("reviewer_id must be nonblank and at most 128 characters")
    reviewed_at = _require_timezone(reviewed_at)
    if result.disposition is ExtractionDisposition.QUARANTINED or result.evidence is None:
        raise ValueError("quarantined extraction results cannot be approved")
    if reviewed_at < result.evidence.retrieved_at:
        raise ValueError("reviewed_at cannot be before evidence retrieval")
    if scope is ApprovalScope.EVIDENCE_AND_RULES:
        if result.service_kind is not EvidenceServiceKind.REGULATION:
            raise ValueError("only regulation extraction can publish parking rules")
        if not result.proposed_rules:
            raise ValueError("regulation result has no parking rules to approve")

    review_payload = {
        "schema_version": "phase8-human-review-v1",
        "source_result_id": result.result_id,
        "scope": scope.value,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at.isoformat(),
    }
    approval_id = _stable_id("approval_", review_payload)
    original_evidence = result.evidence
    approved_evidence_payload = {
        "schema_version": "phase8-approved-evidence-v1",
        "original_evidence_id": original_evidence.evidence_id,
        "approval": review_payload,
    }
    approved_evidence_id = _stable_id("evidence_reviewed_", approved_evidence_payload)
    review_claim = NormalizedClaim(
        claim_type="HUMAN_REVIEW_APPROVAL",
        attributes={
            "approval_id": approval_id,
            "original_evidence_id": original_evidence.evidence_id,
            "reviewer_id": reviewer_id,
            "reviewed_at": reviewed_at.isoformat(),
            "scope": scope.value,
        },
    )
    approved_evidence = original_evidence.model_copy(
        update={
            "evidence_id": approved_evidence_id,
            "normalized_claims": [*original_evidence.normalized_claims, review_claim],
        },
        deep=True,
    )
    approved_rules: list[ParkingRule] = []
    if scope is ApprovalScope.EVIDENCE_AND_RULES:
        for proposal in result.proposed_rules:
            rule_data = proposal.model_dump(exclude={"rule_id", "source_evidence_id"})
            rule_identity_data = proposal.model_dump(
                mode="json", exclude={"rule_id", "source_evidence_id"}
            )
            rule_identity = {
                "schema_version": "phase8-approved-rule-v1",
                "approval_id": approval_id,
                "approved_evidence_id": approved_evidence_id,
                "rule": rule_identity_data,
            }
            approved_rules.append(
                ParkingRule(
                    rule_id=_stable_id("rule_reviewed_", rule_identity),
                    source_evidence_id=approved_evidence_id,
                    **rule_data,
                )
            )
    return ApprovedEvidenceBundle(
        approval_id=approval_id,
        source_result_id=result.result_id,
        scope=scope,
        reviewer_id=reviewer_id,
        reviewed_at=reviewed_at,
        evidence=approved_evidence,
        rules=sorted(approved_rules, key=lambda rule: rule.rule_id),
    )


def _claim_type(kind: EvidenceServiceKind, payload: dict[str, object]) -> str:
    value = payload.get("claim_type") or payload.get("rule_type")
    if not isinstance(value, str):
        raise ValueError(f"{kind.value} claim lacks a stable claim type")
    return value


def _claim_confidence(claim: ExtractionModel) -> float:
    value = getattr(claim, "extraction_confidence", None)
    if not isinstance(value, float):
        raise ValueError("validated claim lacks extraction_confidence")
    return value


def _build_rule_proposals(
    claims: Sequence[ExtractedRegulationClaim],
    segment_ids: Sequence[str],
    evidence_id: str,
) -> list[ParkingRule]:
    proposals: list[ParkingRule] = []
    sorted_claims = sorted(claims, key=lambda claim: _canonical_json(claim.model_dump(mode="json")))
    for segment_id in segment_ids:
        for claim in sorted_claims:
            rule_payload = {
                "schema_version": "phase8-rule-proposal-identity-v1",
                "segment_id": segment_id,
                "evidence_id": evidence_id,
                "claim": claim.model_dump(mode="json"),
            }
            proposals.append(
                ParkingRule(
                    rule_id=_stable_id("rule_ai_", rule_payload),
                    segment_id=segment_id,
                    source_evidence_id=evidence_id,
                    **claim.model_dump(),
                )
            )
    return sorted(proposals, key=lambda rule: rule.rule_id)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_id(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:32]}"
