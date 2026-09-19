from geoalchemy2 import Geometry

from parking_ai.database import models  # noqa: F401
from parking_ai.database.base import Base

EXPECTED_TABLES = {
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


def test_phase_one_metadata_contains_required_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_spatial_columns_use_postgis_types_and_wgs84() -> None:
    expected_geometry_types = {
        "destinations": "POINT",
        "destination_access_points": "POINT",
        "street_segments": "LINESTRING",
        "search_sessions": "POINT",
    }

    for table_name, geometry_type in expected_geometry_types.items():
        column_name = "origin" if table_name == "search_sessions" else "geometry"
        column_type = Base.metadata.tables[table_name].c[column_name].type
        assert isinstance(column_type, Geometry)
        assert column_type.geometry_type == geometry_type
        assert column_type.srid == 4326


def test_required_foreign_keys_match_domain_relationships() -> None:
    expected_targets = {
        "destination_access_points": {"destinations.destination_id"},
        "parking_rules": {"parking_sources.evidence_id", "street_segments.segment_id"},
        "search_route_steps": {"search_sessions.session_id", "street_segments.segment_id"},
        "parking_outcomes": {"search_sessions.session_id", "street_segments.segment_id"},
    }

    for table_name, targets in expected_targets.items():
        actual_targets = {
            foreign_key.target_fullname
            for foreign_key in Base.metadata.tables[table_name].foreign_keys
        }
        assert actual_targets == targets
