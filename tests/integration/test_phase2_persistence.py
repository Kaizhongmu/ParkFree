import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from parking_ai.database.models import (
    DestinationAccessPointModel,
    DestinationModel,
    EvidenceModel,
    ParkingSegmentModel,
    parking_source_segments,
)
from parking_ai.domain import FreeState, LegalState, SearchConstraints
from parking_ai.gis import (
    DeterministicCandidateSegmentService,
    load_smu_gis_fixture,
    upsert_gis_slice,
)

pytestmark = pytest.mark.integration


def test_phase_two_fixture_upsert_is_idempotent_after_fresh_migration(engine: Engine) -> None:
    fixture = load_smu_gis_fixture()
    service = DeterministicCandidateSegmentService(
        fixture.osm.roads,
        evidence_id=fixture.osm.evidence.evidence_id,
        data_freshness=fixture.osm.observed_at,
    )
    segments = service.get_candidate_segments(
        fixture.destination,
        SearchConstraints(max_walk_minutes=30, max_candidates=300),
    )

    with Session(engine) as session:
        transaction = session.begin()
        upsert_gis_slice(session, fixture.destination, fixture.osm.evidence, segments)
        session.flush()

        preserved_segment = session.scalar(
            select(ParkingSegmentModel).order_by(ParkingSegmentModel.segment_id).limit(1)
        )
        assert preserved_segment is not None
        preserved_segment.legal_state = LegalState.LEGAL
        preserved_segment.free_state = FreeState.PAID
        preserved_segment.legal_confidence = 0.91
        preserved_segment.availability_probability = 0.72
        preserved_segment.availability_interval = [0.61, 0.83]
        preserved_segment_id = preserved_segment.segment_id
        session.flush()

        upsert_gis_slice(session, fixture.destination, fixture.osm.evidence, segments)
        session.flush()
        session.expire_all()

        assert session.scalar(select(func.count()).select_from(DestinationModel)) == 1
        assert session.scalar(select(func.count()).select_from(DestinationAccessPointModel)) == 2
        assert session.scalar(select(func.count()).select_from(EvidenceModel)) == 1
        assert session.scalar(select(func.count()).select_from(ParkingSegmentModel)) == 284
        assert session.scalar(select(func.count()).select_from(parking_source_segments)) == 284
        assert set(session.scalars(select(ParkingSegmentModel.legal_state))) == {
            LegalState.LEGAL,
            LegalState.UNKNOWN,
        }
        assert set(session.scalars(select(ParkingSegmentModel.free_state))) == {
            FreeState.PAID,
            FreeState.UNKNOWN,
        }
        preserved_segment = session.get(ParkingSegmentModel, preserved_segment_id)
        assert preserved_segment is not None
        assert preserved_segment.legal_state is LegalState.LEGAL
        assert preserved_segment.free_state is FreeState.PAID
        assert preserved_segment.legal_confidence == pytest.approx(0.91)
        assert preserved_segment.availability_probability == pytest.approx(0.72)
        assert preserved_segment.availability_interval == pytest.approx([0.61, 0.83])
        srids = set(session.scalars(select(func.ST_SRID(ParkingSegmentModel.geometry))).all())
        assert srids == {4326}

        transaction.rollback()
