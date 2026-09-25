"""Enforce evidence chronology and source authority ceilings.

Revision ID: 0006_evidence_integrity
Revises: 0005_review_queue_event_guard
Create Date: 2026-09-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_evidence_integrity"
down_revision: str | None = "0005_review_queue_event_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Prevent writes between the integrity preflight and constraint installation. Historical
    # legal evidence is never rewritten automatically: any violation requires operator review.
    op.execute("LOCK TABLE parking_sources IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        DECLARE
            published_count bigint;
            published_example text;
            observed_count bigint;
            observed_example text;
            authority_count bigint;
            authority_example text;
        BEGIN
            SELECT count(*), min(
                format(
                    'evidence_id=%s published_at=%s retrieved_at=%s',
                    evidence_id,
                    published_at,
                    retrieved_at
                )
            )
            INTO published_count, published_example
            FROM parking_sources
            WHERE published_at > retrieved_at;

            IF published_count > 0 THEN
                RAISE EXCEPTION
                    'parking_sources has % evidence row(s) published after retrieval; example: %',
                    published_count,
                    published_example
                    USING ERRCODE = '23514',
                          HINT = 'Audit the affected evidence timestamps before retrying; '
                              || 'do not rewrite legal provenance automatically.';
            END IF;

            SELECT count(*), min(
                format(
                    'evidence_id=%s observed_at=%s retrieved_at=%s',
                    evidence_id,
                    observed_at,
                    retrieved_at
                )
            )
            INTO observed_count, observed_example
            FROM parking_sources
            WHERE observed_at > retrieved_at;

            IF observed_count > 0 THEN
                RAISE EXCEPTION
                    'parking_sources has % evidence row(s) observed after retrieval; example: %',
                    observed_count,
                    observed_example
                    USING ERRCODE = '23514',
                          HINT = 'Audit the affected evidence timestamps before retrying; '
                              || 'do not rewrite legal provenance automatically.';
            END IF;

            SELECT count(*), min(
                format(
                    'evidence_id=%s source_type=%s reliability_tier=%s',
                    evidence_id,
                    source_type,
                    reliability_tier
                )
            )
            INTO authority_count, authority_example
            FROM parking_sources
            WHERE NOT (
                source_type IN ('OFFICIAL_CODE', 'OFFICIAL_GIS', 'VERIFIED_SIGN')
                OR (
                    source_type IN ('UNIVERSITY', 'OSM')
                    AND reliability_tier IN ('B', 'C', 'D')
                )
                OR (
                    source_type IN ('COMMUNITY', 'WEB')
                    AND reliability_tier IN ('C', 'D')
                )
                OR (
                    source_type = 'IMAGERY_INFERENCE'
                    AND reliability_tier = 'D'
                )
            );

            IF authority_count > 0 THEN
                RAISE EXCEPTION
                    'parking_sources has % evidence row(s) above source authority; example: %',
                    authority_count,
                    authority_example
                    USING ERRCODE = '23514',
                          HINT = 'Audit source identity and reliability before retrying; '
                              || 'do not downgrade legal evidence automatically.';
            END IF;
        END;
        $$;
        """
    )
    op.create_check_constraint(
        op.f("ck_parking_sources_published_not_after_retrieved"),
        "parking_sources",
        "published_at IS NULL OR published_at <= retrieved_at",
    )
    op.create_check_constraint(
        op.f("ck_parking_sources_observed_not_after_retrieved"),
        "parking_sources",
        "observed_at IS NULL OR observed_at <= retrieved_at",
    )
    op.create_check_constraint(
        op.f("ck_parking_sources_source_authority_ceiling"),
        "parking_sources",
        "source_type IN ('OFFICIAL_CODE', 'OFFICIAL_GIS', 'VERIFIED_SIGN') OR "
        "(source_type IN ('UNIVERSITY', 'OSM') AND "
        "reliability_tier IN ('B', 'C', 'D')) OR "
        "(source_type IN ('COMMUNITY', 'WEB') AND "
        "reliability_tier IN ('C', 'D')) OR "
        "(source_type = 'IMAGERY_INFERENCE' AND reliability_tier = 'D')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_parking_sources_source_authority_ceiling"),
        "parking_sources",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_parking_sources_observed_not_after_retrieved"),
        "parking_sources",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_parking_sources_published_not_after_retrieved"),
        "parking_sources",
        type_="check",
    )
