"""Create Phase 1 domain persistence schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

segment_side = postgresql.ENUM("LEFT", "RIGHT", "UNKNOWN", name="segment_side", create_type=False)
physical_state = postgresql.ENUM(
    "PARKABLE", "NOT_PARKABLE", "UNKNOWN", name="physical_state", create_type=False
)
legal_state = postgresql.ENUM("LEGAL", "ILLEGAL", "UNKNOWN", name="legal_state", create_type=False)
free_state = postgresql.ENUM("FREE", "PAID", "UNKNOWN", name="free_state", create_type=False)
evidence_source_type = postgresql.ENUM(
    "OFFICIAL_CODE",
    "OFFICIAL_GIS",
    "VERIFIED_SIGN",
    "UNIVERSITY",
    "OSM",
    "COMMUNITY",
    "WEB",
    "IMAGERY_INFERENCE",
    name="evidence_source_type",
    create_type=False,
)
evidence_storage_policy = postgresql.ENUM(
    "PERSIST",
    "EPHEMERAL",
    "REFERENCE_ONLY",
    name="evidence_storage_policy",
    create_type=False,
)
evidence_reliability_tier = postgresql.ENUM(
    "A", "B", "C", "D", name="evidence_reliability_tier", create_type=False
)
parking_rule_type = postgresql.ENUM(
    "NO_PARKING",
    "TIME_LIMIT",
    "PAID",
    "PERMIT_ONLY",
    "LOADING",
    "STREET_CLEANING",
    "EVENT_RESTRICTION",
    "OTHER",
    name="parking_rule_type",
    create_type=False,
)
search_session_status = postgresql.ENUM(
    "CREATED",
    "PLANNED",
    "COMPLETED",
    "ABANDONED",
    name="search_session_status",
    create_type=False,
)

ENUMS = (
    segment_side,
    physical_state,
    legal_state,
    free_state,
    evidence_source_type,
    evidence_storage_policy,
    evidence_reliability_tier,
    parking_rule_type,
    search_session_status,
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    bind = op.get_bind()
    for enum_type in ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "destinations",
        sa.Column("destination_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("geometry", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("destination_type", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("destination_id", name="pk_destinations"),
    )
    op.create_index(
        "ix_destinations_geometry_gist",
        "destinations",
        ["geometry"],
        unique=False,
        postgresql_using="gist",
    )

    op.create_table(
        "street_segments",
        sa.Column("segment_id", sa.String(length=64), nullable=False),
        sa.Column(
            "geometry", Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=False
        ),
        sa.Column("street_name", sa.String(length=255), nullable=True),
        sa.Column("side", segment_side, nullable=False),
        sa.Column("length_m", sa.Float(), nullable=False),
        sa.Column("estimated_capacity", sa.Float(), nullable=True),
        sa.Column("road_type", sa.String(length=64), nullable=True),
        sa.Column("physical_state", physical_state, nullable=False),
        sa.Column("legal_state", legal_state, nullable=False),
        sa.Column("free_state", free_state, nullable=False),
        sa.Column("legal_confidence", sa.Float(), nullable=False),
        sa.Column("availability_probability", sa.Float(), nullable=True),
        sa.Column("availability_interval", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("data_freshness", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "availability_probability IS NULL OR "
            "(availability_probability >= 0 AND availability_probability <= 1)",
            name="availability_probability_range",
        ),
        sa.CheckConstraint(
            "estimated_capacity IS NULL OR estimated_capacity >= 0",
            name="estimated_capacity_nonnegative",
        ),
        sa.CheckConstraint(
            "legal_confidence >= 0 AND legal_confidence <= 1",
            name="legal_confidence_probability",
        ),
        sa.CheckConstraint("length_m > 0", name="length_m_positive"),
        sa.PrimaryKeyConstraint("segment_id", name="pk_street_segments"),
    )
    op.create_index(
        "ix_street_segments_geometry_gist",
        "street_segments",
        ["geometry"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_street_segments_street_name",
        "street_segments",
        ["street_name"],
        unique=False,
    )

    op.create_table(
        "parking_sources",
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", evidence_source_type, nullable=False),
        sa.Column("source_uri_or_identifier", sa.Text(), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_storage_policy", evidence_storage_policy, nullable=False),
        sa.Column(
            "normalized_claims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reliability_tier", evidence_reliability_tier, nullable=False),
        sa.Column("extractor_version", sa.String(length=128), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_parking_sources"),
    )
    op.create_index(
        "ix_parking_sources_content_hash",
        "parking_sources",
        ["content_hash"],
        unique=False,
    )

    op.create_table(
        "destination_access_points",
        sa.Column("access_point_id", sa.String(length=64), nullable=False),
        sa.Column("destination_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("geometry", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["destinations.destination_id"],
            name="fk_destination_access_points_destination_id_destinations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("access_point_id", name="pk_destination_access_points"),
    )
    op.create_index(
        "ix_destination_access_points_destination_id",
        "destination_access_points",
        ["destination_id"],
        unique=False,
    )
    op.create_index(
        "ix_destination_access_points_geometry_gist",
        "destination_access_points",
        ["geometry"],
        unique=False,
        postgresql_using="gist",
    )

    op.create_table(
        "parking_source_segments",
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("segment_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["parking_sources.evidence_id"],
            name="fk_parking_source_segments_evidence_id_parking_sources",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["street_segments.segment_id"],
            name="fk_parking_source_segments_segment_id_street_segments",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evidence_id", "segment_id", name="pk_parking_source_segments"),
    )

    op.create_table(
        "parking_rules",
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("segment_id", sa.String(length=64), nullable=False),
        sa.Column("rule_type", parking_rule_type, nullable=False),
        sa.Column(
            "days",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("effective_start_date", sa.Date(), nullable=True),
        sa.Column("effective_end_date", sa.Date(), nullable=True),
        sa.Column("max_duration_min", sa.Integer(), nullable=True),
        sa.Column("payment_required", sa.Boolean(), nullable=True),
        sa.Column("permit_required", sa.Boolean(), nullable=True),
        sa.Column("permit_type", sa.String(length=128), nullable=True),
        sa.Column(
            "exceptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_evidence_id", sa.String(length=64), nullable=False),
        sa.Column("extraction_confidence", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "effective_start_date IS NULL OR effective_end_date IS NULL "
            "OR effective_start_date <= effective_end_date",
            name="effective_date_order",
        ),
        sa.CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="extraction_confidence_probability",
        ),
        sa.CheckConstraint(
            "max_duration_min IS NULL OR max_duration_min > 0",
            name="max_duration_positive",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["street_segments.segment_id"],
            name="fk_parking_rules_segment_id_street_segments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"],
            ["parking_sources.evidence_id"],
            name="fk_parking_rules_source_evidence_id_parking_sources",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("rule_id", name="pk_parking_rules"),
    )
    op.create_index("ix_parking_rules_segment_id", "parking_rules", ["segment_id"], unique=False)
    op.create_index(
        "ix_parking_rules_source_evidence_id",
        "parking_rules",
        ["source_evidence_id"],
        unique=False,
    )

    op.create_table(
        "search_sessions",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("destination_id", sa.String(length=64), nullable=False),
        sa.Column("origin", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("requested_arrival_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("free_only", sa.Boolean(), nullable=False),
        sa.Column("max_walk_minutes", sa.Float(), nullable=False),
        sa.Column("max_candidates", sa.Integer(), nullable=False),
        sa.Column(
            "candidate_segment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", search_session_status, nullable=False),
        sa.Column("rule_engine_version", sa.String(length=128), nullable=True),
        sa.Column("availability_model_version", sa.String(length=128), nullable=True),
        sa.Column("route_matrix_version", sa.String(length=128), nullable=True),
        sa.Column("optimizer_version", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("max_candidates > 0", name="max_candidates_positive"),
        sa.CheckConstraint("max_walk_minutes > 0", name="max_walk_minutes_positive"),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["destinations.destination_id"],
            name="fk_search_sessions_destination_id_destinations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_search_sessions"),
    )
    op.create_index(
        "ix_search_sessions_created_at", "search_sessions", ["created_at"], unique=False
    )
    op.create_index(
        "ix_search_sessions_destination_id",
        "search_sessions",
        ["destination_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_sessions_origin_gist",
        "search_sessions",
        ["origin"],
        unique=False,
        postgresql_using="gist",
    )

    op.create_table(
        "search_route_steps",
        sa.Column("route_step_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("segment_id", sa.String(length=64), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("legal_state", legal_state, nullable=False),
        sa.Column("free_state", free_state, nullable=False),
        sa.Column("legal_confidence", sa.Float(), nullable=False),
        sa.Column("availability_probability", sa.Float(), nullable=True),
        sa.Column("drive_eta_min", sa.Float(), nullable=False),
        sa.Column("walk_min", sa.Float(), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("rule_engine_version", sa.String(length=128), nullable=False),
        sa.Column("availability_model_version", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "availability_probability IS NULL OR "
            "(availability_probability >= 0 AND availability_probability <= 1)",
            name="availability_probability_range",
        ),
        sa.CheckConstraint("drive_eta_min >= 0", name="drive_eta_nonnegative"),
        sa.CheckConstraint(
            "legal_confidence >= 0 AND legal_confidence <= 1",
            name="legal_confidence_probability",
        ),
        sa.CheckConstraint("step_order >= 0", name="step_order_nonnegative"),
        sa.CheckConstraint("walk_min >= 0", name="walk_nonnegative"),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["street_segments.segment_id"],
            name="fk_search_route_steps_segment_id_street_segments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["search_sessions.session_id"],
            name="fk_search_route_steps_session_id_search_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("route_step_id", name="pk_search_route_steps"),
        sa.UniqueConstraint("session_id", "step_order", name="uq_search_route_steps_session_order"),
    )
    op.create_index(
        "ix_search_route_steps_segment_id",
        "search_route_steps",
        ["segment_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_route_steps_session_id",
        "search_route_steps",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "parking_outcomes",
        sa.Column("outcome_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("segment_id", sa.String(length=64), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("search_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("route_step_order", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "route_step_order IS NULL OR route_step_order >= 0",
            name="route_step_order_nonnegative",
        ),
        sa.CheckConstraint(
            "search_duration_seconds IS NULL OR search_duration_seconds >= 0",
            name="search_duration_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["street_segments.segment_id"],
            name="fk_parking_outcomes_segment_id_street_segments",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["search_sessions.session_id"],
            name="fk_parking_outcomes_session_id_search_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("outcome_id", name="pk_parking_outcomes"),
    )
    op.create_index(
        "ix_parking_outcomes_segment_id", "parking_outcomes", ["segment_id"], unique=False
    )
    op.create_index(
        "ix_parking_outcomes_session_id", "parking_outcomes", ["session_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_parking_outcomes_session_id", table_name="parking_outcomes")
    op.drop_index("ix_parking_outcomes_segment_id", table_name="parking_outcomes")
    op.drop_table("parking_outcomes")
    op.drop_index("ix_search_route_steps_session_id", table_name="search_route_steps")
    op.drop_index("ix_search_route_steps_segment_id", table_name="search_route_steps")
    op.drop_table("search_route_steps")
    op.drop_index(
        "ix_search_sessions_origin_gist", table_name="search_sessions", postgresql_using="gist"
    )
    op.drop_index("ix_search_sessions_destination_id", table_name="search_sessions")
    op.drop_index("ix_search_sessions_created_at", table_name="search_sessions")
    op.drop_table("search_sessions")
    op.drop_index("ix_parking_rules_source_evidence_id", table_name="parking_rules")
    op.drop_index("ix_parking_rules_segment_id", table_name="parking_rules")
    op.drop_table("parking_rules")
    op.drop_table("parking_source_segments")
    op.drop_index(
        "ix_destination_access_points_geometry_gist",
        table_name="destination_access_points",
        postgresql_using="gist",
    )
    op.drop_index(
        "ix_destination_access_points_destination_id", table_name="destination_access_points"
    )
    op.drop_table("destination_access_points")
    op.drop_index("ix_parking_sources_content_hash", table_name="parking_sources")
    op.drop_table("parking_sources")
    op.drop_index("ix_street_segments_street_name", table_name="street_segments")
    op.drop_index(
        "ix_street_segments_geometry_gist",
        table_name="street_segments",
        postgresql_using="gist",
    )
    op.drop_table("street_segments")
    op.drop_index(
        "ix_destinations_geometry_gist", table_name="destinations", postgresql_using="gist"
    )
    op.drop_table("destinations")

    bind = op.get_bind()
    for enum_type in reversed(ENUMS):
        enum_type.drop(bind, checkfirst=True)
