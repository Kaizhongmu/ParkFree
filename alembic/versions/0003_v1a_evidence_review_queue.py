"""Add the V1A durable evidence-review queue and append-only audit log.

Revision ID: 0003_v1a_evidence_review_queue
Revises: 0002_phase6_search_replay
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_v1a_evidence_review_queue"
down_revision: str | None = "0002_phase6_search_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_review_queue",
        sa.Column("review_item_id", sa.String(length=64), nullable=False),
        sa.Column("extraction_result_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("service_kind", sa.String(length=32), nullable=False),
        sa.Column("extraction_disposition", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri_or_identifier", sa.Text(), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_storage_policy", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("snapshot_schema_version", sa.String(length=128), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "review_reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "error_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("submitted_by", sa.String(length=128), nullable=False),
        sa.Column("assigned_reviewer_id", sa.String(length=128), nullable=True),
        sa.Column("approval_scope", sa.String(length=32), nullable=True),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column("approved_evidence_id", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED')",
            name="status_value",
        ),
        sa.CheckConstraint(
            "service_kind IN ('REGULATION', 'COMMUNITY', 'VISION')",
            name="service_kind_value",
        ),
        sa.CheckConstraint(
            "extraction_disposition = 'REVIEW_REQUIRED'",
            name="extraction_disposition_value",
        ),
        sa.CheckConstraint(
            "source_type IN ('OFFICIAL_CODE', 'OFFICIAL_GIS', 'VERIFIED_SIGN', "
            "'UNIVERSITY', 'OSM', 'COMMUNITY', 'WEB', 'IMAGERY_INFERENCE')",
            name="source_type_value",
        ),
        sa.CheckConstraint(
            "raw_storage_policy IN ('PERSIST', 'EPHEMERAL', 'REFERENCE_ONLY')",
            name="raw_storage_policy_value",
        ),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
        sa.CheckConstraint(
            "result_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="result_snapshot_hash_sha256",
        ),
        sa.CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="content_hash_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object' AND "
            "NOT (result_snapshot ?| ARRAY['content', 'raw_content', 'source_content'])",
            name="result_snapshot_safe_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(review_reason_codes) = 'array'",
            name="review_reason_codes_array",
        ),
        sa.CheckConstraint("jsonb_typeof(error_codes) = 'array'", name="error_codes_array"),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at <= retrieved_at",
            name="published_not_after_retrieved",
        ),
        sa.CheckConstraint(
            "observed_at IS NULL OR observed_at <= retrieved_at",
            name="observed_not_after_retrieved",
        ),
        sa.CheckConstraint(
            "updated_at >= submitted_at AND "
            "(claimed_at IS NULL OR claimed_at >= submitted_at) AND "
            "(claim_expires_at IS NULL OR claim_expires_at > claimed_at) AND "
            "(resolved_at IS NULL OR resolved_at >= submitted_at)",
            name="timestamp_order",
        ),
        sa.CheckConstraint(
            "(assigned_reviewer_id IS NULL) = (claimed_at IS NULL) AND "
            "(claimed_at IS NULL) = (claim_expires_at IS NULL)",
            name="claim_assignment_complete",
        ),
        sa.CheckConstraint(
            "claim_expires_at IS NULL OR claim_expires_at > claimed_at",
            name="claim_expiry_after_claim",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND assigned_reviewer_id IS NULL) OR "
            "(status IN ('IN_REVIEW', 'APPROVED', 'REJECTED') AND "
            "assigned_reviewer_id IS NOT NULL)",
            name="assignee_matches_status",
        ),
        sa.CheckConstraint(
            "(status IN ('APPROVED', 'REJECTED') AND resolved_at IS NOT NULL) OR "
            "(status IN ('PENDING', 'IN_REVIEW') AND resolved_at IS NULL)",
            name="resolution_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'APPROVED' AND "
            "approval_scope IN ('EVIDENCE_ONLY', 'EVIDENCE_AND_RULES') AND "
            "approval_id IS NOT NULL AND approved_evidence_id IS NOT NULL) OR "
            "(status <> 'APPROVED' AND approval_scope IS NULL AND "
            "approval_id IS NULL AND approved_evidence_id IS NULL)",
            name="approval_complete",
        ),
        sa.ForeignKeyConstraint(
            ["approved_evidence_id"],
            ["parking_sources.evidence_id"],
            name="fk_evidence_review_queue_approved_evidence_id_parking_sources",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("review_item_id", name="pk_evidence_review_queue"),
        sa.UniqueConstraint(
            "extraction_result_id", name="uq_evidence_review_queue_extraction_result_id"
        ),
        sa.UniqueConstraint("approval_id", name="uq_evidence_review_queue_approval_id"),
        sa.UniqueConstraint(
            "approved_evidence_id", name="uq_evidence_review_queue_approved_evidence_id"
        ),
    )
    op.create_index(
        "ix_evidence_review_queue_status_submitted_at",
        "evidence_review_queue",
        ["status", "submitted_at"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_review_queue_assignee_status",
        "evidence_review_queue",
        ["assigned_reviewer_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_review_queue_service_source",
        "evidence_review_queue",
        ["service_kind", "source_type"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_review_queue_extractor_version",
        "evidence_review_queue",
        ["extractor_version"],
        unique=False,
    )

    op.create_table(
        "evidence_review_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("review_item_id", sa.String(length=64), nullable=False),
        sa.Column("queue_revision", sa.Integer(), nullable=False),
        sa.Column("action_idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("action_request_hash", sa.String(length=64), nullable=False),
        sa.Column("prior_event_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("actor_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column("approved_evidence_id", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("queue_revision > 0", name="queue_revision_positive"),
        sa.CheckConstraint(
            "action IN ('SUBMIT', 'CLAIM', 'RELEASE', 'APPROVE_EVIDENCE', "
            "'APPROVE_RULES', 'REJECT')",
            name="action_value",
        ),
        sa.CheckConstraint(
            "previous_status IS NULL OR previous_status IN "
            "('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED')",
            name="previous_status_value",
        ),
        sa.CheckConstraint(
            "new_status IN ('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED')",
            name="new_status_value",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(actor_roles) = 'array' AND jsonb_array_length(actor_roles) > 0",
            name="actor_roles_nonempty_array",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code ~ '^[A-Z][A-Z0-9_]{0,127}$'",
            name="reason_code_safe",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_snapshot) = 'object' AND "
            "NOT (metadata_snapshot ?| ARRAY['content', 'raw_content', 'source_content'])",
            name="metadata_snapshot_safe_object",
        ),
        sa.CheckConstraint(
            "(action = 'SUBMIT' AND queue_revision = 1) OR "
            "(action <> 'SUBMIT' AND queue_revision > 1)",
            name="initial_revision_matches_action",
        ),
        sa.CheckConstraint(
            "(action = 'SUBMIT' AND previous_status IS NULL AND new_status = 'PENDING') OR "
            "(action = 'CLAIM' AND new_status = 'IN_REVIEW' AND "
            "(previous_status = 'PENDING' OR "
            "(previous_status = 'IN_REVIEW' AND "
            "reason_code = 'LEASE_EXPIRED_RECLAIM'))) OR "
            "(action = 'RELEASE' AND previous_status = 'IN_REVIEW' AND "
            "new_status = 'PENDING') OR "
            "(action IN ('APPROVE_EVIDENCE', 'APPROVE_RULES') AND "
            "previous_status = 'IN_REVIEW' AND new_status = 'APPROVED') OR "
            "(action = 'REJECT' AND previous_status = 'IN_REVIEW' AND "
            "new_status = 'REJECTED')",
            name="action_status_transition",
        ),
        sa.CheckConstraint(
            "(action = 'CLAIM' AND lease_expires_at IS NOT NULL AND "
            "lease_expires_at > occurred_at) OR "
            "(action <> 'CLAIM' AND lease_expires_at IS NULL)",
            name="lease_matches_action",
        ),
        sa.CheckConstraint(
            "(action IN ('APPROVE_EVIDENCE', 'APPROVE_RULES') AND "
            "approval_id IS NOT NULL AND approved_evidence_id IS NOT NULL) OR "
            "(action NOT IN ('APPROVE_EVIDENCE', 'APPROVE_RULES') AND "
            "approval_id IS NULL AND approved_evidence_id IS NULL)",
            name="approval_references_match_action",
        ),
        sa.CheckConstraint(
            "action_idempotency_key_hash ~ '^[0-9a-f]{64}$' AND "
            "action_request_hash ~ '^[0-9a-f]{64}$' AND "
            "event_hash ~ '^[0-9a-f]{64}$' AND "
            "(prior_event_hash IS NULL OR prior_event_hash ~ '^[0-9a-f]{64}$')",
            name="audit_hashes_sha256",
        ),
        sa.CheckConstraint(
            "(queue_revision = 1 AND prior_event_hash IS NULL) OR "
            "(queue_revision > 1 AND prior_event_hash IS NOT NULL)",
            name="prior_hash_matches_revision",
        ),
        sa.ForeignKeyConstraint(
            ["review_item_id"],
            ["evidence_review_queue.review_item_id"],
            name="fk_evidence_review_events_review_item_id_evidence_review_queue",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_evidence_id"],
            ["parking_sources.evidence_id"],
            name="fk_evidence_review_events_approved_evidence_id_parking_sources",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_evidence_review_events"),
        sa.UniqueConstraint(
            "review_item_id",
            "queue_revision",
            name="uq_evidence_review_events_item_revision",
        ),
        sa.UniqueConstraint(
            "action_idempotency_key_hash",
            name="uq_evidence_review_events_action_idempotency_key_hash",
        ),
        sa.UniqueConstraint("event_hash", name="uq_evidence_review_events_event_hash"),
    )
    op.create_index(
        "ix_evidence_review_events_item_occurred_at",
        "evidence_review_events",
        ["review_item_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_review_events_actor_occurred_at",
        "evidence_review_events",
        ["actor_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_review_events_action_occurred_at",
        "evidence_review_events",
        ["action", "occurred_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION validate_evidence_review_event_append() RETURNS trigger AS $$
        DECLARE
            queue_row evidence_review_queue%ROWTYPE;
            prior_row evidence_review_events%ROWTYPE;
        BEGIN
            SELECT * INTO queue_row
            FROM evidence_review_queue
            WHERE review_item_id = NEW.review_item_id
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'review event requires an existing queue item'
                    USING ERRCODE = '23503';
            END IF;
            IF queue_row.revision <> NEW.queue_revision
               OR queue_row.status <> NEW.new_status
               OR queue_row.updated_at <> NEW.occurred_at THEN
                RAISE EXCEPTION 'review event does not match the queue projection'
                    USING ERRCODE = '40001';
            END IF;
            IF NEW.queue_revision = 1 THEN
                IF NEW.action <> 'SUBMIT'
                   OR NEW.actor_id <> queue_row.submitted_by
                   OR NOT (NEW.actor_roles ? 'SUBMITTER') THEN
                    RAISE EXCEPTION 'invalid review submission actor'
                        USING ERRCODE = '42501';
                END IF;
            ELSE
                SELECT * INTO prior_row
                FROM evidence_review_events
                WHERE review_item_id = NEW.review_item_id
                  AND queue_revision = NEW.queue_revision - 1;
                IF NOT FOUND
                   OR NEW.prior_event_hash IS DISTINCT FROM prior_row.event_hash
                   OR NEW.previous_status IS DISTINCT FROM prior_row.new_status
                   OR NEW.occurred_at < prior_row.occurred_at THEN
                    RAISE EXCEPTION 'review event does not extend the audit chain'
                        USING ERRCODE = '40001';
                END IF;
                IF NEW.actor_id = queue_row.submitted_by THEN
                    RAISE EXCEPTION 'submitter cannot review their own extraction'
                        USING ERRCODE = '42501';
                END IF;
            END IF;
            IF NEW.action IN ('CLAIM', 'RELEASE')
               AND NOT (NEW.actor_roles ?| ARRAY['EVIDENCE_REVIEWER', 'REGULATION_PUBLISHER']) THEN
                RAISE EXCEPTION 'actor role cannot manage a review claim'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.action IN ('APPROVE_EVIDENCE', 'REJECT')
               AND NOT (NEW.actor_roles ? 'EVIDENCE_REVIEWER') THEN
                RAISE EXCEPTION 'actor role cannot decide evidence review'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.action = 'APPROVE_RULES'
               AND NOT (NEW.actor_roles ? 'REGULATION_PUBLISHER') THEN
                RAISE EXCEPTION 'actor role cannot publish regulation rules'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.action = 'CLAIM'
               AND (queue_row.assigned_reviewer_id <> NEW.actor_id
                    OR queue_row.claim_expires_at <> NEW.lease_expires_at) THEN
                RAISE EXCEPTION 'claim event does not match queue assignment'
                    USING ERRCODE = '40001';
            END IF;
            IF NEW.action = 'RELEASE' AND queue_row.assigned_reviewer_id IS NOT NULL THEN
                RAISE EXCEPTION 'release event did not clear queue assignment'
                    USING ERRCODE = '40001';
            END IF;
            IF NEW.action IN ('APPROVE_EVIDENCE', 'APPROVE_RULES')
               AND (queue_row.assigned_reviewer_id <> NEW.actor_id
                    OR queue_row.approval_id <> NEW.approval_id
                    OR queue_row.approved_evidence_id <> NEW.approved_evidence_id) THEN
                RAISE EXCEPTION 'approval event does not match queue publication'
                    USING ERRCODE = '40001';
            END IF;
            IF NEW.action = 'REJECT'
               AND queue_row.assigned_reviewer_id <> NEW.actor_id THEN
                RAISE EXCEPTION 'rejection actor does not match queue assignment'
                    USING ERRCODE = '40001';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_validate_evidence_review_event_append
        BEFORE INSERT ON evidence_review_events
        FOR EACH ROW EXECUTE FUNCTION validate_evidence_review_event_append();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_approved_review_evidence() RETURNS trigger AS $$
        DECLARE
            old_evidence_key varchar(64);
            new_evidence_key varchar(64);
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                old_evidence_key := OLD.evidence_id;
                PERFORM pg_advisory_xact_lock(hashtextextended(old_evidence_key, 0));
            END IF;
            IF TG_OP <> 'DELETE' THEN
                new_evidence_key := NEW.evidence_id;
                PERFORM pg_advisory_xact_lock(hashtextextended(new_evidence_key, 0));
            END IF;
            IF EXISTS (
                SELECT 1 FROM evidence_review_queue
                WHERE status = 'APPROVED'
                  AND approved_evidence_id IN (old_evidence_key, new_evidence_key)
            ) THEN
                RAISE EXCEPTION 'approved reviewed evidence is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_approved_review_evidence
        BEFORE INSERT OR UPDATE OR DELETE ON parking_sources
        FOR EACH ROW EXECUTE FUNCTION protect_approved_review_evidence();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_approved_review_rules() RETURNS trigger AS $$
        DECLARE
            old_evidence_key varchar(64);
            new_evidence_key varchar(64);
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                old_evidence_key := OLD.source_evidence_id;
                PERFORM pg_advisory_xact_lock(hashtextextended(old_evidence_key, 0));
            END IF;
            IF TG_OP <> 'DELETE' THEN
                new_evidence_key := NEW.source_evidence_id;
                PERFORM pg_advisory_xact_lock(hashtextextended(new_evidence_key, 0));
            END IF;
            IF EXISTS (
                SELECT 1 FROM evidence_review_queue
                WHERE status = 'APPROVED'
                  AND approved_evidence_id IN (old_evidence_key, new_evidence_key)
            ) THEN
                RAISE EXCEPTION 'approved reviewed rules are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_approved_review_rules
        BEFORE INSERT OR UPDATE OR DELETE ON parking_rules
        FOR EACH ROW EXECUTE FUNCTION protect_approved_review_rules();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_approved_review_segments() RETURNS trigger AS $$
        DECLARE
            evidence_key varchar(64);
        BEGIN
            IF TG_OP = 'DELETE' THEN
                evidence_key := OLD.evidence_id;
            ELSE
                evidence_key := NEW.evidence_id;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(evidence_key, 0));
            IF TG_OP = 'UPDATE' AND OLD.evidence_id <> evidence_key THEN
                PERFORM pg_advisory_xact_lock(hashtextextended(OLD.evidence_id, 0));
            END IF;
            IF EXISTS (
                SELECT 1 FROM evidence_review_queue
                WHERE status = 'APPROVED' AND approved_evidence_id = evidence_key
            ) OR (
                TG_OP = 'UPDATE' AND EXISTS (
                    SELECT 1 FROM evidence_review_queue
                    WHERE status = 'APPROVED'
                      AND approved_evidence_id = OLD.evidence_id
                )
            ) THEN
                RAISE EXCEPTION 'approved reviewed segment bindings are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_approved_review_segments
        BEFORE INSERT OR UPDATE OR DELETE ON parking_source_segments
        FOR EACH ROW EXECUTE FUNCTION protect_approved_review_segments();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_evidence_review_queue() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'evidence review queue rows cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.status IN ('APPROVED', 'REJECTED') THEN
                RAISE EXCEPTION 'resolved evidence review rows are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.revision <> OLD.revision + 1 THEN
                RAISE EXCEPTION 'evidence review revision must increment by one'
                    USING ERRCODE = '40001';
            END IF;
            IF ROW(
                NEW.extraction_result_id,
                NEW.service_kind,
                NEW.extraction_disposition,
                NEW.extractor_version,
                NEW.source_type,
                NEW.source_uri_or_identifier,
                NEW.publisher,
                NEW.published_at,
                NEW.observed_at,
                NEW.retrieved_at,
                NEW.raw_storage_policy,
                NEW.content_hash,
                NEW.snapshot_schema_version,
                NEW.result_snapshot,
                NEW.result_snapshot_hash,
                NEW.review_reason_codes,
                NEW.error_codes,
                NEW.submitted_by,
                NEW.submitted_at
            ) IS DISTINCT FROM ROW(
                OLD.extraction_result_id,
                OLD.service_kind,
                OLD.extraction_disposition,
                OLD.extractor_version,
                OLD.source_type,
                OLD.source_uri_or_identifier,
                OLD.publisher,
                OLD.published_at,
                OLD.observed_at,
                OLD.retrieved_at,
                OLD.raw_storage_policy,
                OLD.content_hash,
                OLD.snapshot_schema_version,
                OLD.result_snapshot,
                OLD.result_snapshot_hash,
                OLD.review_reason_codes,
                OLD.error_codes,
                OLD.submitted_by,
                OLD.submitted_at
            ) THEN
                RAISE EXCEPTION 'evidence review proposal fields are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_evidence_review_queue
        BEFORE UPDATE OR DELETE ON evidence_review_queue
        FOR EACH ROW EXECUTE FUNCTION protect_evidence_review_queue();
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_evidence_review_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evidence review events are append-only'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidence_review_events_append_only
        BEFORE UPDATE OR DELETE ON evidence_review_events
        FOR EACH ROW EXECUTE FUNCTION prevent_evidence_review_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_approved_review_segments ON parking_source_segments"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_approved_review_segments()")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_approved_review_rules ON parking_rules")
    op.execute("DROP FUNCTION IF EXISTS protect_approved_review_rules()")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_approved_review_evidence ON parking_sources")
    op.execute("DROP FUNCTION IF EXISTS protect_approved_review_evidence()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_evidence_review_event_append ON evidence_review_events"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_evidence_review_event_append()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_evidence_review_events_append_only ON evidence_review_events"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_evidence_review_event_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_evidence_review_queue ON evidence_review_queue")
    op.execute("DROP FUNCTION IF EXISTS protect_evidence_review_queue()")

    op.drop_index(
        "ix_evidence_review_events_action_occurred_at", table_name="evidence_review_events"
    )
    op.drop_index(
        "ix_evidence_review_events_actor_occurred_at", table_name="evidence_review_events"
    )
    op.drop_index("ix_evidence_review_events_item_occurred_at", table_name="evidence_review_events")
    op.drop_table("evidence_review_events")

    op.drop_index("ix_evidence_review_queue_extractor_version", table_name="evidence_review_queue")
    op.drop_index("ix_evidence_review_queue_service_source", table_name="evidence_review_queue")
    op.drop_index("ix_evidence_review_queue_assignee_status", table_name="evidence_review_queue")
    op.drop_index(
        "ix_evidence_review_queue_status_submitted_at", table_name="evidence_review_queue"
    )
    op.drop_table("evidence_review_queue")
