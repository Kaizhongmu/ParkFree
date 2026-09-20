from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import timedelta, tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from parking_ai.database.models import (
    EvidenceModel,
    ParkingRuleModel,
    parking_source_segments,
)
from parking_ai.domain import Evidence, EvidenceSourceType, ParkingRule
from parking_ai.domain.schemas import NormalizedClaim
from parking_ai.regulations.engine import (
    DEFAULT_REGULATION_TIMEZONE,
    RULE_ENGINE_VERSION,
    DeterministicRegulationEngine,
)


def build_regulation_engine(
    session: Session,
    candidate_segment_ids: Iterable[str],
    *,
    rule_engine_version: str = RULE_ENGINE_VERSION,
    timezone: tzinfo = DEFAULT_REGULATION_TIMEZONE,
    evidence_max_age: Mapping[EvidenceSourceType, timedelta | None] | None = None,
) -> DeterministicRegulationEngine:
    """Build a read-only engine snapshot for exactly the requested candidate segments."""

    segment_ids = tuple(sorted(set(candidate_segment_ids)))
    if any(not segment_id.strip() for segment_id in segment_ids):
        raise ValueError("candidate segment IDs must not be blank")
    if not segment_ids:
        return DeterministicRegulationEngine(
            (),
            (),
            rule_engine_version=rule_engine_version,
            timezone=timezone,
            evidence_max_age=evidence_max_age,
        )

    rule_models = session.scalars(
        select(ParkingRuleModel)
        .where(ParkingRuleModel.segment_id.in_(segment_ids))
        .order_by(ParkingRuleModel.rule_id)
    ).all()
    rules = [_hydrate_rule(model) for model in rule_models]

    evidence_ids = tuple(sorted({rule.source_evidence_id for rule in rules}))
    evidence_models = (
        session.scalars(
            select(EvidenceModel)
            .where(EvidenceModel.evidence_id.in_(evidence_ids))
            .order_by(EvidenceModel.evidence_id)
        ).all()
        if evidence_ids
        else []
    )
    segment_refs: defaultdict[str, list[str]] = defaultdict(list)
    if evidence_ids:
        association_rows = session.execute(
            select(
                parking_source_segments.c.evidence_id,
                parking_source_segments.c.segment_id,
            )
            .where(parking_source_segments.c.evidence_id.in_(evidence_ids))
            .order_by(
                parking_source_segments.c.evidence_id,
                parking_source_segments.c.segment_id,
            )
        ).all()
        for evidence_id, segment_id in association_rows:
            segment_refs[evidence_id].append(segment_id)

    evidence = [
        _hydrate_evidence(model, segment_refs.get(model.evidence_id, []))
        for model in evidence_models
    ]
    return DeterministicRegulationEngine(
        rules,
        evidence,
        rule_engine_version=rule_engine_version,
        timezone=timezone,
        evidence_max_age=evidence_max_age,
    )


def _hydrate_rule(model: ParkingRuleModel) -> ParkingRule:
    return ParkingRule(
        rule_id=model.rule_id,
        segment_id=model.segment_id,
        rule_type=model.rule_type,
        days=model.days,
        start_time=model.start_time,
        end_time=model.end_time,
        effective_start_date=model.effective_start_date,
        effective_end_date=model.effective_end_date,
        max_duration_min=model.max_duration_min,
        payment_required=model.payment_required,
        permit_required=model.permit_required,
        permit_type=model.permit_type,
        exceptions=model.exceptions,
        source_evidence_id=model.source_evidence_id,
        extraction_confidence=model.extraction_confidence,
    )


def _hydrate_evidence(model: EvidenceModel, segment_ids: list[str]) -> Evidence:
    return Evidence(
        evidence_id=model.evidence_id,
        source_type=model.source_type,
        source_uri_or_identifier=model.source_uri_or_identifier,
        publisher=model.publisher,
        published_at=model.published_at,
        observed_at=model.observed_at,
        retrieved_at=model.retrieved_at,
        raw_storage_policy=model.raw_storage_policy,
        segment_ids=segment_ids,
        normalized_claims=[
            NormalizedClaim.model_validate(claim) for claim in model.normalized_claims
        ],
        reliability_tier=model.reliability_tier,
        extractor_version=model.extractor_version,
        content_hash=model.content_hash,
    )
