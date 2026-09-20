from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from parking_ai.config import Settings
from parking_ai.database.models import (
    EvidenceModel,
    ParkingRuleModel,
    ParkingSegmentModel,
    SearchRouteStepModel,
    SearchSessionModel,
    parking_source_segments,
)
from parking_ai.domain import (
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalState,
    ParkingRuleType,
    SearchConstraints,
)
from parking_ai.gis import (
    DeterministicCandidateSegmentService,
    load_smu_gis_fixture,
    upsert_gis_slice,
)
from parking_ai.main import create_app

pytestmark = pytest.mark.integration

ARRIVAL = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)


def test_default_phase6_runtime_persists_and_replays_complete_search(
    engine: Engine,
    migrated_database: str,
) -> None:
    fixture = load_smu_gis_fixture()
    generator = DeterministicCandidateSegmentService(
        fixture.osm.roads,
        evidence_id=fixture.osm.evidence.evidence_id,
        data_freshness=fixture.osm.observed_at,
    )
    all_segments = generator.get_candidate_segments(
        fixture.destination,
        SearchConstraints(max_walk_minutes=30, max_candidates=1_000),
    )
    selected_segment = generator.get_candidate_segments(
        fixture.destination,
        SearchConstraints(max_walk_minutes=8, max_candidates=20),
    )[0]
    regulation_evidence_id = "evidence-phase6-api-test"
    regulation_rule_id = "rule-phase6-api-test"

    with Session(engine) as session, session.begin():
        upsert_gis_slice(session, fixture.destination, fixture.osm.evidence, all_segments)
        session.add(
            EvidenceModel(
                evidence_id=regulation_evidence_id,
                source_type=EvidenceSourceType.OFFICIAL_CODE,
                source_uri_or_identifier="test:phase6-explicit-free-rule",
                publisher="Phase 6 integration fixture",
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
                raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
                normalized_claims=[],
                reliability_tier=EvidenceReliabilityTier.A,
            )
        )
        session.flush()
        session.execute(
            parking_source_segments.insert().values(
                evidence_id=regulation_evidence_id,
                segment_id=selected_segment.segment_id,
            )
        )
        session.add(
            ParkingRuleModel(
                rule_id=regulation_rule_id,
                segment_id=selected_segment.segment_id,
                rule_type=ParkingRuleType.PAID,
                days=[],
                payment_required=False,
                permit_required=False,
                exceptions=[],
                source_evidence_id=regulation_evidence_id,
                extraction_confidence=0.99,
            )
        )

    settings = Settings(
        environment="test",
        log_level="CRITICAL",
        database_url=migrated_database,
        parking_fallback_latitude=33.0,
        parking_fallback_longitude=-97.0,
        parking_fallback_id="phase6-test-garage",
        parking_fallback_description="Use the configured Phase 6 test garage.",
    )
    client = TestClient(create_app(settings, clock=lambda: ARRIVAL))
    payload = {
        "origin": {"lat": 32.842, "lon": -96.784},
        "destination": {"destination_id": fixture.destination.destination_id},
        "arrival_time": ARRIVAL.isoformat(),
        "parking_duration_minutes": 60,
        "free_only": True,
        "max_walk_minutes": 8.0,
        "vehicle_profile": {"type": "passenger", "permit_types": []},
        "max_candidates": 20,
    }
    headers = {"Idempotency-Key": "phase6-api-integration-request"}

    first = client.post("/v1/parking/search", json=payload, headers=headers)
    second = client.post("/v1/parking/search", json=payload, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    body = first.json()
    assert body["replayable"] is True
    assert body["route"]["steps"][0]["segment_id"] == selected_segment.segment_id
    assert body["versions"]["route_matrix_provider"] == "local-straight-line-matrix-v1"
    assert "LOCAL_STRAIGHT_LINE_ROUTE_COST_APPROXIMATION" in body["warnings"]
    assert selected_segment.segment_id not in body["unknown_segment_ids"]
    assert any(
        decision["segment"]["segment_id"] == selected_segment.segment_id
        and decision["eligible"] is True
        and decision["legality"]["legal_state"] == "LEGAL"
        and decision["legality"]["free_state"] == "FREE"
        for decision in body["candidate_decisions"]
    )

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SearchSessionModel)) == 1
        assert session.scalar(select(func.count()).select_from(SearchRouteStepModel)) == 1
        stored = session.scalar(select(SearchSessionModel))
        assert stored is not None
        assert stored.replayable is True
        assert stored.response_snapshot == body
        persisted_segment = session.get(ParkingSegmentModel, selected_segment.segment_id)
        assert persisted_segment is not None
        assert persisted_segment.legal_state is LegalState.UNKNOWN
        assert persisted_segment.free_state is FreeState.UNKNOWN
        assert persisted_segment.availability_probability is None
