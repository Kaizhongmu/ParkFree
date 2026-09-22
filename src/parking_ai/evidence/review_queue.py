"""Provider-independent durable-review workflow for Phase 8 extraction results.

The queue is modeled as an append-only event stream.  This module deliberately contains no
database, HTTP, identity-provider, or raw-source-content implementation; callers inject a
repository and trusted actor identity.  Replaying repository events reconstructs and validates
the current snapshot before every transition.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from parking_ai.agents.evidence_services import (
    ApprovalScope,
    ApprovedEvidenceBundle,
    EvidenceExtractionResult,
    EvidenceServiceKind,
    ExtractionDisposition,
    approve_extraction,
    validate_extraction_result_integrity,
)


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(UTC)


AwareDateTime = Annotated[datetime, AfterValidator(_require_timezone)]


class ReviewQueueModel(BaseModel):
    """Strict immutable base for persisted workflow values."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewQueueStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewQueueAction(StrEnum):
    SUBMIT = "SUBMIT"
    CLAIM = "CLAIM"
    RELEASE = "RELEASE"
    APPROVE_EVIDENCE = "APPROVE_EVIDENCE"
    APPROVE_RULES = "APPROVE_RULES"
    REJECT = "REJECT"


class ReviewReasonCode(StrEnum):
    EXTRACTION_READY = "EXTRACTION_READY"
    REVIEW_STARTED = "REVIEW_STARTED"
    LEASE_EXPIRED_RECLAIM = "LEASE_EXPIRED_RECLAIM"
    REVIEW_RELEASED = "REVIEW_RELEASED"
    EVIDENCE_VERIFIED = "EVIDENCE_VERIFIED"
    REGULATION_VERIFIED = "REGULATION_VERIFIED"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    INVALID_EXTRACTION = "INVALID_EXTRACTION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ReviewRole(StrEnum):
    SUBMITTER = "SUBMITTER"
    EVIDENCE_REVIEWER = "EVIDENCE_REVIEWER"
    REGULATION_PUBLISHER = "REGULATION_PUBLISHER"


class ReviewActor(ReviewQueueModel):
    """Trusted identity and roles supplied by an authorization boundary."""

    actor_id: str = Field(min_length=1, max_length=128)
    roles: tuple[ReviewRole, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def normalize_actor(self) -> ReviewActor:
        actor_id = self.actor_id.strip()
        if not actor_id:
            raise ValueError("actor_id must be nonblank")
        roles = tuple(sorted(set(self.roles), key=lambda role: role.value))
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "roles", roles)
        return self


class ReviewQueueItem(ReviewQueueModel):
    """Immutable submitted extraction snapshot; raw source content is never included."""

    queue_item_id: str = Field(min_length=1, max_length=64)
    extraction_result_id: str = Field(min_length=1, max_length=64)
    extraction_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction_result: EvidenceExtractionResult
    submitted_by: str = Field(min_length=1, max_length=128)
    submitted_at: AwareDateTime

    @model_validator(mode="after")
    def validate_result_binding(self) -> ReviewQueueItem:
        validate_extraction_result_integrity(self.extraction_result)
        if self.extraction_result_id != self.extraction_result.result_id:
            raise ValueError("extraction_result_id must match the extraction result")
        if self.extraction_snapshot_hash != review_extraction_snapshot_hash(self.extraction_result):
            raise ValueError("extraction snapshot hash does not match the extraction result")
        if (
            self.extraction_result.disposition is not ExtractionDisposition.REVIEW_REQUIRED
            or self.extraction_result.evidence is None
        ):
            raise ValueError("queue items require review-required extraction evidence")
        return self


