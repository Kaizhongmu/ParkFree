from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError

from alembic import command

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "alembic_version",
    "destination_access_points",
    "destinations",
    "evidence_review_events",
    "evidence_review_queue",
    "parking_outcomes",
    "parking_rules",
    "parking_source_segments",
    "parking_sources",
    "search_route_steps",
    "search_sessions",
    "street_segments",
}


def test_migration_creates_phase_one_schema(engine: Engine) -> None:
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) >= EXPECTED_TABLES
    street_indexes = {index["name"] for index in inspector.get_indexes("street_segments")}
    assert "ix_street_segments_geometry_gist" in street_indexes


def test_migration_creates_foreign_keys(engine: Engine) -> None:
    inspector = inspect(engine)

    rule_foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("parking_rules")
    }
    step_targets = {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("search_route_steps")
    }

    assert set(rule_foreign_keys) == {
        "fk_parking_rules_evidence_segment_binding",
        "fk_parking_rules_segment_id_street_segments",
        "fk_parking_rules_source_evidence_id_parking_sources",
    }
    provenance = rule_foreign_keys["fk_parking_rules_evidence_segment_binding"]
    assert provenance["constrained_columns"] == ["source_evidence_id", "segment_id"]
    assert provenance["referred_table"] == "parking_source_segments"
    assert provenance["referred_columns"] == ["evidence_id", "segment_id"]
    assert provenance["options"] == {"deferrable": True, "initially": "DEFERRED"}
    assert step_targets == {"search_sessions", "street_segments"}


def test_phase6_migration_adds_replay_columns_and_constraints(engine: Engine) -> None:
    inspector = inspect(engine)
    session_columns = {
        column["name"]: column for column in inspector.get_columns("search_sessions")
    }
    step_columns = {
        column["name"]: column for column in inspector.get_columns("search_route_steps")
    }

    assert session_columns["replayable"]["nullable"] is False
    assert session_columns["request_snapshot"]["nullable"] is True
    assert session_columns["response_snapshot"]["nullable"] is True
    assert step_columns["legality_evaluation_id"]["nullable"] is True
    assert step_columns["availability_prediction_id"]["nullable"] is True
    assert step_columns["availability_target_window_seconds"]["nullable"] is True

    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("search_sessions")
    }
    check_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("search_sessions")
    }
    assert "uq_search_sessions_idempotency_key_hash" in unique_constraints
    assert "uq_search_sessions_route_id" in unique_constraints
    assert "ck_search_sessions_replayable_snapshot_complete" in check_constraints


def test_v1a_migration_adds_review_queue_audit_constraints_and_indexes(engine: Engine) -> None:
    inspector = inspect(engine)
    queue_columns = {
        column["name"]: column for column in inspector.get_columns("evidence_review_queue")
    }
    event_columns = {
        column["name"]: column for column in inspector.get_columns("evidence_review_events")
    }

    assert queue_columns["extraction_result_id"]["nullable"] is False
    assert queue_columns["result_snapshot"]["nullable"] is False
    assert queue_columns["result_snapshot_hash"]["nullable"] is False
    assert queue_columns["approval_scope"]["nullable"] is True
    assert queue_columns["claim_expires_at"]["nullable"] is True
    assert event_columns["actor_roles"]["nullable"] is False
    assert event_columns["reason_code"]["nullable"] is False
    assert event_columns["lease_expires_at"]["nullable"] is True
    assert event_columns["action_idempotency_key_hash"]["nullable"] is False
    assert event_columns["event_hash"]["nullable"] is False

    queue_constraints = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("evidence_review_queue")
    }
    event_constraints = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("evidence_review_events")
    }
    queue_unique = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("evidence_review_queue")
    }
    event_unique = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("evidence_review_events")
    }
    queue_indexes = {index["name"] for index in inspector.get_indexes("evidence_review_queue")}

    assert "ck_evidence_review_queue_approval_complete" in queue_constraints
    assert "ck_evidence_review_queue_claim_assignment_complete" in queue_constraints
    assert "ck_evidence_review_queue_result_snapshot_safe_object" in queue_constraints
    assert "ck_evidence_review_events_action_status_transition" in event_constraints
    assert "ck_evidence_review_events_lease_matches_action" in event_constraints
    assert "ck_evidence_review_events_audit_hashes_sha256" in event_constraints
    assert "uq_evidence_review_queue_extraction_result_id" in queue_unique
    assert "uq_evidence_review_events_item_revision" in event_unique
    assert "uq_evidence_review_events_action_idempotency_key_hash" in event_unique
    assert "ix_evidence_review_queue_status_submitted_at" in queue_indexes

    event_foreign_keys = {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("evidence_review_events")
    }
    assert event_foreign_keys == {"evidence_review_queue", "parking_sources"}


