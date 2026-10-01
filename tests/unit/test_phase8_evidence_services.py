from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from parking_ai.agents import (
    ApprovalScope,
    CommunityEvidenceService,
    EvidenceExtractionResult,
    ExtractionDisposition,
    ExtractionRequest,
    RegulationEvidenceService,
    ReviewReason,
    SourceMaterial,
    VisionEvidenceService,
    approve_extraction,
)
from parking_ai.domain import (
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
)

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
REVIEWED = datetime(2026, 9, 21, 13, tzinfo=UTC)


class StaticAdapter:
    def __init__(self, output: object, *, version: str = "fixture-extractor-v1") -> None:
        self.extractor_version = version
        self.output = output
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> object:
        self.requests.append(request)
        return self.output


class FailingAdapter:
    extractor_version = "failing-extractor-v1"

    def extract(self, request: ExtractionRequest) -> object:
        del request
        raise TimeoutError("untrusted provider details must not escape")


def source(
    source_type: EvidenceSourceType = EvidenceSourceType.OFFICIAL_CODE,
    *,
    segment_ids: list[str] | None = None,
    content: str = "No parking Monday through Friday from 8 AM to 6 PM.",
    storage: EvidenceStoragePolicy = EvidenceStoragePolicy.REFERENCE_ONLY,
) -> SourceMaterial:
    return SourceMaterial(
        source_type=source_type,
        source_uri_or_identifier="fixture://source/section-1",
        publisher="Fixture Publisher",
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        observed_at=datetime(2026, 9, 20, tzinfo=UTC),
        retrieved_at=NOW,
        raw_storage_policy=storage,
        segment_ids=segment_ids or ["segment-b", "segment-a"],
        content=content,
    )


def regulation_output(*, claims: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": "phase8-regulation-response-v1",
        "extractor_version": "fixture-extractor-v1",
        "claims": claims
        or [
            {
                "rule_type": "NO_PARKING",
                "days": ["FRI", "MON"],
                "start_time": "08:00:00",
                "end_time": "18:00:00",
                "exceptions": [
                    {
                        "exception_type": "PERMIT",
                        "parameters": {"permit_type": "SMU-A"},
                    }
                ],
                "extraction_confidence": 0.96,
            },
            {
                "rule_type": "PAID",
                "payment_required": True,
                "extraction_confidence": 0.93,
            },
        ],
    }


def test_regulation_output_is_typed_deterministic_and_requires_human_review() -> None:
    adapter = StaticAdapter(regulation_output())
    result = RegulationEvidenceService(adapter).extract(source())

    assert result.disposition is ExtractionDisposition.REVIEW_REQUIRED
    assert result.review_reasons == [ReviewReason.HUMAN_VERIFICATION_REQUIRED]
    assert result.evidence is not None
    assert result.evidence.reliability_tier is EvidenceReliabilityTier.A
    assert result.evidence.source_type is EvidenceSourceType.OFFICIAL_CODE
    assert result.evidence.segment_ids == ["segment-a", "segment-b"]
    assert len(result.proposed_rules) == 4
    assert len({rule.rule_id for rule in result.proposed_rules}) == 4
    assert any(rule.exceptions for rule in result.proposed_rules)
    assert adapter.requests[0].service_kind == "REGULATION"

    reordered_claims = list(reversed(regulation_output()["claims"]))  # type: ignore[arg-type]
    reordered = RegulationEvidenceService(
        StaticAdapter(regulation_output(claims=reordered_claims))
    ).extract(source(segment_ids=["segment-a", "segment-b"]))
    assert reordered.result_id == result.result_id
    assert reordered.evidence == result.evidence
    assert reordered.proposed_rules == result.proposed_rules


def test_material_source_or_claim_changes_change_stable_identity() -> None:
    baseline = RegulationEvidenceService(StaticAdapter(regulation_output())).extract(source())
    changed_content = RegulationEvidenceService(StaticAdapter(regulation_output())).extract(
        source(content="Different official source text")
    )
    changed_claim = RegulationEvidenceService(
        StaticAdapter(
            regulation_output(
                claims=[
                    {
                        "rule_type": "NO_PARKING",
                        "extraction_confidence": 0.95,
                    }
                ]
            )
        )
    ).extract(source())

    assert baseline.evidence is not None
    assert changed_content.evidence is not None
    assert changed_claim.evidence is not None
    assert baseline.evidence.evidence_id != changed_content.evidence.evidence_id
    assert baseline.evidence.evidence_id != changed_claim.evidence.evidence_id