class ReviewQueueEvent(ReviewQueueModel):
    """One append-only workflow transition."""

    event_id: str = Field(min_length=1, max_length=64)
    queue_item_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(gt=0)
    action: ReviewQueueAction
    actor: ReviewActor
    reason_code: ReviewReasonCode
    occurred_at: AwareDateTime
    previous_status: ReviewQueueStatus | None
    new_status: ReviewQueueStatus
    idempotency_key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease_expires_at: AwareDateTime | None = None
    item: ReviewQueueItem | None = None
    approval_scope: ApprovalScope | None = None
    approved_bundle: ApprovedEvidenceBundle | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ReviewQueueEvent:
        _validate_reason_code(self.action, self.reason_code)

        if self.action is ReviewQueueAction.SUBMIT:
            if self.revision != 1 or self.previous_status is not None or self.item is None:
                raise ValueError("submit must be the first event and include its queue item")
            if self.item.queue_item_id != self.queue_item_id:
                raise ValueError("submitted item must match the event queue_item_id")
            if self.new_status is not ReviewQueueStatus.PENDING:
                raise ValueError("submit status must match the extraction disposition")
            if self.approval_scope is not None or self.approved_bundle is not None:
                raise ValueError("submit cannot contain approval output")
        else:
            if self.previous_status is None or self.item is not None:
                raise ValueError("transition events require a previous status and no queue item")

        if self.action in {
            ReviewQueueAction.APPROVE_EVIDENCE,
            ReviewQueueAction.APPROVE_RULES,
        }:
            if self.new_status is not ReviewQueueStatus.APPROVED:
                raise ValueError("approval actions must enter APPROVED")
            if self.approval_scope is None or self.approved_bundle is None:
                raise ValueError("approval actions require a scope and approved bundle")
            if self.approved_bundle.scope is not self.approval_scope:
                raise ValueError("approval scope must match the approved bundle")
            expected_scope = (
                ApprovalScope.EVIDENCE_ONLY
                if self.action is ReviewQueueAction.APPROVE_EVIDENCE
                else ApprovalScope.EVIDENCE_AND_RULES
            )
            if self.approval_scope is not expected_scope:
                raise ValueError("approval action does not match its scope")
        elif self.approval_scope is not None or self.approved_bundle is not None:
            raise ValueError("non-approval events cannot contain approval output")

        if self.action is ReviewQueueAction.CLAIM:
            if self.lease_expires_at is None or self.lease_expires_at <= self.occurred_at:
                raise ValueError("claim requires a future lease expiry")
            if self.new_status is not ReviewQueueStatus.IN_REVIEW:
                raise ValueError("claim must enter IN_REVIEW")
        elif self.lease_expires_at is not None:
            raise ValueError("only claim events may include a lease expiry")
        if (
            self.action is ReviewQueueAction.RELEASE
            and self.new_status is not ReviewQueueStatus.PENDING
        ):
            raise ValueError("release must enter PENDING")
        if (
            self.action is ReviewQueueAction.REJECT
            and self.new_status is not ReviewQueueStatus.REJECTED
        ):
            raise ValueError("reject must enter REJECTED")

        expected_request_hash = _request_hash(
            self.queue_item_id, self.action, self.actor, self.reason_code
        )
        if self.request_hash != expected_request_hash:
            raise ValueError("request_hash does not match event request content")

        expected_id = _event_id(
            queue_item_id=self.queue_item_id,
            revision=self.revision,
            action=self.action,
            actor=self.actor,
            reason_code=self.reason_code,
            occurred_at=self.occurred_at,
            previous_status=self.previous_status,
            new_status=self.new_status,
            idempotency_key_hash=self.idempotency_key_hash,
            request_hash=self.request_hash,
            lease_expires_at=self.lease_expires_at,
            item=self.item,
            approval_scope=self.approval_scope,
            approved_bundle=self.approved_bundle,
        )
        if self.event_id != expected_id:
            raise ValueError("event_id does not match event content")
        return self


class ReviewQueueSnapshot(ReviewQueueModel):
    """Current state reconstructed from a validated append-only event stream."""

    snapshot_id: str = Field(min_length=1, max_length=64)
    item: ReviewQueueItem
    status: ReviewQueueStatus
    revision: int = Field(gt=0)
    assignee_id: str | None = Field(default=None, min_length=1, max_length=128)
    claim_expires_at: AwareDateTime | None = None
    approval_scope: ApprovalScope | None = None
    approved_bundle: ApprovedEvidenceBundle | None = None
    decision_reason_code: ReviewReasonCode | None = None
    last_event_id: str = Field(min_length=1, max_length=64)


