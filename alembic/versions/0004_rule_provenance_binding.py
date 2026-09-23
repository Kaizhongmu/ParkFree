"""Bind each parking rule's source evidence to the same segment.

Revision ID: 0004_rule_provenance_binding
Revises: 0003_v1a_evidence_review_queue
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_rule_provenance_binding"
down_revision: str | None = "0003_v1a_evidence_review_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "fk_parking_rules_evidence_segment_binding"


def upgrade() -> None:
    # Fail before any schema mutation when a legacy rule cites evidence that is not
    # explicitly bound to the rule's segment. Operators can inspect and repair the
    # reported pairs before retrying this transactional migration.
    op.execute(
        """
        DO $$
        DECLARE
            orphan_count bigint;
            orphan_example text;
        BEGIN
            SELECT count(*), min(
                format(
                    'rule_id=%s source_evidence_id=%s segment_id=%s',
                    rules.rule_id,
                    rules.source_evidence_id,
                    rules.segment_id
                )
            )
            INTO orphan_count, orphan_example
            FROM parking_rules AS rules
            LEFT JOIN parking_source_segments AS bindings
              ON bindings.evidence_id = rules.source_evidence_id
             AND bindings.segment_id = rules.segment_id
            WHERE bindings.evidence_id IS NULL;

            IF orphan_count > 0 THEN
                RAISE EXCEPTION
                    'rule provenance has % orphan pair(s); example: %',
                    orphan_count,
                    orphan_example
                    USING ERRCODE = '23503',
                          HINT = 'Repair missing parking_source_segments pairs, then retry.';
            END IF;
        END;
        $$;
        """
    )
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "parking_rules",
        "parking_source_segments",
        ["source_evidence_id", "segment_id"],
        ["evidence_id", "segment_id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "parking_rules", type_="foreignkey")
