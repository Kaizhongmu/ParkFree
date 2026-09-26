from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from parking_ai.config import Settings
from parking_ai.database.session import create_database_engine
from parking_ai.domain import SearchConstraints
from parking_ai.gis.fixtures import load_smu_gis_fixture
from parking_ai.gis.generator import DeterministicCandidateSegmentService
from parking_ai.gis.persistence import upsert_gis_slice

SMU_SEED_MAX_WALK_MINUTES = 8.0
SMU_SEED_MAX_CANDIDATES = 300
SMU_SEED_EXPECTED_SEGMENT_COUNT = 284


@dataclass(frozen=True)
class SMUSeedResult:
    destination_id: str
    segment_count: int


def seed_smu_fixture(session: Session) -> SMUSeedResult:
    """Idempotently load the canonical offline SMU GIS fixture into a migrated database."""

    fixture = load_smu_gis_fixture()
    service = DeterministicCandidateSegmentService(
        fixture.osm.roads,
        evidence_id=fixture.osm.evidence.evidence_id,
        data_freshness=fixture.osm.observed_at,
    )
    segments = service.get_candidate_segments(
        fixture.destination,
        SearchConstraints(
            max_walk_minutes=SMU_SEED_MAX_WALK_MINUTES,
            max_candidates=SMU_SEED_MAX_CANDIDATES,
        ),
    )
    segment_ids = {segment.segment_id for segment in segments}
    if len(segments) != SMU_SEED_EXPECTED_SEGMENT_COUNT or len(segment_ids) != len(segments):
        raise RuntimeError(
            "canonical SMU fixture must produce exactly "
            f"{SMU_SEED_EXPECTED_SEGMENT_COUNT} unique candidate segments; got "
            f"{len(segments)} segments and {len(segment_ids)} unique IDs"
        )
    upsert_gis_slice(session, fixture.destination, fixture.osm.evidence, segments)
    session.flush()
    return SMUSeedResult(
        destination_id=fixture.destination.destination_id,
        segment_count=len(segments),
    )


def main() -> None:
    """Seed the configured database without printing credentials or provider payloads."""

    database_url = Settings().database_url
    if database_url is None:
        raise SystemExit("DATABASE_URL is required; configure it before seeding the SMU fixture.")

    try:
        engine = create_database_engine(database_url)
        try:
            with Session(engine) as session, session.begin():
                result = seed_smu_fixture(session)
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError, RuntimeError):
        raise SystemExit(
            "SMU fixture seed failed; verify DATABASE_URL, migrations, and database availability."
        ) from None

    print(
        f"Seeded {result.segment_count} SMU candidate segments "
        f"for destination {result.destination_id}."
    )


if __name__ == "__main__":
    main()