class ReviewQueueRepository(Protocol):
    """Minimal durable event repository.

    ``append_event`` must atomically enforce both ``expected_revision`` and global uniqueness of
    ``event.idempotency_key_hash``.  It must never replace or delete an existing event.
    """

    def list_events(self, queue_item_id: str) -> Sequence[ReviewQueueEvent]: ...

    def append_event(
        self,
        event: ReviewQueueEvent,
        *,
        expected_revision: int,
    ) -> ReviewQueueEvent: ...


class ReviewQueueError(ValueError):
    """Base error for rejected review-queue operations."""


class ReviewQueueNotFoundError(ReviewQueueError):
    pass


class ReviewQueueConflictError(ReviewQueueError):
    pass


class ReviewQueueAuthorizationError(ReviewQueueError):
    pass


class ReviewQueueTransitionError(ReviewQueueError):
    pass


class ReviewQueueService:
    """Apply RBAC and state transitions over an injected append-only repository."""

    def __init__(
        self,
        repository: ReviewQueueRepository,
        *,
        clock: Callable[[], datetime],
        claim_lease: timedelta = timedelta(minutes=15),
    ) -> None:
        if claim_lease <= timedelta(0):
            raise ValueError("claim_lease must be positive")
        self._repository = repository
        self._clock = clock
        self._claim_lease = claim_lease

    def submit(
        self,
        result: EvidenceExtractionResult,
        *,
        actor: ReviewActor,
        reason_code: ReviewReasonCode,
        idempotency_key: str,
    ) -> ReviewQueueSnapshot:
        _authorize(actor, ReviewQueueAction.SUBMIT)
        occurred_at = self._now()
        _validate_reason_code(ReviewQueueAction.SUBMIT, reason_code)
        if (
            result.disposition is not ExtractionDisposition.REVIEW_REQUIRED
            or result.evidence is None
        ):
            raise ReviewQueueTransitionError(
                "only review-required extraction results with evidence may be submitted"
            )
        if result.evidence is not None and occurred_at < result.evidence.retrieved_at:
            raise ReviewQueueTransitionError("submission cannot precede evidence retrieval")

        item = _make_item(result, actor=actor, submitted_at=occurred_at)
        existing = tuple(self._repository.list_events(item.queue_item_id))
        if existing:
            snapshot = replay_review_events(existing)
            request_hash = _request_hash(
                item.queue_item_id, ReviewQueueAction.SUBMIT, actor, reason_code
            )
            return _idempotent_snapshot(
                snapshot, existing, idempotency_key=idempotency_key, request_hash=request_hash
            )

        request_hash = _request_hash(
            item.queue_item_id, ReviewQueueAction.SUBMIT, actor, reason_code
        )

        event = _make_event(
            queue_item_id=item.queue_item_id,
            revision=1,
            action=ReviewQueueAction.SUBMIT,
            actor=actor,
            reason_code=reason_code,
            occurred_at=occurred_at,
            previous_status=None,
            new_status=ReviewQueueStatus.PENDING,
            idempotency_key_hash=_idempotency_hash(idempotency_key),
            request_hash=request_hash,
            item=item,
        )
        stored_event = self._repository.append_event(event, expected_revision=0)
        if stored_event != event:
            return self.get(item.queue_item_id)
        return replay_review_events((stored_event,))

    def get(self, queue_item_id: str) -> ReviewQueueSnapshot:
        events = tuple(self._repository.list_events(queue_item_id))
        if not events:
            raise ReviewQueueNotFoundError("review queue item was not found")
        return replay_review_events(events)

    def act(
        self,
        queue_item_id: str,
        action: ReviewQueueAction,
        *,
        actor: ReviewActor,
        reason_code: ReviewReasonCode,
        idempotency_key: str,
    ) -> ReviewQueueSnapshot:
        if action is ReviewQueueAction.SUBMIT:
            raise ReviewQueueTransitionError("use submit() for SUBMIT actions")
        _authorize(actor, action)
        occurred_at = self._now()
        _validate_reason_code(action, reason_code)
        existing = tuple(self._repository.list_events(queue_item_id))
        if not existing:
            raise ReviewQueueNotFoundError("review queue item was not found")
        snapshot = replay_review_events(existing)
        request_hash = _request_hash(queue_item_id, action, actor, reason_code)
        repeated = _find_idempotent_event(
            existing, idempotency_key=idempotency_key, request_hash=request_hash
        )
        if repeated is not None:
            return snapshot
        if occurred_at < snapshot.item.submitted_at:
            raise ReviewQueueTransitionError("transition cannot precede submission")

        new_status, approval_scope, bundle = _transition(
            snapshot,
            action=action,
            actor=actor,
            occurred_at=occurred_at,
            reason_code=reason_code,
        )
        lease_expires_at = (
            occurred_at + self._claim_lease if action is ReviewQueueAction.CLAIM else None
        )
        event = _make_event(
            queue_item_id=queue_item_id,
            revision=snapshot.revision + 1,
            action=action,
            actor=actor,
            reason_code=reason_code,
            occurred_at=occurred_at,
            previous_status=snapshot.status,
            new_status=new_status,
            idempotency_key_hash=_idempotency_hash(idempotency_key),
            request_hash=request_hash,
            lease_expires_at=lease_expires_at,
            approval_scope=approval_scope,
            approved_bundle=bundle,
        )
        stored_event = self._repository.append_event(
            event,
            expected_revision=snapshot.revision,
        )
        if stored_event != event:
            return self.get(queue_item_id)
        return replay_review_events((*existing, stored_event))

    def _now(self) -> datetime:
        return _require_timezone(self._clock())


