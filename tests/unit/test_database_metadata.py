from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint, Enum, ForeignKeyConstraint

from parking_ai.database import models  # noqa: F401
from parking_ai.database.base import Base

EXPECTED_TABLES = {
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
        "parking_rules": {
            "parking_source_segments.evidence_id",
            "parking_source_segments.segment_id",
            "parking_sources.evidence_id",
            "street_segments.segment_id",
        },
        "search_route_steps": {"search_sessions.session_id", "street_segments.segment_id"},
        "parking_outcomes": {"search_sessions.session_id", "street_segments.segment_id"},
        "evidence_review_queue": {"parking_sources.evidence_id"},
        "evidence_review_events": {
            "evidence_review_queue.review_item_id",
            "parking_sources.evidence_id",
        },
    }

    for table_name, targets in expected_targets.items():
        actual_targets = {
            foreign_key.target_fullname
            for foreign_key in Base.metadata.tables[table_name].foreign_keys
        }
        assert actual_targets == targets


def test_parking_rule_provenance_binding_is_composite_and_deferred() -> None:
    rules = Base.metadata.tables["parking_rules"]
    constraints = {
        constraint.name: constraint
        for constraint in rules.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    provenance = constraints["fk_parking_rules_evidence_segment_binding"]
    assert [element.parent.name for element in provenance.elements] == [
        "source_evidence_id",
        "segment_id",
    ]
    assert [element.target_fullname for element in provenance.elements] == [
        "parking_source_segments.evidence_id",
        "parking_source_segments.segment_id",
    ]
    assert provenance.deferrable is True
    assert provenance.initially == "DEFERRED"

    # Preserve the original independent evidence and segment references as well.
    assert "fk_parking_rules_source_evidence_id_parking_sources" in constraints
    assert "fk_parking_rules_segment_id_street_segments" in constraints


def test_evidence_metadata_enforces_chronology_and_source_authority() -> None:
    evidence = Base.metadata.tables["parking_sources"]
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in evidence.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(constraints) == {
        "ck_parking_sources_observed_not_after_retrieved",
        "ck_parking_sources_published_not_after_retrieved",
        "ck_parking_sources_source_authority_ceiling",
    }
    authority_sql = constraints["ck_parking_sources_source_authority_ceiling"]
    assert "source_type IN ('UNIVERSITY', 'OSM')" in authority_sql
    assert "reliability_tier IN ('B', 'C', 'D')" in authority_sql
    assert "source_type = 'IMAGERY_INFERENCE'" in authority_sql
    assert "reliability_tier = 'D'" in authority_sql


def test_review_queue_metadata_uses_check_constrained_strings_and_audit_keys() -> None:
    queue = Base.metadata.tables["evidence_review_queue"]
    events = Base.metadata.tables["evidence_review_events"]

    assert not isinstance(queue.c.status.type, Enum)
    assert not isinstance(queue.c.service_kind.type, Enum)
    assert queue.c.extraction_result_id.unique is None
    assert queue.c.result_snapshot.nullable is False
    assert queue.c.result_snapshot_hash.nullable is False
    assert queue.c.revision.nullable is False
    assert queue.c.approval_scope.nullable is True
    assert queue.c.approved_evidence_id.nullable is True
    assert events.c.reason_code.nullable is False

    queue_constraints = {constraint.name for constraint in queue.constraints}
    event_constraints = {constraint.name for constraint in events.constraints}
    assert "ck_evidence_review_queue_approval_complete" in queue_constraints
    assert "ck_evidence_review_queue_result_snapshot_safe_object" in queue_constraints
    assert "uq_evidence_review_queue_extraction_result_id" in queue_constraints
    assert "ck_evidence_review_events_action_status_transition" in event_constraints
    assert "ck_evidence_review_events_lease_matches_action" in event_constraints
    assert "ck_evidence_review_events_audit_hashes_sha256" in event_constraints
    assert "uq_evidence_review_events_item_revision" in event_constraints
    assert "uq_evidence_review_events_action_idempotency_key_hash" in event_constraints

    transition_constraint = next(
        constraint
        for constraint in events.constraints
        if constraint.name == "ck_evidence_review_events_action_status_transition"
    )
    transition_sql = str(transition_constraint.sqltext)
    assert "previous_status = 'IN_REVIEW'" in transition_sql
    assert "reason_code = 'LEASE_EXPIRED_RECLAIM'" in transition_sql
