from datetime import UTC, datetime

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import Engine, func, select
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
    ParkingRuleModel,
    ParkingSegmentModel,
    parking_source_segments,
)
from parking_ai.domain import (
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalState,
    ParkingSegment,
    PhysicalState,
    SegmentSide,
    UserProfile,
)
from parking_ai.evidence import persist_approved_evidence
from parking_ai.regulations import build_regulation_engine

pytestmark = pytest.mark.integration
RETRIEVED = datetime(2026, 9, 20, 12, tzinfo=UTC)
REVIEWED = datetime(2026, 9, 20, 13, tzinfo=UTC)
ARRIVAL = datetime(2026, 9, 21, 15, tzinfo=UTC)


class RegulationFixtureAdapter:
    extractor_version = "phase8-fixture-regulation-v1"

    def extract(self, request: ExtractionRequest) -> object:
        assert request.source.content
        return {
            "schema_version": "phase8-regulation-response-v1",
            "extractor_version": self.extractor_version,
            "claims": [
                {
                    "rule_type": "NO_PARKING",
                    "exceptions": [
                        {
                            "exception_type": "PERMIT",
                            "parameters": {"permit_type": "PHASE8-TEST"},
                        }
                    ],
                    "extraction_confidence": 0.97,
                }
            ],
        }


def test_approved_regulation_persistence_is_idempotent_and_engine_ready(engine: Engine) -> None:
    segment = ParkingSegment(
        segment_id="phase8-segment",
        geometry={
            "type": "LineString",
            "coordinates": [(-96.784, 32.842), (-96.783, 32.843)],
        },
        street_name="Phase 8 Test Street",
        side=SegmentSide.LEFT,
        length_m=50.0,
        physical_state=PhysicalState.UNKNOWN,
        legal_state=LegalState.UNKNOWN,
        free_state=FreeState.UNKNOWN,
        legal_confidence=0.0,
        data_freshness=RETRIEVED,
    )

    with Session(engine) as session:
        transaction = session.begin()
        session.add(
            ParkingSegmentModel(
                segment_id=segment.segment_id,
                geometry=WKTElement("LINESTRING(-96.784 32.842, -96.783 32.843)", srid=4326),
                street_name=segment.street_name,
                side=segment.side,
                length_m=segment.length_m,
                estimated_capacity=None,
                road_type="residential",
                physical_state=segment.physical_state,
                legal_state=segment.legal_state,
                free_state=segment.free_state,
                legal_confidence=segment.legal_confidence,
                data_freshness=segment.data_freshness,
            )
        )
        session.flush()

        before = build_regulation_engine(session, [segment.segment_id]).evaluate_legality(
            segment,
            UserProfile(requested_parking_duration_min=60),
            ARRIVAL,
        )
        assert before.legal_state is LegalState.UNKNOWN

        extracted = RegulationEvidenceService(RegulationFixtureAdapter()).extract(
            SourceMaterial(
                source_type=EvidenceSourceType.OFFICIAL_CODE,
                source_uri_or_identifier="fixture://official-code/phase8",
                publisher="Fixture City",
                retrieved_at=RETRIEVED,
                raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
                segment_ids=[segment.segment_id],
                content="Parking prohibited at all times for the Phase 8 persistence test.",
            )
        )
        approved = approve_extraction(
            extracted,
            reviewer_id="integration-reviewer",
            reviewed_at=REVIEWED,
            scope=ApprovalScope.EVIDENCE_AND_RULES,
        )
        persist_approved_evidence(session, approved)
        persist_approved_evidence(session, approved)
        session.flush()

        assert (
            session.scalar(
                select(func.count())
                .select_from(EvidenceModel)
                .where(EvidenceModel.evidence_id == approved.evidence.evidence_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ParkingRuleModel)
                .where(ParkingRuleModel.source_evidence_id == approved.evidence.evidence_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(parking_source_segments)
                .where(parking_source_segments.c.evidence_id == approved.evidence.evidence_id)
            )
            == 1
        )
        persisted_rule = session.scalar(
            select(ParkingRuleModel).where(
                ParkingRuleModel.source_evidence_id == approved.evidence.evidence_id
            )
        )
        assert persisted_rule is not None
        assert persisted_rule.exceptions == [
            {
                "exception_type": "PERMIT",
                "parameters": {"permit_type": "PHASE8-TEST"},
            }
        ]

        persisted_segment = session.get(ParkingSegmentModel, segment.segment_id)
        assert persisted_segment is not None
        assert persisted_segment.legal_state is LegalState.UNKNOWN
        assert persisted_segment.free_state is FreeState.UNKNOWN
        assert persisted_segment.availability_probability is None

        after = build_regulation_engine(session, [segment.segment_id]).evaluate_legality(
            segment,
            UserProfile(requested_parking_duration_min=60),
            ARRIVAL,
        )
        assert after.legal_state is LegalState.ILLEGAL
        assert after.evidence_refs == [approved.evidence.evidence_id]

        transaction.rollback()