def replay_review_events(events: Sequence[ReviewQueueEvent]) -> ReviewQueueSnapshot:
    """Validate and replay a complete queue-item event stream."""

    if not events:
        raise ReviewQueueNotFoundError("review queue event stream is empty")
    ordered = tuple(events)
    first = ordered[0]
    if first.action is not ReviewQueueAction.SUBMIT or first.item is None:
        raise ReviewQueueConflictError("event stream must begin with SUBMIT")
    queue_item_id = first.queue_item_id
    item = first.item
    status = first.new_status
    assignee_id: str | None = None
    claim_expires_at: datetime | None = None
    approval_scope: ApprovalScope | None = None
    approved_bundle: ApprovedEvidenceBundle | None = None
    decision_reason_code: ReviewReasonCode | None = None

    previous_event_time = first.occurred_at
    if first.revision != 1:
        raise ReviewQueueConflictError("event revisions must begin at one")
    if first.occurred_at != item.submitted_at or first.actor.actor_id != item.submitted_by:
        raise ReviewQueueConflictError("submit event does not match its queue item provenance")
    _authorize(first.actor, ReviewQueueAction.SUBMIT)

    used_idempotency_hashes = {first.idempotency_key_hash}

    for expected_revision, event in enumerate(ordered[1:], start=2):
        if event.queue_item_id != queue_item_id:
            raise ReviewQueueConflictError("event stream mixes queue item identities")
        if event.revision != expected_revision:
            raise ReviewQueueConflictError("event revisions must be contiguous")
        if event.previous_status is not status:
            raise ReviewQueueConflictError("event previous_status does not match replay state")
        if event.occurred_at < previous_event_time:
            raise ReviewQueueConflictError("event timestamps must be monotonic")
        if event.idempotency_key_hash in used_idempotency_hashes:
            raise ReviewQueueConflictError("event stream reuses an idempotency key")
        used_idempotency_hashes.add(event.idempotency_key_hash)
        _authorize(event.actor, event.action)
        if event.actor.actor_id == item.submitted_by:
            raise ReviewQueueConflictError("submitter cannot participate in review transitions")

        if event.action is ReviewQueueAction.CLAIM:
            if status not in {ReviewQueueStatus.PENDING, ReviewQueueStatus.IN_REVIEW}:
                raise ReviewQueueConflictError(
                    "only pending or expired in-review items may be claimed"
                )
            if status is ReviewQueueStatus.IN_REVIEW and (
                claim_expires_at is None or event.occurred_at < claim_expires_at
            ):
                raise ReviewQueueConflictError("an active claim cannot be replaced")
            if (
                status is ReviewQueueStatus.IN_REVIEW
                and event.reason_code is not ReviewReasonCode.LEASE_EXPIRED_RECLAIM
            ):
                raise ReviewQueueConflictError("expired claim replacement lacks its reason code")
            if (
                status is ReviewQueueStatus.PENDING
                and event.reason_code is not ReviewReasonCode.REVIEW_STARTED
            ):
                raise ReviewQueueConflictError("new claim lacks its reason code")
            assignee_id = event.actor.actor_id
            claim_expires_at = event.lease_expires_at
        elif event.action is ReviewQueueAction.RELEASE:
            if status is not ReviewQueueStatus.IN_REVIEW:
                raise ReviewQueueConflictError("only in-review items may be released")
            if event.actor.actor_id != assignee_id:
                raise ReviewQueueConflictError("only the current claimant may release an item")
            assignee_id = None
            claim_expires_at = None
        elif event.action in {
            ReviewQueueAction.APPROVE_EVIDENCE,
            ReviewQueueAction.APPROVE_RULES,
        }:
            if status is not ReviewQueueStatus.IN_REVIEW:
                raise ReviewQueueConflictError("only in-review items may be approved")
            if event.actor.actor_id != assignee_id:
                raise ReviewQueueConflictError("only the current claimant may decide an item")
            if claim_expires_at is None or event.occurred_at >= claim_expires_at:
                raise ReviewQueueConflictError("decision used an expired claim")
            if event.approval_scope is None:
                raise ReviewQueueConflictError("approval event is missing its scope")
            expected_bundle = approve_extraction(
                item.extraction_result,
                reviewer_id=event.actor.actor_id,
                reviewed_at=event.occurred_at,
                scope=event.approval_scope,
            )
            if event.approved_bundle != expected_bundle:
                raise ReviewQueueConflictError("approved bundle does not match its review event")
            approval_scope = event.approval_scope
            approved_bundle = event.approved_bundle
            decision_reason_code = event.reason_code
        elif event.action is ReviewQueueAction.REJECT:
            if status is not ReviewQueueStatus.IN_REVIEW:
                raise ReviewQueueConflictError("only in-review items may be rejected")
            if event.actor.actor_id != assignee_id:
                raise ReviewQueueConflictError("only the current claimant may decide an item")
            if claim_expires_at is None or event.occurred_at >= claim_expires_at:
                raise ReviewQueueConflictError("decision used an expired claim")
            decision_reason_code = event.reason_code
        else:
            raise ReviewQueueConflictError("SUBMIT may appear only as the first event")

        status = event.new_status
        previous_event_time = event.occurred_at

    snapshot_payload = {
        "queue_item_id": queue_item_id,
        "status": status.value,
        "revision": ordered[-1].revision,
        "assignee_id": assignee_id,
        "claim_expires_at": (
            claim_expires_at.isoformat() if claim_expires_at is not None else None
        ),
        "approval_scope": approval_scope.value if approval_scope is not None else None,
        "approval_id": approved_bundle.approval_id if approved_bundle is not None else None,
        "decision_reason_code": (
            decision_reason_code.value if decision_reason_code is not None else None
        ),
        "last_event_id": ordered[-1].event_id,
    }
    return ReviewQueueSnapshot(
        snapshot_id=_stable_id("review_snapshot_", snapshot_payload),
        item=item,
        status=status,
        revision=ordered[-1].revision,
        assignee_id=assignee_id,
        claim_expires_at=claim_expires_at,
        approval_scope=approval_scope,
        approved_bundle=approved_bundle,
        decision_reason_code=decision_reason_code,
        last_event_id=ordered[-1].event_id,
    )