def test_invalid_or_version_mismatched_output_is_quarantined_without_payload() -> None:
    invalid = regulation_output()
    invalid["unexpected"] = "must be rejected"
    invalid_result = RegulationEvidenceService(StaticAdapter(invalid)).extract(source())

    mismatch = regulation_output()
    mismatch["extractor_version"] = "other-version"
    mismatch_result = RegulationEvidenceService(StaticAdapter(mismatch)).extract(source())

    for result in (invalid_result, mismatch_result):
        assert result.disposition is ExtractionDisposition.QUARANTINED
        assert result.evidence is None
        assert result.proposed_rules == []
        assert result.validation_errors
    assert ReviewReason.INVALID_EXTRACTOR_OUTPUT in invalid_result.review_reasons
    assert ReviewReason.EXTRACTOR_VERSION_MISMATCH in mismatch_result.review_reasons


def test_untyped_or_unsupported_rule_exception_is_quarantined() -> None:
    result = RegulationEvidenceService(
        StaticAdapter(
            regulation_output(
                claims=[
                    {
                        "rule_type": "NO_PARKING",
                        "exceptions": [
                            {
                                "exception_type": "MAGIC_OVERRIDE",
                                "parameters": {"anything": True},
                            }
                        ],
                        "extraction_confidence": 0.95,
                    }
                ]
            )
        )
    ).extract(source())

    assert result.disposition is ExtractionDisposition.QUARANTINED
    assert result.evidence is None
    assert result.proposed_rules == []


def test_adapter_failure_is_quarantined_without_leaking_exception_message() -> None:
    result = RegulationEvidenceService(FailingAdapter()).extract(source())

    assert result.disposition is ExtractionDisposition.QUARANTINED
    assert result.validation_errors == ["adapter:TimeoutError"]
    assert "untrusted provider" not in result.model_dump_json()


def test_community_claims_are_low_authority_evidence_and_never_rules() -> None:
    adapter = StaticAdapter(
        {
            "schema_version": "phase8-community-response-v1",
            "extractor_version": "fixture-extractor-v1",
            "claims": [
                {
                    "claim_type": "FREE_AFTER",
                    "location_text": "near the library",
                    "statement": "Reported free after 6 PM; verify signs.",
                    "local_time": "18:00:00",
                    "extraction_confidence": 0.72,
                }
            ],
        }
    )
    result = CommunityEvidenceService(adapter).extract(source(EvidenceSourceType.COMMUNITY))

    assert result.evidence is not None
    assert result.evidence.reliability_tier is EvidenceReliabilityTier.C
    assert result.proposed_rules == []
    assert result.review_reasons == [
        ReviewReason.HUMAN_VERIFICATION_REQUIRED,
        ReviewReason.LOW_AUTHORITY_SOURCE,
        ReviewReason.LOW_EXTRACTION_CONFIDENCE,
    ]


def test_vision_claims_remain_imagery_inference_and_cannot_publish_rules() -> None:
    adapter = StaticAdapter(
        {
            "schema_version": "phase8-vision-response-v1",
            "extractor_version": "fixture-extractor-v1",
            "sign_claims": [
                {"rule_type": "TIME_LIMIT", "max_duration_min": 120, "extraction_confidence": 0.9}
            ],
            "physical_claims": [
                {
                    "claim_type": "SIGN_PRESENT",
                    "observation": "A parking sign may be present.",
                    "extraction_confidence": 0.84,
                }
            ],
        }
    )
    result = VisionEvidenceService(adapter).extract(source(EvidenceSourceType.IMAGERY_INFERENCE))

    assert result.evidence is not None
    assert result.evidence.source_type is EvidenceSourceType.IMAGERY_INFERENCE
    assert result.evidence.reliability_tier is EvidenceReliabilityTier.D
    assert len(result.proposed_rules) == 2
    with pytest.raises(ValueError, match="only regulation extraction"):
        approve_extraction(
            result,
            reviewer_id="reviewer-1",
            reviewed_at=REVIEWED,
            scope=ApprovalScope.EVIDENCE_AND_RULES,
        )
    approved = approve_extraction(
        result,
        reviewer_id="reviewer-1",
        reviewed_at=REVIEWED,
    )
    assert approved.rules == []


