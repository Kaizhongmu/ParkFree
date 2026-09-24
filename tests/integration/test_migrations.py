import pytest
from sqlalchemy import Engine, inspect, text

pytestmark = pytest.mark.integration

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