def _transition(
    snapshot: ReviewQueueSnapshot,
    *,
    action: ReviewQueueAction,
    actor: ReviewActor,
    occurred_at: datetime,
    reason_code: ReviewReasonCode,
) -> tuple[ReviewQueueStatus, ApprovalScope | None, ApprovedEvidenceBundle | None]:
    status = snapshot.status
    if status in {ReviewQueueStatus.APPROVED, ReviewQueueStatus.REJECTED}:
        raise ReviewQueueTransitionError("approved and rejected items are terminal")
    if actor.actor_id == snapshot.item.submitted_by:
        raise ReviewQueueAuthorizationError("submitters cannot review their own extraction")

    if action is ReviewQueueAction.CLAIM:
        if status is ReviewQueueStatus.IN_REVIEW:
            if snapshot.claim_expires_at is None or occurred_at < snapshot.claim_expires_at:
                raise ReviewQueueTransitionError("the current claim lease has not expired")
            if reason_code is not ReviewReasonCode.LEASE_EXPIRED_RECLAIM:
                raise ReviewQueueTransitionError("expired claims require LEASE_EXPIRED_RECLAIM")
        elif status is not ReviewQueueStatus.PENDING:
            raise ReviewQueueTransitionError(
                "only pending or expired in-review items may be claimed"
            )
        elif reason_code is not ReviewReasonCode.REVIEW_STARTED:
            raise ReviewQueueTransitionError("new claims require REVIEW_STARTED")
        return ReviewQueueStatus.IN_REVIEW, None, None
    if action is ReviewQueueAction.RELEASE:
        if status is not ReviewQueueStatus.IN_REVIEW:
            raise ReviewQueueTransitionError("only in-review items may be released")
        if snapshot.assignee_id != actor.actor_id:
            raise ReviewQueueAuthorizationError("only the current claimant may release an item")
        return ReviewQueueStatus.PENDING, None, None
    if action is ReviewQueueAction.REJECT:
        _require_current_claim(snapshot, actor, occurred_at)
        return ReviewQueueStatus.REJECTED, None, None
    if action is ReviewQueueAction.APPROVE_EVIDENCE:
        _require_current_claim(snapshot, actor, occurred_at)
        scope = ApprovalScope.EVIDENCE_ONLY
    elif action is ReviewQueueAction.APPROVE_RULES:
        _require_current_claim(snapshot, actor, occurred_at)
        if snapshot.item.extraction_result.service_kind is not EvidenceServiceKind.REGULATION:
            raise ReviewQueueTransitionError("only regulation extraction can approve rules")
        scope = ApprovalScope.EVIDENCE_AND_RULES
    else:
        raise ReviewQueueTransitionError("unsupported review action")

    try:
        bundle = approve_extraction(
            snapshot.item.extraction_result,
            reviewer_id=actor.actor_id,
            reviewed_at=occurred_at,
            scope=scope,
        )
    except ValueError as error:
        raise ReviewQueueTransitionError(str(error)) from error
    return ReviewQueueStatus.APPROVED, scope, bundle


