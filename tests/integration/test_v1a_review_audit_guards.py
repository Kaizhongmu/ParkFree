from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from parking_ai.database.models import EvidenceModel
from parking_ai.domain import (
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def test_review_proposal_and_audit_events_are_database_immutable(engine: Engine) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO evidence_review_queue (
                        review_item_id, extraction_result_id, status, service_kind,
                        extraction_disposition, extractor_version, source_type,
                        source_uri_or_identifier, retrieved_at, raw_storage_policy,
                        snapshot_schema_version, result_snapshot, result_snapshot_hash,
                        review_reason_codes, error_codes, submitted_by, revision,
                        submitted_at, updated_at
                    ) VALUES (
                        :review_item_id, :extraction_result_id, 'PENDING', 'REGULATION',
                        'REVIEW_REQUIRED', 'fixture-extractor-v1', 'OFFICIAL_CODE',
                        'fixture://v1a/review', :now, 'REFERENCE_ONLY',
                        'v1a-review-snapshot-v1', CAST(:snapshot AS jsonb), :snapshot_hash,
                        CAST(:reasons AS jsonb), CAST(:errors AS jsonb), 'fixture-submitter', 1,
                        :now, :now
                    )
                    """
                ),
                {
                    "review_item_id": "review-guard-test",
                    "extraction_result_id": "extract-guard-test",
                    "now": NOW,
                    "snapshot": '{"result_id":"extract-guard-test"}',
                    "snapshot_hash": HASH_A,
                    "reasons": '["HUMAN_VERIFICATION_REQUIRED"]',
                    "errors": "[]",
                },
            )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO evidence_review_events (
                            event_id, review_item_id, queue_revision,
                            action_idempotency_key_hash, action_request_hash,
                            prior_event_hash, event_hash, actor_id, actor_roles,
                            action, reason_code, previous_status, new_status, occurred_at,
                            metadata_snapshot
                        ) VALUES (
                            'review-event-wrong-role', 'review-guard-test', 1,
                            :idempotency_hash, :request_hash, NULL, :event_hash,
                            'fixture-submitter', CAST('["EVIDENCE_REVIEWER"]' AS jsonb),
                            'SUBMIT', 'EXTRACTION_READY', NULL, 'PENDING', :now,
                            CAST('{}' AS jsonb)
                        )
                        """
                    ),
                    {
                        "idempotency_hash": "d" * 64,
                        "request_hash": "e" * 64,
                        "event_hash": "f" * 64,
                        "now": NOW,
                    },
                )
            connection.execute(
                text(
                    """
                    INSERT INTO evidence_review_events (
                        event_id, review_item_id, queue_revision,
                        action_idempotency_key_hash, action_request_hash,
                        prior_event_hash, event_hash, actor_id, actor_roles,
                        action, reason_code, previous_status, new_status, occurred_at,
                        metadata_snapshot
                    ) VALUES (
                        'review-event-guard-test', 'review-guard-test', 1,
                        :idempotency_hash, :request_hash, NULL, :event_hash,
                        'fixture-submitter', CAST('["SUBMITTER"]' AS jsonb),
                        'SUBMIT', 'EXTRACTION_READY', NULL, 'PENDING', :now,
                        CAST('{}' AS jsonb)
                    )
                    """
                ),
                {
                    "idempotency_hash": HASH_A,
                    "request_hash": HASH_B,
                    "event_hash": HASH_C,
                    "now": NOW,
                },
            )

            with pytest.raises(DBAPIError), connection.begin_nested():
                claim_time = NOW.replace(minute=1)
                claim_expiry = NOW.replace(minute=16)
                connection.execute(
                    text(
                        """
                        UPDATE evidence_review_queue
                        SET status = 'IN_REVIEW', assigned_reviewer_id = 'reviewer-2',
                            claimed_at = :claim_time, claim_expires_at = :claim_expiry,
                            revision = 2, updated_at = :claim_time
                        WHERE review_item_id = 'review-guard-test'
                        """
                    ),
                    {"claim_time": claim_time, "claim_expiry": claim_expiry},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO evidence_review_events (
                            event_id, review_item_id, queue_revision,
                            action_idempotency_key_hash, action_request_hash,
                            prior_event_hash, event_hash, actor_id, actor_roles,
                            action, reason_code, previous_status, new_status,
                            lease_expires_at, occurred_at, metadata_snapshot
                        ) VALUES (
                            'review-event-bad-chain', 'review-guard-test', 2,
                            :idempotency_hash, :request_hash, :wrong_prior_hash, :event_hash,
                            'reviewer-2', CAST('["EVIDENCE_REVIEWER"]' AS jsonb),
                            'CLAIM', 'REVIEW_STARTED', 'PENDING', 'IN_REVIEW',
                            :claim_expiry, :claim_time, CAST('{}' AS jsonb)
                        )
                        """
                    ),
                    {
                        "idempotency_hash": "1" * 64,
                        "request_hash": "2" * 64,
                        "wrong_prior_hash": "3" * 64,
                        "event_hash": "4" * 64,
                        "claim_time": claim_time,
                        "claim_expiry": claim_expiry,
                    },
                )

            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE evidence_review_events SET actor_id = 'tampered' "
                        "WHERE event_id = 'review-event-guard-test'"
                    )
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "DELETE FROM evidence_review_events "
                        "WHERE event_id = 'review-event-guard-test'"
                    )
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE evidence_review_queue "
                        "SET source_uri_or_identifier = 'tampered', revision = 2 "
                        "WHERE review_item_id = 'review-guard-test'"
                    )
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "DELETE FROM evidence_review_queue "
                        "WHERE review_item_id = 'review-guard-test'"
                    )
                )
        finally:
            transaction.rollback()


def test_review_publication_writes_share_the_approval_transaction_lock(
    engine: Engine,
) -> None:
    evidence_id = "review-lock-evidence"
    with Session(engine) as setup:
        setup.add(
            EvidenceModel(
                evidence_id=evidence_id,
                source_type=EvidenceSourceType.OFFICIAL_CODE,
                source_uri_or_identifier="fixture://v1a/lock",
                publisher="Fixture Authority",
                retrieved_at=NOW,
                raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
                normalized_claims=[],
                reliability_tier=EvidenceReliabilityTier.A,
                extractor_version="fixture-v1",
                content_hash=HASH_A,
            )
        )
        setup.commit()

    first = engine.connect()
    second = engine.connect()
    first_transaction = first.begin()
    second_transaction = second.begin()
    try:
        first.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:evidence_id, 0))"),
            {"evidence_id": evidence_id},
        )
        second.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(DBAPIError):
            second.execute(
                text(
                    "UPDATE parking_sources SET publisher = 'racing writer' "
                    "WHERE evidence_id = :evidence_id"
                ),
                {"evidence_id": evidence_id},
            )
    finally:
        second_transaction.rollback()
        first_transaction.rollback()
        second.close()
        first.close()

    with Session(engine) as cleanup:
        stored = cleanup.get(EvidenceModel, evidence_id)
        assert stored is not None
        assert stored.publisher == "Fixture Authority"
        cleanup.delete(stored)
        cleanup.commit()
