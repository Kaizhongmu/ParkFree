from datetime import UTC, date, datetime, time

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from parking_ai.database.models import (
    DestinationAccessPointModel,
    DestinationModel,
    EvidenceModel,
    ParkingOutcomeModel,
    ParkingRuleModel,
    ParkingSegmentModel,
    SearchRouteStepModel,
    SearchSessionModel,
)
from parking_ai.domain.enums import (
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

NOW = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)
pytestmark = pytest.mark.integration


def test_phase_one_entities_and_relationships_round_trip(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        destination = DestinationModel(
            destination_id="destination-test",
            name="Test Destination",
            geometry=WKTElement("POINT(-96.784 32.842)", srid=4326),
            destination_type="test",
        )
        access_point = DestinationAccessPointModel(
            access_point_id="access-test",
            name="Main Entrance",
            geometry=WKTElement("POINT(-96.7839 32.8421)", srid=4326),
        )
        destination.access_points.append(access_point)

        segment = ParkingSegmentModel(
            segment_id="segment-test",
            geometry=WKTElement("LINESTRING(-96.784 32.842, -96.783 32.843)", srid=4326),
            street_name="Test Street",
            side=SegmentSide.LEFT,
            length_m=45.0,
            estimated_capacity=5.0,
            road_type="residential",
            physical_state=PhysicalState.PARKABLE,
            legal_state=LegalState.UNKNOWN,
            free_state=FreeState.UNKNOWN,
            legal_confidence=0.0,
            data_freshness=NOW,
        )
        evidence = EvidenceModel(
            evidence_id="evidence-test",
            source_type=EvidenceSourceType.OFFICIAL_CODE,
            source_uri_or_identifier="test-code-section",
            publisher="Test City",
            retrieved_at=NOW,
            raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
            normalized_claims=[{"claim_type": "NO_PARKING", "attributes": {}}],
            reliability_tier=EvidenceReliabilityTier.A,
            content_hash="test-hash",
        )
        evidence.segments.append(segment)
        rule = ParkingRuleModel(
            rule_id="rule-test",
            rule_type=ParkingRuleType.NO_PARKING,
            days=["MON", "TUE"],
            start_time=time(8),
            end_time=time(18),
            effective_start_date=date(2026, 1, 1),
            max_duration_min=None,
            payment_required=False,
            permit_required=False,
            exceptions=[],
            extraction_confidence=0.95,
        )
        rule.segment = segment
        rule.source_evidence = evidence

        search_session = SearchSessionModel(
            session_id="session-test",
            destination=destination,
            origin=WKTElement("POINT(-96.790 32.840)", srid=4326),
            requested_arrival_time=NOW,
            free_only=True,
            max_walk_minutes=8.0,
            max_candidates=20,
            candidate_segment_ids=[segment.segment_id],
            status=SearchSessionStatus.PLANNED,
            rule_engine_version="rules-test-v0",
            availability_model_version="availability-test-v0",
            route_matrix_version="matrix-test-v0",
            optimizer_version="optimizer-test-v0",
            created_at=NOW,
        )
        route_step = SearchRouteStepModel(
            route_step_id="step-test",
            segment=segment,
            step_order=0,
            legal_state=LegalState.LEGAL,
            free_state=FreeState.FREE,
            legal_confidence=0.95,
            availability_probability=0.6,
            drive_eta_min=3.0,
            walk_min=4.0,
            evidence_refs=[evidence.evidence_id],
            rule_engine_version="rules-test-v0",
            availability_model_version="availability-test-v0",
        )
        outcome = ParkingOutcomeModel(
            outcome_id="outcome-test",
            segment=segment,
            success=True,
            occurred_at=NOW,
            search_duration_seconds=40,
            route_step_order=0,
        )
        search_session.route_steps.append(route_step)
        search_session.outcomes.append(outcome)
        session.add(search_session)
        session.flush()

        loaded_session = session.scalar(
            select(SearchSessionModel).where(SearchSessionModel.session_id == "session-test")
        )
        loaded_rule = session.scalar(
            select(ParkingRuleModel).where(ParkingRuleModel.rule_id == "rule-test")
        )
        line_wkt = session.scalar(
            select(func.ST_AsText(ParkingSegmentModel.geometry)).where(
                ParkingSegmentModel.segment_id == "segment-test"
            )
        )

        assert loaded_session is not None
        assert loaded_session.destination.access_points[0].access_point_id == "access-test"
        assert loaded_session.route_steps[0].segment.street_name == "Test Street"
        assert loaded_session.outcomes[0].success is True
        assert loaded_session.replayable is False
        assert loaded_session.response_snapshot is None
        assert loaded_session.requested_arrival_time.utcoffset() is not None
        assert loaded_rule is not None
        assert loaded_rule.source_evidence.segments[0].segment_id == "segment-test"
        assert loaded_rule.segment.rules[0].rule_id == "rule-test"
        assert line_wkt == "LINESTRING(-96.784 32.842,-96.783 32.843)"

        session.rollback()