def _require_current_claim(
    snapshot: ReviewQueueSnapshot,
    actor: ReviewActor,
    occurred_at: datetime,
) -> None:
    if snapshot.status is not ReviewQueueStatus.IN_REVIEW:
        raise ReviewQueueTransitionError("item must be claimed before a decision")
    if snapshot.assignee_id != actor.actor_id:
        raise ReviewQueueAuthorizationError("only the current claimant may decide an item")
    if snapshot.claim_expires_at is None or occurred_at >= snapshot.claim_expires_at:
        raise ReviewQueueTransitionError("claim lease has expired")


def _authorize(actor: ReviewActor, action: ReviewQueueAction) -> None:
    allowed: dict[ReviewQueueAction, frozenset[ReviewRole]] = {
        ReviewQueueAction.SUBMIT: frozenset({ReviewRole.SUBMITTER}),
        ReviewQueueAction.CLAIM: frozenset(
            {
                ReviewRole.EVIDENCE_REVIEWER,
                ReviewRole.REGULATION_PUBLISHER,
            }
        ),
        ReviewQueueAction.RELEASE: frozenset(
            {
                ReviewRole.EVIDENCE_REVIEWER,
                ReviewRole.REGULATION_PUBLISHER,
            }
        ),
        ReviewQueueAction.APPROVE_EVIDENCE: frozenset({ReviewRole.EVIDENCE_REVIEWER}),
        ReviewQueueAction.APPROVE_RULES: frozenset({ReviewRole.REGULATION_PUBLISHER}),
        ReviewQueueAction.REJECT: frozenset({ReviewRole.EVIDENCE_REVIEWER}),
    }
    if not allowed[action].intersection(actor.roles):
        raise ReviewQueueAuthorizationError(f"actor is not authorized for {action.value}")


