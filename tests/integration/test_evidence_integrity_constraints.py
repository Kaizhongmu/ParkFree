from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from parking_ai.database.models import EvidenceModel
from parking_ai.domain import (
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
)

pytestmark = pytest.mark.integration
RETRIEVED = datetime(2026, 9, 24, 12, tzinfo=UTC)


def evidence_model(
    evidence_id: str,
    *,
    source_type: EvidenceSourceType = EvidenceSourceType.OFFICIAL_CODE,
    reliability_tier: EvidenceReliabilityTier = EvidenceReliabilityTier.A,
    published_at: datetime | None = None,
    observed_at: datetime | None = None,
) -> EvidenceModel:
    return EvidenceModel(
        evidence_id=evidence_id,
        source_type=source_type,
        source_uri_or_identifier=f"fixture://{evidence_id}",
        published_at=published_at,
        observed_at=observed_at,
        retrieved_at=RETRIEVED,
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        normalized_claims=[],
        reliability_tier=reliability_tier,
    )


@pytest.mark.parametrize(
    "invalid",
    [
        evidence_model(
            "db-future-publication",
            published_at=RETRIEVED + timedelta(seconds=1),
        ),
        evidence_model(
            "db-future-observation",
            observed_at=RETRIEVED + timedelta(seconds=1),
        ),
        evidence_model(
            "db-elevated-community",
            source_type=EvidenceSourceType.COMMUNITY,
            reliability_tier=EvidenceReliabilityTier.A,
        ),
    ],
)
def test_database_rejects_invalid_evidence_inserts(
    engine: Engine,
    invalid: EvidenceModel,
) -> None:
    with Session(engine) as session:
        with pytest.raises(DBAPIError) as exc_info, session.begin_nested():
            session.add(invalid)
            session.flush()

        assert exc_info.value.orig.sqlstate == "23514"


def test_database_allows_conservative_tier_and_rejects_invalid_update(engine: Engine) -> None:
    evidence_id = "db-conservative-osm"
    with Session(engine) as session:
        transaction = session.begin()
        session.add(
            evidence_model(
                evidence_id,
                source_type=EvidenceSourceType.OSM,
                reliability_tier=EvidenceReliabilityTier.C,
                published_at=RETRIEVED,
                observed_at=RETRIEVED,
            )
        )
        session.flush()

        with pytest.raises(DBAPIError) as exc_info, session.begin_nested():
            session.execute(
                update(EvidenceModel)
                .where(EvidenceModel.evidence_id == evidence_id)
                .values(reliability_tier=EvidenceReliabilityTier.A)
            )

        assert exc_info.value.orig.sqlstate == "23514"
        transaction.rollback()
