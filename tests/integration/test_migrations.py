import pytest
from sqlalchemy import Engine, inspect

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "alembic_version",
    "destination_access_points",
    "destinations",
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

    rule_targets = {
        foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("parking_rules")
    }
    step_targets = {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("search_route_steps")
    }

    assert rule_targets == {"parking_sources", "street_segments"}
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
