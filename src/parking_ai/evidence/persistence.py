"""Persistence boundary for explicitly human-approved Phase 8 evidence."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from parking_ai.agents import ApprovedEvidenceBundle
from parking_ai.database.models import (
    EvidenceModel,
    ParkingRuleModel,
    parking_source_segments,
)


class EvidencePersistenceConflictError(RuntimeError):
    """Raised when a stable reviewed identity collides with different stored content."""


def persist_approved_evidence(session: Session, bundle: ApprovedEvidenceBundle) -> None:
    """Stage an approved bundle idempotently without committing the caller's transaction.

    Extraction results and quarantined output are intentionally not accepted here. The caller
    must cross the explicit human-review boundary and supply an ``ApprovedEvidenceBundle``.
    Existing GIS-owned segment fields are never updated.
    """

    if not isinstance(bundle, ApprovedEvidenceBundle):
        raise TypeError("only an ApprovedEvidenceBundle may cross the persistence boundary")

    evidence = bundle.evidence
    _lock_reviewed_identity(session, evidence.evidence_id)
    evidence_values: dict[str, object] = {
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
    inserted_evidence_id = session.scalar(
        evidence_insert.on_conflict_do_nothing(
            index_elements=[EvidenceModel.evidence_id]
        ).returning(EvidenceModel.evidence_id)
    )
    if inserted_evidence_id is None:
        existing_evidence = session.get(EvidenceModel, evidence.evidence_id)
        if existing_evidence is None or not _evidence_matches(existing_evidence, evidence_values):
            raise EvidencePersistenceConflictError(
                "reviewed evidence ID is already associated with different content"
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
        rule_values: dict[str, object] = {
            "rule_id": rule.rule_id,
            "segment_id": rule.segment_id,
            "rule_type": rule.rule_type,
            "days": [day.value for day in rule.days],
            "start_time": rule.start_time,
            "end_time": rule.end_time,
            "effective_start_date": rule.effective_start_date,
            "effective_end_date": rule.effective_end_date,
            "max_duration_min": rule.max_duration_min,
            "payment_required": rule.payment_required,
            "permit_required": rule.permit_required,
            "permit_type": rule.permit_type,
            "exceptions": [exception.model_dump(mode="json") for exception in rule.exceptions],
            "source_evidence_id": rule.source_evidence_id,
            "extraction_confidence": rule.extraction_confidence,
        }
        rule_insert = insert(ParkingRuleModel).values(**rule_values)
        inserted_rule_id = session.scalar(
            rule_insert.on_conflict_do_nothing(index_elements=[ParkingRuleModel.rule_id]).returning(
                ParkingRuleModel.rule_id
            )
        )
        if inserted_rule_id is None:
            existing_rule = session.get(ParkingRuleModel, rule.rule_id)
            if existing_rule is None or not _rule_matches(existing_rule, rule_values):
                raise EvidencePersistenceConflictError(
                    "reviewed rule ID is already associated with different content"
                )

    session.flush()
    validate_persisted_approved_evidence(session, bundle)


def validate_persisted_approved_evidence(
    session: Session,
    bundle: ApprovedEvidenceBundle,
) -> None:
    """Verify that stored reviewed publication exactly matches its approved bundle."""

    evidence = bundle.evidence
    evidence_values: dict[str, object] = {
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
    stored_evidence = session.get(EvidenceModel, evidence.evidence_id)
    if stored_evidence is None or not _evidence_matches(stored_evidence, evidence_values):
        raise EvidencePersistenceConflictError(
            "stored reviewed evidence does not match approved content"
        )

    stored_segment_ids = list(
        session.scalars(
            select(parking_source_segments.c.segment_id)
            .where(parking_source_segments.c.evidence_id == evidence.evidence_id)
            .order_by(parking_source_segments.c.segment_id)
        )
    )
    if stored_segment_ids != sorted(evidence.segment_ids):
        raise EvidencePersistenceConflictError(
            "reviewed evidence segment bindings do not match approved content"
        )
    stored_rule_ids = list(
        session.scalars(
            select(ParkingRuleModel.rule_id)
            .where(ParkingRuleModel.source_evidence_id == evidence.evidence_id)
            .order_by(ParkingRuleModel.rule_id)
        )
    )
    if stored_rule_ids != sorted(rule.rule_id for rule in bundle.rules):
        raise EvidencePersistenceConflictError(
            "stored reviewed rules do not match the approved bundle"
        )
    expected_rules = {rule.rule_id: rule for rule in bundle.rules}
    for stored_rule_id in stored_rule_ids:
        stored_rule = session.get(ParkingRuleModel, stored_rule_id)
        expected_rule = expected_rules[stored_rule_id]
        rule_values: dict[str, object] = {
            "rule_id": expected_rule.rule_id,
            "segment_id": expected_rule.segment_id,
            "rule_type": expected_rule.rule_type,
            "days": [day.value for day in expected_rule.days],
            "start_time": expected_rule.start_time,
            "end_time": expected_rule.end_time,
            "effective_start_date": expected_rule.effective_start_date,
            "effective_end_date": expected_rule.effective_end_date,
            "max_duration_min": expected_rule.max_duration_min,
            "payment_required": expected_rule.payment_required,
            "permit_required": expected_rule.permit_required,
            "permit_type": expected_rule.permit_type,
            "exceptions": [
                exception.model_dump(mode="json") for exception in expected_rule.exceptions
            ],
            "source_evidence_id": expected_rule.source_evidence_id,
            "extraction_confidence": expected_rule.extraction_confidence,
        }
        if stored_rule is None or not _rule_matches(stored_rule, rule_values):
            raise EvidencePersistenceConflictError(
                "stored reviewed rule does not match approved content"
            )


def _evidence_matches(model: EvidenceModel, values: dict[str, object]) -> bool:
    return all(getattr(model, field_name) == value for field_name, value in values.items())


def _rule_matches(model: ParkingRuleModel, values: dict[str, object]) -> bool:
    return all(getattr(model, field_name) == value for field_name, value in values.items())


def _lock_reviewed_identity(session: Session, evidence_id: str) -> None:
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(evidence_id, 0))))