def test_review_queue_projection_event_guard_is_deferred(engine: Engine) -> None:
    with engine.connect() as connection:
        trigger = connection.execute(
            text(
                """
                SELECT tgdeferrable, tginitdeferred
                FROM pg_trigger
                WHERE tgrelid = 'evidence_review_queue'::regclass
                  AND tgname = 'trg_require_evidence_review_projection_event'
                """
            )
        ).one()

    assert trigger.tgdeferrable is True
    assert trigger.tginitdeferred is True


def test_evidence_integrity_constraints_are_migrated(engine: Engine) -> None:
    inspector = inspect(engine)
    constraints = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("parking_sources")
    }

    assert "ck_parking_sources_published_not_after_retrieved" in constraints
    assert "ck_parking_sources_observed_not_after_retrieved" in constraints
    authority_sql = constraints["ck_parking_sources_source_authority_ceiling"]
    assert "UNIVERSITY" in authority_sql
    assert "IMAGERY_INFERENCE" in authority_sql


@pytest.mark.parametrize(
    ("evidence_id", "published_at", "observed_at", "source_type", "tier"),
    [
        (
            "legacy-future-publication",
            datetime(2026, 9, 24, 12, 1, tzinfo=UTC),
            None,
            "OFFICIAL_CODE",
            "A",
        ),
        (
            "legacy-future-observation",
            None,
            datetime(2026, 9, 24, 12, 1, tzinfo=UTC),
            "OFFICIAL_CODE",
            "A",
        ),
        (
            "legacy-elevated-community",
            None,
            None,
            "COMMUNITY",
            "A",
        ),
    ],
)
def test_evidence_integrity_migration_rejects_legacy_violations_without_rewriting(
    engine: Engine,
    database_url: str,
    evidence_id: str,
    published_at: datetime | None,
    observed_at: datetime | None,
    source_type: str,
    tier: str,
) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    retrieved_at = datetime(2026, 9, 24, 12, tzinfo=UTC)

    command.downgrade(config, "0005_review_queue_event_guard")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO parking_sources (
                        evidence_id,
                        source_type,
                        source_uri_or_identifier,
                        published_at,
                        observed_at,
                        retrieved_at,
                        raw_storage_policy,
                        normalized_claims,
                        reliability_tier
                    ) VALUES (
                        :evidence_id,
                        CAST(:source_type AS evidence_source_type),
                        :source_uri,
                        :published_at,
                        :observed_at,
                        :retrieved_at,
                        'REFERENCE_ONLY',
                        CAST('[]' AS jsonb),
                        CAST(:tier AS evidence_reliability_tier)
                    )
                    """
                ),
                {
                    "evidence_id": evidence_id,
                    "source_type": source_type,
                    "source_uri": f"fixture://{evidence_id}",
                    "published_at": published_at,
                    "observed_at": observed_at,
                    "retrieved_at": retrieved_at,
                    "tier": tier,
                },
            )

        with pytest.raises(DBAPIError) as exc_info:
            command.upgrade(config, "head")
        assert exc_info.value.orig.sqlstate == "23514"

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0005_review_queue_event_guard"
            )
            stored = connection.execute(
                text(
                    """
                    SELECT published_at, observed_at, source_type::text, reliability_tier::text
                    FROM parking_sources
                    WHERE evidence_id = :evidence_id
                    """
                ),
                {"evidence_id": evidence_id},
            ).one()
            assert stored == (published_at, observed_at, source_type, tier)
            constraint_count = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_constraint
                    WHERE conrelid = 'parking_sources'::regclass
                      AND (
                          conname LIKE 'ck_parking_sources_%_not_after_retrieved'
                          OR conname = 'ck_parking_sources_source_authority_ceiling'
                      )
                    """
                )
            )
            assert constraint_count == 0
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM parking_sources WHERE evidence_id = :evidence_id"),
                {"evidence_id": evidence_id},
            )
        command.upgrade(config, "head")