def _make_item(
    result: EvidenceExtractionResult,
    *,
    actor: ReviewActor,
    submitted_at: datetime,
) -> ReviewQueueItem:
    identity = {
        "schema_version": "review-queue-item-v1",
        "extraction_result": result.model_dump(mode="json"),
    }
    return ReviewQueueItem(
        queue_item_id=_stable_id("review_item_", identity),
        extraction_result_id=result.result_id,
        extraction_snapshot_hash=review_extraction_snapshot_hash(result),
        extraction_result=result,
        submitted_by=actor.actor_id,
        submitted_at=submitted_at,
    )


def _make_event(
    *,
    queue_item_id: str,
    revision: int,
    action: ReviewQueueAction,
    actor: ReviewActor,
    reason_code: ReviewReasonCode,
    occurred_at: datetime,
    previous_status: ReviewQueueStatus | None,
    new_status: ReviewQueueStatus,
    idempotency_key_hash: str,
    request_hash: str,
    lease_expires_at: datetime | None = None,
    item: ReviewQueueItem | None = None,
    approval_scope: ApprovalScope | None = None,
    approved_bundle: ApprovedEvidenceBundle | None = None,
) -> ReviewQueueEvent:
    event_id = _event_id(
        queue_item_id=queue_item_id,
        revision=revision,
        action=action,
        actor=actor,
        reason_code=reason_code,
        occurred_at=occurred_at,
        previous_status=previous_status,
        new_status=new_status,
        idempotency_key_hash=idempotency_key_hash,
        request_hash=request_hash,
        lease_expires_at=lease_expires_at,
        item=item,
        approval_scope=approval_scope,
        approved_bundle=approved_bundle,
    )
    return ReviewQueueEvent(
        event_id=event_id,
        queue_item_id=queue_item_id,
        revision=revision,
        action=action,
        actor=actor,
        reason_code=reason_code,
        occurred_at=occurred_at,
        previous_status=previous_status,
        new_status=new_status,
        idempotency_key_hash=idempotency_key_hash,
        request_hash=request_hash,
        lease_expires_at=lease_expires_at,
        item=item,
        approval_scope=approval_scope,
        approved_bundle=approved_bundle,
    )


def _event_id(
    *,
    queue_item_id: str,
    revision: int,
    action: ReviewQueueAction,
    actor: ReviewActor,
    reason_code: ReviewReasonCode,
    occurred_at: datetime,
    previous_status: ReviewQueueStatus | None,
    new_status: ReviewQueueStatus,
    idempotency_key_hash: str,
    request_hash: str,
    lease_expires_at: datetime | None,
    item: ReviewQueueItem | None,
    approval_scope: ApprovalScope | None,
    approved_bundle: ApprovedEvidenceBundle | None,
) -> str:
    payload = {
        "schema_version": "review-queue-event-v1",
        "queue_item_id": queue_item_id,
        "revision": revision,
        "action": action.value,
        "actor": actor.model_dump(mode="json"),
        "reason_code": reason_code.value,
        "occurred_at": occurred_at.isoformat(),
        "previous_status": previous_status.value if previous_status is not None else None,
        "new_status": new_status.value,
        "idempotency_key_hash": idempotency_key_hash,
        "request_hash": request_hash,
        "lease_expires_at": (
            lease_expires_at.isoformat() if lease_expires_at is not None else None
        ),
        "item": item.model_dump(mode="json") if item is not None else None,
        "approval_scope": approval_scope.value if approval_scope is not None else None,
        "approved_bundle": (
            approved_bundle.model_dump(mode="json") if approved_bundle is not None else None
        ),
    }
    return _stable_id("review_event_", payload)


