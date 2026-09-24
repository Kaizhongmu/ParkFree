"""Require every durable review-queue projection to have an audit event.

Revision ID: 0005_review_queue_event_guard
Revises: 0004_rule_provenance_binding
Create Date: 2026-09-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_review_queue_event_guard"
down_revision: str | None = "0004_rule_provenance_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGER_NAME = "trg_require_evidence_review_projection_event"
FUNCTION_NAME = "require_evidence_review_projection_event"


def upgrade() -> None:
    # Do not manufacture missing audit history. An operator must investigate any
    # projection whose current revision is not backed by its matching event.
    # Hold writers through preflight and trigger installation so no projection can
    # commit in the gap between the scan and the new deferred constraint.
    op.execute("LOCK TABLE evidence_review_queue IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        DECLARE
            invalid_count bigint;
            invalid_example text;
        BEGIN
            SELECT count(*), min(
                format(
                    'review_item_id=%s revision=%s',
                    queue.review_item_id,
                    queue.revision
                )
            )
            INTO invalid_count, invalid_example
            FROM evidence_review_queue AS queue
            LEFT JOIN evidence_review_events AS event
              ON event.review_item_id = queue.review_item_id
             AND event.queue_revision = queue.revision
            WHERE event.event_id IS NULL
               OR event.new_status IS DISTINCT FROM queue.status
               OR event.occurred_at IS DISTINCT FROM queue.updated_at;

            IF invalid_count > 0 THEN
                RAISE EXCEPTION
                    'review queue has % projection(s) without a matching audit event; example: %',
                    invalid_count,
                    invalid_example
                    USING ERRCODE = '23514',
                          HINT = 'Audit the affected review stream before retrying; '
                              || 'do not synthesize history.';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION_NAME}() RETURNS trigger AS $$
        DECLARE
            matching_event evidence_review_events%ROWTYPE;
        BEGIN
            SELECT * INTO matching_event
            FROM evidence_review_events
            WHERE review_item_id = NEW.review_item_id
              AND queue_revision = NEW.revision;

            IF NOT FOUND
               OR matching_event.new_status IS DISTINCT FROM NEW.status
               OR matching_event.occurred_at IS DISTINCT FROM NEW.updated_at THEN
                RAISE EXCEPTION
                    'review queue projection requires a matching audit event'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"""
        CREATE CONSTRAINT TRIGGER {TRIGGER_NAME}
        AFTER INSERT OR UPDATE ON evidence_review_queue
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}();
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON evidence_review_queue")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_NAME}()")