def test_human_review_is_required_before_regulation_rules_become_persistable() -> None:
    result = RegulationEvidenceService(StaticAdapter(regulation_output())).extract(source())
    approved = approve_extraction(
        result,
        reviewer_id="parking-policy-reviewer",
        reviewed_at=REVIEWED,
        scope=ApprovalScope.EVIDENCE_AND_RULES,
    )
    repeated = approve_extraction(
        result,
        reviewer_id="parking-policy-reviewer",
        reviewed_at=REVIEWED,
        scope=ApprovalScope.EVIDENCE_AND_RULES,
    )

    assert approved == repeated
    assert approved.evidence.evidence_id.startswith("evidence_reviewed_")
    assert all(rule.source_evidence_id == approved.evidence.evidence_id for rule in approved.rules)
    review_claim = approved.evidence.normalized_claims[-1]
    assert review_claim.claim_type == "HUMAN_REVIEW_APPROVAL"
    assert review_claim.attributes["reviewer_id"] == "parking-policy-reviewer"


def test_approval_identity_is_stable_across_equivalent_timezones() -> None:
    result = RegulationEvidenceService(StaticAdapter(regulation_output())).extract(source())
    central_time = REVIEWED.astimezone(timezone(-timedelta(hours=5)))

    utc_approval = approve_extraction(
        result,
        reviewer_id="parking-policy-reviewer",
        reviewed_at=REVIEWED,
        scope=ApprovalScope.EVIDENCE_AND_RULES,
    )
    central_approval = approve_extraction(
        result,
        reviewer_id="parking-policy-reviewer",
        reviewed_at=central_time,
        scope=ApprovalScope.EVIDENCE_AND_RULES,
    )

    assert central_approval == utc_approval
    assert central_approval.reviewed_at.tzinfo is UTC


def test_quarantined_result_cannot_be_approved() -> None:
    quarantined = EvidenceExtractionResult(
        result_id="extract-quarantined",
        service_kind="REGULATION",
        disposition="QUARANTINED",
        extractor_version="fixture-extractor-v1",
        review_reasons=["INVALID_EXTRACTOR_OUTPUT"],
        validation_errors=["claims.0:missing"],
    )

    with pytest.raises(ValueError, match="cannot be approved"):
        approve_extraction(
            quarantined,
            reviewer_id="reviewer-1",
            reviewed_at=REVIEWED,
        )


@pytest.mark.parametrize(
    "source_type",
    [
        EvidenceSourceType.COMMUNITY,
        EvidenceSourceType.WEB,
        EvidenceSourceType.IMAGERY_INFERENCE,
    ],
)
def test_low_authority_or_third_party_raw_content_cannot_be_persisted(
    source_type: EvidenceSourceType,
) -> None:
    material = source(source_type, storage=EvidenceStoragePolicy.PERSIST)
    service: object
    if source_type is EvidenceSourceType.COMMUNITY:
        service = CommunityEvidenceService(StaticAdapter({}))
    elif source_type is EvidenceSourceType.IMAGERY_INFERENCE:
        service = VisionEvidenceService(StaticAdapter({}))
    else:
        service = RegulationEvidenceService(StaticAdapter({}))

    with pytest.raises(ValueError, match="cannot use the PERSIST"):
        service.extract(material)  # type: ignore[attr-defined]


def test_source_metadata_requires_aware_ordered_timestamps() -> None:
    data = source().model_dump()
    data["retrieved_at"] = datetime(2026, 9, 21, 12)
    with pytest.raises(ValidationError, match="timezone"):
        SourceMaterial.model_validate(data)

    data = source().model_dump()
    data["observed_at"] = datetime(2026, 9, 22, tzinfo=UTC)
    with pytest.raises(ValidationError, match="after retrieved"):
        SourceMaterial.model_validate(data)
