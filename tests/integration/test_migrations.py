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
