"""Persistence boundary for explicitly human-approved Phase 8 evidence."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from parking_ai.agents import ApprovedEvidenceBundle
from parking_ai.database.models import (
    EvidenceModel,
    ParkingRuleModel,
    parking_source_segments,
)


def persist_approved_evidence(session: Session, bundle: ApprovedEvidenceBundle) -> None:
    """Stage an approved bundle idempotently without committing the caller's transaction.

    Extraction results and quarantined output are intentionally not accepted here. The caller
    must cross the explicit human-review boundary and supply an ``ApprovedEvidenceBundle``.
    Existing GIS-owned segment fields are never updated.
    """

    if not isinstance(bundle, ApprovedEvidenceBundle):
        raise TypeError("only an ApprovedEvidenceBundle may cross the persistence boundary")

    evidence = bundle.evidence
    evidence_values = {
        "evidence_id": evidence.evidence_id,
        "source_type": evidence.source_type,
        "source_uri_or_identifier": evidence.source_uri_or_identifier,
        "publisher": evidence.publisher,
        "published_at": evidence.published_at,
        "observed_at": evidence.observed_at,
        "retrieved_at": evidence.retrieved_at,
        "raw_storage_policy": evidence.raw_storage_policy,
        "normalized_claims": [
            claim.model_dump(mode="json") for claim in evidence.normalized_claims
        ],
        "reliability_tier": evidence.reliability_tier,
        "extractor_version": evidence.extractor_version,
        "content_hash": evidence.content_hash,
    }
    evidence_insert = insert(EvidenceModel).values(**evidence_values)
    session.execute(
        evidence_insert.on_conflict_do_nothing(index_elements=[EvidenceModel.evidence_id])
    )

    for segment_id in evidence.segment_ids:
        association_insert = insert(parking_source_segments).values(
            evidence_id=evidence.evidence_id,
            segment_id=segment_id,
        )
        session.execute(
            association_insert.on_conflict_do_nothing(
                index_elements=[
                    parking_source_segments.c.evidence_id,
                    parking_source_segments.c.segment_id,
                ]
            )
        )

    for rule in bundle.rules:
        rule_insert = insert(ParkingRuleModel).values(
            rule_id=rule.rule_id,
            segment_id=rule.segment_id,
            rule_type=rule.rule_type,
            days=[day.value for day in rule.days],
            start_time=rule.start_time,
            end_time=rule.end_time,
            effective_start_date=rule.effective_start_date,
            effective_end_date=rule.effective_end_date,
            max_duration_min=rule.max_duration_min,
            payment_required=rule.payment_required,
            permit_required=rule.permit_required,
            permit_type=rule.permit_type,
            exceptions=[exception.model_dump(mode="json") for exception in rule.exceptions],
            source_evidence_id=rule.source_evidence_id,
            extraction_confidence=rule.extraction_confidence,
        )
        session.execute(
            rule_insert.on_conflict_do_nothing(index_elements=[ParkingRuleModel.rule_id])
        )
