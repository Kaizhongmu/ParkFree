"""SQLAlchemy persistence adapter for the durable evidence-review event stream."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from parking_ai.agents import (
    ApprovalScope,
    EvidenceExtractionResult,
    approve_extraction,
)
from parking_ai.database.models import EvidenceReviewEventModel, EvidenceReviewQueueModel
from parking_ai.evidence.persistence import (
    EvidencePersistenceConflictError,
    persist_approved_evidence,
    validate_persisted_approved_evidence,
)
from parking_ai.evidence.review_queue import (
    ReviewActor,
    ReviewQueueAction,
    ReviewQueueConflictError,
    ReviewQueueEvent,
    ReviewQueueItem,
    ReviewQueueSnapshot,
    ReviewQueueStatus,
    ReviewReasonCode,
    ReviewRole,
    replay_review_events,
    review_audit_event_hash,
    review_extraction_snapshot_hash,
)

_QUEUE_SNAPSHOT_SCHEMA_VERSION = "v1a-review-queue-snapshot-v1"
_EVENT_METADATA = {"event_schema_version": "v1a-review-event-v1"}


class SQLAlchemyReviewQueueRepository:
    """Persist review transitions in the caller-owned SQLAlchemy transaction.

    The adapter never commits.  An approval transition stages the reviewed evidence/rules, queue
    state update, and append-only audit event in one database savepoint so the caller can commit
    all four effects atomically.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_events(self, queue_item_id: str) -> Sequence[ReviewQueueEvent]:
        queue_row = self._session.get(EvidenceReviewQueueModel, queue_item_id)
        if queue_row is None:
            return ()
        event_rows = tuple(
            self._session.scalars(
                select(EvidenceReviewEventModel)
                .where(EvidenceReviewEventModel.review_item_id == queue_item_id)
                .order_by(EvidenceReviewEventModel.queue_revision)
            )
        )
        if not event_rows:
            raise ReviewQueueConflictError("persisted review item has no audit events")

        try:
            result = EvidenceExtractionResult.model_validate(queue_row.result_snapshot)
            item = ReviewQueueItem(
                queue_item_id=queue_row.review_item_id,
                extraction_result_id=queue_row.extraction_result_id,
                extraction_snapshot_hash=queue_row.result_snapshot_hash,
                extraction_result=result,
                submitted_by=queue_row.submitted_by,
                submitted_at=queue_row.submitted_at,
            )
            self._validate_queue_proposal_mirrors(queue_row, result)
            events = self._hydrate_events(item, event_rows)
            snapshot = replay_review_events(events)
            self._validate_queue_projection(queue_row, snapshot, event_rows)
            if snapshot.approved_bundle is not None:
                validate_persisted_approved_evidence(
                    self._session,
                    snapshot.approved_bundle,
                )
        except (EvidencePersistenceConflictError, ValidationError, ValueError) as error:
            if isinstance(error, ReviewQueueConflictError):
                raise
            raise ReviewQueueConflictError(
                "persisted review stream failed integrity validation"
            ) from error
        return events

    def append_event(
        self,
        event: ReviewQueueEvent,
        *,
        expected_revision: int,
    ) -> ReviewQueueEvent:
        duplicate = self._session.scalar(
            select(EvidenceReviewEventModel).where(
                EvidenceReviewEventModel.action_idempotency_key_hash == event.idempotency_key_hash
            )
        )
        if duplicate is not None:
            return self._resolve_idempotent_event(duplicate, event)

        if event.action is ReviewQueueAction.SUBMIT:
            return self._append_submission(event, expected_revision=expected_revision)
        return self._append_transition(event, expected_revision=expected_revision)

    def _append_submission(
        self,
        event: ReviewQueueEvent,
        *,
        expected_revision: int,
    ) -> ReviewQueueEvent:
        if expected_revision != 0 or event.item is None:
            raise ReviewQueueConflictError("submission requires an empty event stream")
        if self._session.get(EvidenceReviewQueueModel, event.queue_item_id) is not None:
            duplicate = self._session.scalar(
                select(EvidenceReviewEventModel).where(
                    EvidenceReviewEventModel.action_idempotency_key_hash
                    == event.idempotency_key_hash
                )
            )
            if duplicate is not None:
                return self._resolve_idempotent_event(duplicate, event)
            raise ReviewQueueConflictError("extraction result is already queued")

        item = event.item
        result = item.extraction_result
        evidence = result.evidence
        if evidence is None:
            raise ReviewQueueConflictError("review submission is missing evidence")
        queue_row = EvidenceReviewQueueModel(
            review_item_id=item.queue_item_id,
            extraction_result_id=item.extraction_result_id,
            status=event.new_status.value,
            service_kind=result.service_kind.value,
            extraction_disposition=result.disposition.value,
            extractor_version=result.extractor_version,
            source_type=evidence.source_type.value,
            source_uri_or_identifier=evidence.source_uri_or_identifier,
            publisher=evidence.publisher,
            published_at=evidence.published_at,
            observed_at=evidence.observed_at,
            retrieved_at=evidence.retrieved_at,
            raw_storage_policy=evidence.raw_storage_policy.value,
            content_hash=evidence.content_hash,
            snapshot_schema_version=_QUEUE_SNAPSHOT_SCHEMA_VERSION,
            result_snapshot=result.model_dump(mode="json"),
            result_snapshot_hash=item.extraction_snapshot_hash,
            review_reason_codes=[reason.value for reason in result.review_reasons],
            error_codes=result.validation_errors,
            submitted_by=item.submitted_by,
            revision=event.revision,
            submitted_at=item.submitted_at,
            updated_at=event.occurred_at,
        )
        try:
            with self._session.begin_nested():
                self._session.add(queue_row)
                self._session.flush()
                self._session.add(self._event_model(event, prior_event_hash=None))
                self._session.flush()
        except IntegrityError as error:
            duplicate = self._session.scalar(
                select(EvidenceReviewEventModel).where(
                    EvidenceReviewEventModel.action_idempotency_key_hash
                    == event.idempotency_key_hash
                )
            )
            if duplicate is not None:
                return self._resolve_idempotent_event(duplicate, event)
            raise ReviewQueueConflictError(
                "review submission conflicted with stored state"
            ) from error
        return event

    def _append_transition(
        self,
        event: ReviewQueueEvent,
        *,
        expected_revision: int,
    ) -> ReviewQueueEvent:
        queue_row = self._session.scalar(
            select(EvidenceReviewQueueModel)
            .where(EvidenceReviewQueueModel.review_item_id == event.queue_item_id)
            .with_for_update()
        )
        if queue_row is None:
            raise ReviewQueueConflictError("review queue item was not found")
        if queue_row.revision != expected_revision:
            duplicate = self._session.scalar(
                select(EvidenceReviewEventModel).where(
                    EvidenceReviewEventModel.action_idempotency_key_hash
                    == event.idempotency_key_hash
                )
            )
            if duplicate is not None:
                return self._resolve_idempotent_event(duplicate, event)
            raise ReviewQueueConflictError("stale review queue revision")

        current_events = tuple(self.list_events(event.queue_item_id))
        current = replay_review_events(current_events)
        if current.revision != expected_revision or event.previous_status is not current.status:
            raise ReviewQueueConflictError("review transition does not match stored state")
        prior_row = self._session.scalar(
            select(EvidenceReviewEventModel).where(
                EvidenceReviewEventModel.review_item_id == event.queue_item_id,
                EvidenceReviewEventModel.queue_revision == expected_revision,
            )
        )
        if prior_row is None:
            raise ReviewQueueConflictError("review audit chain is incomplete")

        try:
            with self._session.begin_nested():
                if event.approved_bundle is not None:
                    persist_approved_evidence(self._session, event.approved_bundle)
                self._project_transition(queue_row, event)
                self._session.flush()
                self._session.add(self._event_model(event, prior_event_hash=prior_row.event_hash))
                self._session.flush()
        except IntegrityError as error:
            duplicate = self._session.scalar(
                select(EvidenceReviewEventModel).where(
                    EvidenceReviewEventModel.action_idempotency_key_hash
                    == event.idempotency_key_hash
                )
            )
            if duplicate is not None:
                return self._resolve_idempotent_event(duplicate, event)
            raise ReviewQueueConflictError(
                "review transition conflicted with stored state"
            ) from error
        return event

    def _resolve_idempotent_event(
        self,
        stored_row: EvidenceReviewEventModel,
        requested: ReviewQueueEvent,
    ) -> ReviewQueueEvent:
        if stored_row.action_request_hash != requested.request_hash:
            raise ReviewQueueConflictError(
                "idempotency key was already used for a different review request"
            )
        stored_event = next(
            (
                candidate
                for candidate in self.list_events(stored_row.review_item_id)
                if candidate.idempotency_key_hash == requested.idempotency_key_hash
            ),
            None,
        )
        if stored_event is None:
            raise ReviewQueueConflictError("stored idempotency event could not be replayed")
        return stored_event

    @staticmethod
    def _project_transition(
        queue_row: EvidenceReviewQueueModel,
        event: ReviewQueueEvent,
    ) -> None:
        queue_row.status = event.new_status.value
        queue_row.revision = event.revision
        queue_row.updated_at = event.occurred_at
        if event.action is ReviewQueueAction.CLAIM:
            queue_row.assigned_reviewer_id = event.actor.actor_id
            queue_row.claimed_at = event.occurred_at
            queue_row.claim_expires_at = event.lease_expires_at
        elif event.action is ReviewQueueAction.RELEASE:
            queue_row.assigned_reviewer_id = None
            queue_row.claimed_at = None
            queue_row.claim_expires_at = None
        elif event.action in {
            ReviewQueueAction.APPROVE_EVIDENCE,
            ReviewQueueAction.APPROVE_RULES,
        }:
            bundle = event.approved_bundle
            if bundle is None or event.approval_scope is None:
                raise ReviewQueueConflictError("approval transition lacks approved output")
            queue_row.approval_scope = event.approval_scope.value
            queue_row.approval_id = bundle.approval_id
            queue_row.approved_evidence_id = bundle.evidence.evidence_id
            queue_row.resolved_at = event.occurred_at
        elif event.action is ReviewQueueAction.REJECT:
            queue_row.resolved_at = event.occurred_at

    @staticmethod
    def _event_model(
        event: ReviewQueueEvent,
        *,
        prior_event_hash: str | None,
    ) -> EvidenceReviewEventModel:
        bundle = event.approved_bundle
        return EvidenceReviewEventModel(
            event_id=event.event_id,
            review_item_id=event.queue_item_id,
            queue_revision=event.revision,
            action_idempotency_key_hash=event.idempotency_key_hash,
            action_request_hash=event.request_hash,
            prior_event_hash=prior_event_hash,
            event_hash=review_audit_event_hash(
                event,
                prior_event_hash=prior_event_hash,
            ),
            actor_id=event.actor.actor_id,
            actor_roles=[role.value for role in event.actor.roles],
            action=event.action.value,
            reason_code=event.reason_code.value,
            previous_status=(
                event.previous_status.value if event.previous_status is not None else None
            ),
            new_status=event.new_status.value,
            lease_expires_at=event.lease_expires_at,
            approval_id=bundle.approval_id if bundle is not None else None,
            approved_evidence_id=(bundle.evidence.evidence_id if bundle is not None else None),
            occurred_at=event.occurred_at,
            metadata_snapshot=dict(_EVENT_METADATA),
        )

    @staticmethod
    def _hydrate_events(
        item: ReviewQueueItem,
        rows: Sequence[EvidenceReviewEventModel],
    ) -> tuple[ReviewQueueEvent, ...]:
        events: list[ReviewQueueEvent] = []
        prior_hash: str | None = None
        for row in rows:
            if row.metadata_snapshot != _EVENT_METADATA:
                raise ReviewQueueConflictError("review audit event metadata is invalid")
            action = ReviewQueueAction(row.action)
            if row.reason_code is None:
                raise ReviewQueueConflictError("review audit event lacks a reason code")
            actor = ReviewActor(
                actor_id=row.actor_id,
                roles=tuple(ReviewRole(role) for role in row.actor_roles),
            )
            scope = (
                ApprovalScope.EVIDENCE_ONLY
                if action is ReviewQueueAction.APPROVE_EVIDENCE
                else (
                    ApprovalScope.EVIDENCE_AND_RULES
                    if action is ReviewQueueAction.APPROVE_RULES
                    else None
                )
            )
            bundle = (
                approve_extraction(
                    item.extraction_result,
                    reviewer_id=actor.actor_id,
                    reviewed_at=row.occurred_at,
                    scope=scope,
                )
                if scope is not None
                else None
            )
            event = ReviewQueueEvent(
                event_id=row.event_id,
                queue_item_id=row.review_item_id,
                revision=row.queue_revision,
                action=action,
                actor=actor,
                reason_code=ReviewReasonCode(row.reason_code),
                occurred_at=row.occurred_at,
                previous_status=(
                    ReviewQueueStatus(row.previous_status)
                    if row.previous_status is not None
                    else None
                ),
                new_status=ReviewQueueStatus(row.new_status),
                idempotency_key_hash=row.action_idempotency_key_hash,
                request_hash=row.action_request_hash,
                lease_expires_at=row.lease_expires_at,
                item=item if action is ReviewQueueAction.SUBMIT else None,
                approval_scope=scope,
                approved_bundle=bundle,
            )
            if row.prior_event_hash != prior_hash:
                raise ReviewQueueConflictError("review audit prior hash does not match its chain")
            if row.event_hash != review_audit_event_hash(
                event,
                prior_event_hash=prior_hash,
            ):
                raise ReviewQueueConflictError("review audit event hash is invalid")
            if bundle is not None and (
                row.approval_id != bundle.approval_id
                or row.approved_evidence_id != bundle.evidence.evidence_id
            ):
                raise ReviewQueueConflictError("review approval references do not match output")
            if bundle is None and (
                row.approval_id is not None or row.approved_evidence_id is not None
            ):
                raise ReviewQueueConflictError("non-approval event has approval references")
            events.append(event)
            prior_hash = row.event_hash
        return tuple(events)

    @staticmethod
    def _validate_queue_proposal_mirrors(
        row: EvidenceReviewQueueModel,
        result: EvidenceExtractionResult,
    ) -> None:
        evidence = result.evidence
        if evidence is None:
            raise ReviewQueueConflictError("persisted review proposal has no evidence")
        expected: dict[str, Any] = {
            "extraction_result_id": result.result_id,
            "service_kind": result.service_kind.value,
            "extraction_disposition": result.disposition.value,
            "extractor_version": result.extractor_version,
            "source_type": evidence.source_type.value,
            "source_uri_or_identifier": evidence.source_uri_or_identifier,
            "publisher": evidence.publisher,
            "published_at": evidence.published_at,
            "observed_at": evidence.observed_at,
            "retrieved_at": evidence.retrieved_at,
            "raw_storage_policy": evidence.raw_storage_policy.value,
            "content_hash": evidence.content_hash,
            "snapshot_schema_version": _QUEUE_SNAPSHOT_SCHEMA_VERSION,
            "result_snapshot_hash": review_extraction_snapshot_hash(result),
            "review_reason_codes": [reason.value for reason in result.review_reasons],
            "error_codes": result.validation_errors,
        }
        if any(getattr(row, name) != value for name, value in expected.items()):
            raise ReviewQueueConflictError("review queue proposal mirrors are inconsistent")

    @staticmethod
    def _validate_queue_projection(
        row: EvidenceReviewQueueModel,
        snapshot: ReviewQueueSnapshot,
        event_rows: Sequence[EvidenceReviewEventModel],
    ) -> None:
        last = event_rows[-1]
        expected_resolved_at = (
            last.occurred_at
            if snapshot.status in {ReviewQueueStatus.APPROVED, ReviewQueueStatus.REJECTED}
            else None
        )
        expected_approval_id = (
            snapshot.approved_bundle.approval_id if snapshot.approved_bundle is not None else None
        )
        expected_evidence_id = (
            snapshot.approved_bundle.evidence.evidence_id
            if snapshot.approved_bundle is not None
            else None
        )
        expected_claimed_at = next(
            (
                event.occurred_at
                for event in reversed(event_rows)
                if event.action == ReviewQueueAction.CLAIM.value
            ),
            None,
        )
        if snapshot.status is ReviewQueueStatus.PENDING:
            expected_claimed_at = None
        projection = {
            "status": snapshot.status.value,
            "revision": snapshot.revision,
            "assigned_reviewer_id": snapshot.assignee_id,
            "claim_expires_at": snapshot.claim_expires_at,
            "claimed_at": expected_claimed_at,
            "approval_scope": (
                snapshot.approval_scope.value if snapshot.approval_scope is not None else None
            ),
            "approval_id": expected_approval_id,
            "approved_evidence_id": expected_evidence_id,
            "updated_at": last.occurred_at,
            "resolved_at": expected_resolved_at,
        }
        if any(getattr(row, name) != value for name, value in projection.items()):
            raise ReviewQueueConflictError(
                "review queue projection does not match its event stream"
            )
