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
from parking_ai.domain import FreeState, LegalState
from parking_ai.gis.seed import seed_smu_fixture

pytestmark = pytest.mark.integration


def test_phase_two_fixture_upsert_is_idempotent_after_fresh_migration(engine: Engine) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        with (
            Session(bind=connection, join_transaction_mode="create_savepoint") as session,
            session.begin(),
        ):
            first_seed = seed_smu_fixture(session)
            assert first_seed.destination_id == "smu-fondren-library"
            assert first_seed.segment_count == 284

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

        with (
            Session(bind=connection, join_transaction_mode="create_savepoint") as session,
            session.begin(),
        ):
            second_seed = seed_smu_fixture(session)
            assert second_seed == first_seed

        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            assert session.scalar(select(func.count()).select_from(DestinationModel)) == 1
            assert (
                session.scalar(select(func.count()).select_from(DestinationAccessPointModel)) == 2
            )
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
