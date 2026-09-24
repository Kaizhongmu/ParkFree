from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_BEFORE_GUARD = "0004_rule_provenance_binding"
MIGRATION_WITH_GUARD = "0005_review_queue_event_guard"
TRIGGER_NAME = "trg_require_evidence_review_projection_event"


def _alembic_config(database_url: str) -> Config:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def test_migration_preflight_rejects_queue_projection_without_audit_event(
    engine: Engine,
    database_url: str,
) -> None:
    config = _alembic_config(database_url)
    review_item_id = "review-preflight-missing-event"
    extraction_result_id = "extract-preflight-missing-event"

    command.downgrade(config, MIGRATION_BEFORE_GUARD)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO evidence_review_queue (
                        review_item_id, extraction_result_id, status, service_kind,
                        extraction_disposition, extractor_version, source_type,
                        source_uri_or_identifier, retrieved_at, raw_storage_policy,
                        snapshot_schema_version, result_snapshot, result_snapshot_hash,
                        review_reason_codes, error_codes, submitted_by, revision,
                        submitted_at, updated_at
                    ) VALUES (
                        :review_item_id, :extraction_result_id, 'PENDING', 'REGULATION',
                        'REVIEW_REQUIRED', 'fixture-extractor-v1', 'OFFICIAL_CODE',
                        'fixture://v1a/preflight-missing-event', now(), 'REFERENCE_ONLY',
                        'v1a-review-snapshot-v1',
                        CAST(:snapshot AS jsonb), :snapshot_hash,
                        CAST('["HUMAN_VERIFICATION_REQUIRED"]' AS jsonb),
                        CAST('[]' AS jsonb), 'fixture-submitter', 1, now(), now()
                    )
                    """
                ),
                {
                    "review_item_id": review_item_id,
                    "extraction_result_id": extraction_result_id,
                    "snapshot": f'{{"result_id":"{extraction_result_id}"}}',
                    "snapshot_hash": "a" * 64,
                },
            )

        with pytest.raises(IntegrityError) as captured:
            command.upgrade(config, MIGRATION_WITH_GUARD)
        assert captured.value.orig.sqlstate == "23514"

        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == MIGRATION_BEFORE_GUARD
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM evidence_review_queue "
                        "WHERE review_item_id = :review_item_id"
                    ),
                    {"review_item_id": review_item_id},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_trigger WHERE tgname = :trigger_name"),
                    {"trigger_name": TRIGGER_NAME},
                )
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE evidence_review_queue "
                    "DISABLE TRIGGER trg_protect_evidence_review_queue"
                )
            )
            connection.execute(
                text("DELETE FROM evidence_review_queue WHERE review_item_id = :review_item_id"),
                {"review_item_id": review_item_id},
            )
            connection.execute(
                text(
                    "ALTER TABLE evidence_review_queue "
                    "ENABLE TRIGGER trg_protect_evidence_review_queue"
                )
            )
        command.upgrade(config, MIGRATION_WITH_GUARD)