def review_extraction_snapshot_hash(result: EvidenceExtractionResult) -> str:
    """Return the canonical digest stored beside a review proposal snapshot."""

    return hashlib.sha256(
        _canonical_json(result.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def review_audit_event_hash(
    event: ReviewQueueEvent,
    *,
    prior_event_hash: str | None,
) -> str:
    """Hash one semantic audit event into its append-only per-item chain."""

    payload = {
        "schema_version": "review-audit-chain-v1",
        "prior_event_hash": prior_event_hash,
        "event": event.model_dump(mode="json"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_reason_code(
    action: ReviewQueueAction,
    reason_code: ReviewReasonCode,
) -> None:
    allowed: dict[ReviewQueueAction, frozenset[ReviewReasonCode]] = {
        ReviewQueueAction.SUBMIT: frozenset({ReviewReasonCode.EXTRACTION_READY}),
        ReviewQueueAction.CLAIM: frozenset(
            {ReviewReasonCode.REVIEW_STARTED, ReviewReasonCode.LEASE_EXPIRED_RECLAIM}
        ),
        ReviewQueueAction.RELEASE: frozenset({ReviewReasonCode.REVIEW_RELEASED}),
        ReviewQueueAction.APPROVE_EVIDENCE: frozenset({ReviewReasonCode.EVIDENCE_VERIFIED}),
        ReviewQueueAction.APPROVE_RULES: frozenset({ReviewReasonCode.REGULATION_VERIFIED}),
        ReviewQueueAction.REJECT: frozenset(
            {
                ReviewReasonCode.SOURCE_MISMATCH,
                ReviewReasonCode.INVALID_EXTRACTION,
                ReviewReasonCode.INSUFFICIENT_EVIDENCE,
            }
        ),
    }
    if reason_code not in allowed[action]:
        raise ReviewQueueTransitionError(f"{reason_code.value} is not valid for {action.value}")


def _idempotency_hash(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise ReviewQueueTransitionError(
            "idempotency_key must be nonblank and at most 255 characters"
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _request_hash(
    queue_item_id: str,
    action: ReviewQueueAction,
    actor: ReviewActor,
    reason_code: ReviewReasonCode,
) -> str:
    payload = {
        "schema_version": "review-queue-request-v1",
        "queue_item_id": queue_item_id,
        "action": action.value,
        "actor": actor.model_dump(mode="json"),
        "reason_code": reason_code.value,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _find_idempotent_event(
    events: Sequence[ReviewQueueEvent],
    *,
    idempotency_key: str,
    request_hash: str,
) -> ReviewQueueEvent | None:
    key_hash = _idempotency_hash(idempotency_key)
    for event in events:
        if event.idempotency_key_hash != key_hash:
            continue
        if event.request_hash != request_hash:
            raise ReviewQueueConflictError(
                "idempotency key was already used for a different review request"
            )
        return event
    return None


def _idempotent_snapshot(
    snapshot: ReviewQueueSnapshot,
    events: Sequence[ReviewQueueEvent],
    *,
    idempotency_key: str,
    request_hash: str,
) -> ReviewQueueSnapshot:
    if (
        _find_idempotent_event(events, idempotency_key=idempotency_key, request_hash=request_hash)
        is None
    ):
        raise ReviewQueueConflictError("extraction result is already queued")
    return snapshot


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_id(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:32]}"
