from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from parking_ai.agents import (
    ApprovalScope,
    ExtractionRequest,
    RegulationEvidenceService,
    SourceMaterial,
    approve_extraction,
)
from parking_ai.database.models import (
    EvidenceModel,
    EvidenceReviewEventModel,
    EvidenceReviewQueueModel,
    ParkingRuleModel,
    ParkingSegmentModel,
    parking_source_segments,
)
from parking_ai.domain import (
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalState,
    PhysicalState,
    SegmentSide,
)
from parking_ai.evidence import (
    EvidencePersistenceConflictError,
    ReviewActor,
    ReviewQueueAction,
    ReviewQueueService,
    ReviewQueueStatus,
    ReviewReasonCode,
    ReviewRole,
    SQLAlchemyReviewQueueRepository,
    persist_approved_evidence,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 22, 15, tzinfo=UTC)


class RegulationFixtureAdapter:
    extractor_version = "v1a-integration-extractor-v1"

    def extract(self, request: ExtractionRequest) -> object:
        assert request.source.content
        return {
            "schema_version": "phase8-regulation-response-v1",
            "extractor_version": self.extractor_version,
            "claims": [
                {
                    "rule_type": "NO_PARKING",
                    "extraction_confidence": 0.98,
                }
            ],
        }


def test_review_approval_persists_publication_state_and_audit_atomically(
    engine: Engine,
) -> None:
    segment_id = "v1a-review-segment"
    with Session(engine) as session:
        transaction = session.begin()
        session.add(
            ParkingSegmentModel(
                segment_id=segment_id,
                geometry=WKTElement(
                    "LINESTRING(-96.784 32.842, -96.783 32.843)",
                    srid=4326,
                ),
                street_name="V1A Review Street",
                side=SegmentSide.LEFT,
                length_m=50.0,
                estimated_capacity=None,
                road_type="residential",
                physical_state=PhysicalState.UNKNOWN,
                legal_state=LegalState.UNKNOWN,
                free_state=FreeState.UNKNOWN,
                legal_confidence=0.0,
                data_freshness=NOW - timedelta(days=1),
            )
        )
        result = RegulationEvidenceService(RegulationFixtureAdapter()).extract(
            SourceMaterial(
                source_type=EvidenceSourceType.OFFICIAL_CODE,
                source_uri_or_identifier="fixture://v1a/official-code",
                publisher="Fixture City",
                retrieved_at=NOW - timedelta(hours=1),
                raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
                segment_ids=[segment_id],
                content="Parking prohibited at all times.",
            )
        )
        service = ReviewQueueService(
            SQLAlchemyReviewQueueRepository(session),
            clock=lambda: NOW,
        )
        submitter = ReviewActor(actor_id="ingestion-worker", roles=(ReviewRole.SUBMITTER,))
        publisher = ReviewActor(
            actor_id="regulation-publisher",
            roles=(ReviewRole.REGULATION_PUBLISHER,),
        )

        submitted = service.submit(
            result,
            actor=submitter,
            reason_code=ReviewReasonCode.EXTRACTION_READY,
            idempotency_key="v1a-integration-submit",
        )
        claimed = service.act(
            submitted.item.queue_item_id,
            ReviewQueueAction.CLAIM,
            actor=publisher,
            reason_code=ReviewReasonCode.REVIEW_STARTED,
            idempotency_key="v1a-integration-claim",
        )

        expected_bundle = approve_extraction(
            result,
            reviewer_id=publisher.actor_id,
            reviewed_at=NOW,
            scope=ApprovalScope.EVIDENCE_AND_RULES,
        )
        collision = EvidenceModel(
            evidence_id=expected_bundle.evidence.evidence_id,
            source_type=expected_bundle.evidence.source_type,
            source_uri_or_identifier="fixture://v1a/conflicting-content",
            publisher=expected_bundle.evidence.publisher,
            published_at=expected_bundle.evidence.published_at,
            observed_at=expected_bundle.evidence.observed_at,
            retrieved_at=expected_bundle.evidence.retrieved_at,
            raw_storage_policy=expected_bundle.evidence.raw_storage_policy,
            normalized_claims=[
                claim.model_dump(mode="json")
                for claim in expected_bundle.evidence.normalized_claims
            ],
            reliability_tier=expected_bundle.evidence.reliability_tier,
            extractor_version=expected_bundle.evidence.extractor_version,
            content_hash=expected_bundle.evidence.content_hash,
        )
        session.add(collision)
        session.flush()
        with pytest.raises(EvidencePersistenceConflictError):
            service.act(
                submitted.item.queue_item_id,
                ReviewQueueAction.APPROVE_RULES,
                actor=publisher,
                reason_code=ReviewReasonCode.REGULATION_VERIFIED,
                idempotency_key="v1a-integration-approve",
            )
        assert service.get(submitted.item.queue_item_id) == claimed
        assert session.scalar(select(func.count()).select_from(EvidenceReviewEventModel)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(ParkingRuleModel)
                .where(ParkingRuleModel.source_evidence_id == expected_bundle.evidence.evidence_id)
            )
            == 0
        )

        session.delete(collision)
        session.flush()
        approved = service.act(
            submitted.item.queue_item_id,
            ReviewQueueAction.APPROVE_RULES,
            actor=publisher,
            reason_code=ReviewReasonCode.REGULATION_VERIFIED,
            idempotency_key="v1a-integration-approve",
        )
        session.flush()
        session.execute(
            text("SET CONSTRAINTS trg_require_evidence_review_projection_event IMMEDIATE")
        )
        session.execute(
            text("SET CONSTRAINTS trg_require_evidence_review_projection_event DEFERRED")
        )
        session.expire_all()

        reloaded = service.get(submitted.item.queue_item_id)
        assert submitted.status is ReviewQueueStatus.PENDING
        assert claimed.status is ReviewQueueStatus.IN_REVIEW
        assert reloaded == approved
        assert approved.status is ReviewQueueStatus.APPROVED
        assert approved.approved_bundle is not None
        evidence_id = approved.approved_bundle.evidence.evidence_id

        assert session.scalar(select(func.count()).select_from(EvidenceReviewQueueModel)) == 1
        assert session.scalar(select(func.count()).select_from(EvidenceReviewEventModel)) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(EvidenceModel)
                .where(EvidenceModel.evidence_id == evidence_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ParkingRuleModel)
                .where(ParkingRuleModel.source_evidence_id == evidence_id)
            )
            == 1
        )

        # A publication retry after terminal approval must validate and return before issuing
        # INSERT statements, because the database immutability triggers intentionally reject even
        # no-op ON CONFLICT inserts for approved evidence identities.
        persist_approved_evidence(session, approved.approved_bundle)
        session.flush()
        conflicting_bundle = approved.approved_bundle.model_copy(
            update={
                "evidence": approved.approved_bundle.evidence.model_copy(
                    update={"publisher": "Conflicting Fixture City"},
                    deep=True,
                )
            },
            deep=True,
        )
        with pytest.raises(
            EvidencePersistenceConflictError,
            match="stored reviewed evidence does not match approved content",
        ):
            persist_approved_evidence(session, conflicting_bundle)

        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                update(EvidenceModel)
                .where(EvidenceModel.evidence_id == evidence_id)
                .values(publisher="tampered")
            )
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                update(ParkingRuleModel)
                .where(ParkingRuleModel.source_evidence_id == evidence_id)
                .values(extraction_confidence=0.01)
            )
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                delete(parking_source_segments).where(
                    parking_source_segments.c.evidence_id == evidence_id
                )
            )
        session.expire_all()
        assert service.get(submitted.item.queue_item_id) == approved

        repeated = service.act(
            submitted.item.queue_item_id,
            ReviewQueueAction.APPROVE_RULES,
            actor=publisher,
            reason_code=ReviewReasonCode.REGULATION_VERIFIED,
            idempotency_key="v1a-integration-approve",
        )
        assert repeated == approved
        assert session.scalar(select(func.count()).select_from(EvidenceReviewEventModel)) == 3

        transaction.rollback()
