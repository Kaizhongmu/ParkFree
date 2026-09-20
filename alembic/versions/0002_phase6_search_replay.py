"""Add Phase 6 replayable search-session snapshots.

Revision ID: 0002_phase6_search_replay
Revises: 0001_initial_schema
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_phase6_search_replay"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_sessions",
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_sessions",
        sa.Column("request_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_sessions",
        sa.Column(
            "replayable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "search_sessions",
        sa.Column("snapshot_schema_version", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "search_sessions",
        sa.Column(
            "request_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "search_sessions",
        sa.Column(
            "candidate_decisions_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "search_sessions",
        sa.Column(
            "route_matrix_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "search_sessions",
        sa.Column(
            "optimizer_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "search_sessions",
        sa.Column("route_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_sessions",
        sa.Column(
            "route_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "search_sessions",
        sa.Column(
            "response_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "search_sessions",
        sa.Column("artifact_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_sessions",
        sa.Column("route_matrix_provider_version", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_search_sessions_idempotency_key_hash",
        "search_sessions",
        ["idempotency_key_hash"],
    )
    op.create_unique_constraint(
        "uq_search_sessions_route_id",
        "search_sessions",
        ["route_id"],
    )
    op.create_check_constraint(
        "replayable_snapshot_complete",
        "search_sessions",
        "NOT replayable OR ("
        "request_hash IS NOT NULL AND "
        "snapshot_schema_version IS NOT NULL AND "
        "request_snapshot IS NOT NULL AND "
        "candidate_decisions_snapshot IS NOT NULL AND "
        "route_matrix_snapshot IS NOT NULL AND "
        "optimizer_snapshot IS NOT NULL AND "
        "route_id IS NOT NULL AND "
        "route_snapshot IS NOT NULL AND "
        "response_snapshot IS NOT NULL AND "
        "artifact_hash IS NOT NULL AND "
        "rule_engine_version IS NOT NULL AND "
        "availability_model_version IS NOT NULL AND "
        "route_matrix_version IS NOT NULL AND "
        "route_matrix_provider_version IS NOT NULL AND "
        "optimizer_version IS NOT NULL"
        ")",
    )

    op.add_column(
        "search_route_steps",
        sa.Column("legality_evaluation_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_route_steps",
        sa.Column("availability_prediction_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_route_steps",
        sa.Column("availability_target_window_seconds", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "availability_target_window_positive",
        "search_route_steps",
        "availability_target_window_seconds IS NULL OR availability_target_window_seconds > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_search_route_steps_availability_target_window_positive"),
        "search_route_steps",
        type_="check",
    )
    op.drop_column("search_route_steps", "availability_target_window_seconds")
    op.drop_column("search_route_steps", "availability_prediction_id")
    op.drop_column("search_route_steps", "legality_evaluation_id")

    op.drop_constraint(
        op.f("ck_search_sessions_replayable_snapshot_complete"),
        "search_sessions",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_search_sessions_route_id"),
        "search_sessions",
        type_="unique",
    )
    op.drop_constraint(
        op.f("uq_search_sessions_idempotency_key_hash"),
        "search_sessions",
        type_="unique",
    )
    op.drop_column("search_sessions", "route_matrix_provider_version")
    op.drop_column("search_sessions", "artifact_hash")
    op.drop_column("search_sessions", "response_snapshot")
    op.drop_column("search_sessions", "route_snapshot")
    op.drop_column("search_sessions", "route_id")
    op.drop_column("search_sessions", "optimizer_snapshot")
    op.drop_column("search_sessions", "route_matrix_snapshot")
    op.drop_column("search_sessions", "candidate_decisions_snapshot")
    op.drop_column("search_sessions", "request_snapshot")
    op.drop_column("search_sessions", "snapshot_schema_version")
    op.drop_column("search_sessions", "replayable")
    op.drop_column("search_sessions", "request_hash")
    op.drop_column("search_sessions", "idempotency_key_hash")
