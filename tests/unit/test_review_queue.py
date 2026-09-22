from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from parking_ai.agents.evidence_services import (
    ApprovalScope,
    CommunityEvidenceService,
    EvidenceExtractionResult,
    EvidenceServiceKind,
    ExtractionDisposition,
    ExtractionRequest,
    RegulationEvidenceService,
    ReviewReason,
    SourceMaterial,
)
from parking_ai.domain.enums import (
    EvidenceSourceType,
    EvidenceStoragePolicy,
)
from parking_ai.evidence.review_queue import (
    ReviewActor,
    ReviewQueueAction,
    ReviewQueueAuthorizationError,
    ReviewQueueConflictError,
    ReviewQueueEvent,
    ReviewQueueService,
    ReviewQueueStatus,
    ReviewQueueTransitionError,
    ReviewReasonCode,
    ReviewRole,
    replay_review_events,
)

NOW = datetime(2026, 9, 21, 15, tzinfo=UTC)


class FixtureAdapter:
    extractor_version = "fixture-v1"

    def __init__(self, kind: EvidenceServiceKind) -> None:
        self.kind = kind

    def extract(self, request: ExtractionRequest) -> object:
        del request
        if self.kind is EvidenceServiceKind.REGULATION:
            return {
                "schema_version": "phase8-regulation-response-v1",
                "extractor_version": self.extractor_version,
                "claims": [
                    {
                        "rule_type": "NO_PARKING",
                        "extraction_confidence": 0.95,
                    }
                ],
            }
        return {
            "schema_version": "phase8-community-response-v1",
            "extractor_version": self.extractor_version,
            "claims": [
                {
                    "claim_type": "PARKING_RESTRICTION",
                    "location_text": "fixture segment",
                    "statement": "A restriction was reported; verify it.",
                    "extraction_confidence": 0.9,
                }
            ],
        }


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class MemoryEventRepository:
    def __init__(self) -> None:
        self.events: dict[str, list[ReviewQueueEvent]] = {}
        self.idempotency_hashes: set[str] = set()

    def list_events(self, queue_item_id: str) -> tuple[ReviewQueueEvent, ...]:
        return tuple(self.events.get(queue_item_id, ()))

    def append_event(
        self,
        event: ReviewQueueEvent,
        *,
        expected_revision: int,
    ) -> ReviewQueueEvent:
        stream = self.events.setdefault(event.queue_item_id, [])
        if len(stream) != expected_revision:
            raise ReviewQueueConflictError("stale revision")
        if event.idempotency_key_hash in self.idempotency_hashes:
            raise ReviewQueueConflictError("duplicate idempotency key")
        stream.append(event)
        self.idempotency_hashes.add(event.idempotency_key_hash)
        return event


def actor(actor_id: str, *roles: ReviewRole) -> ReviewActor:
    return ReviewActor(actor_id=actor_id, roles=roles)


def extraction_result(
    *,
    kind: EvidenceServiceKind = EvidenceServiceKind.REGULATION,
    disposition: ExtractionDisposition = ExtractionDisposition.REVIEW_REQUIRED,
) -> EvidenceExtractionResult:
    if disposition is ExtractionDisposition.QUARANTINED:
        return EvidenceExtractionResult(
            result_id="extract-quarantined",
            service_kind=kind,
            disposition=disposition,
            extractor_version="fixture-v1",
            review_reasons=[ReviewReason.INVALID_EXTRACTOR_OUTPUT],
            validation_errors=["claims.0:missing"],
        )

    source = SourceMaterial(
        source_type=(
            EvidenceSourceType.OFFICIAL_CODE
            if kind is EvidenceServiceKind.REGULATION
            else EvidenceSourceType.COMMUNITY
        ),
        source_uri_or_identifier="fixture://review/source",
        publisher="Fixture Authority",
        retrieved_at=NOW - timedelta(hours=1),
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        segment_ids=["segment-1"],
        content="Fixture source content.",
    )
    result = (
        RegulationEvidenceService(FixtureAdapter(kind)).extract(source)
        if kind is EvidenceServiceKind.REGULATION
        else CommunityEvidenceService(FixtureAdapter(kind)).extract(source)
    )
    if disposition is ExtractionDisposition.REVIEW_REQUIRED:
        return result
    return result.model_copy(update={"disposition": disposition, "review_reasons": []})


def queue(
    result: EvidenceExtractionResult | None = None,
) -> tuple[ReviewQueueService, MemoryEventRepository, MutableClock, str]:
    repository = MemoryEventRepository()
    clock = MutableClock(NOW)
    service = ReviewQueueService(repository, clock=clock, claim_lease=timedelta(minutes=10))
    submitted = service.submit(
        result or extraction_result(),
        actor=actor("ingestion-worker", ReviewRole.SUBMITTER),
        reason_code=ReviewReasonCode.EXTRACTION_READY,
        idempotency_key="submit-command-1",
    )
    return service, repository, clock, submitted.item.queue_item_id


def test_submit_is_strict_deterministic_and_idempotent_without_raw_content() -> None:
    result = extraction_result()
    service, repository, _, item_id = queue(result)
    first = service.get(item_id)
    repeated = service.submit(
        result,
        actor=actor("ingestion-worker", ReviewRole.SUBMITTER),
        reason_code=ReviewReasonCode.EXTRACTION_READY,
        idempotency_key="submit-command-1",
    )

    assert repeated == first
    assert first.status is ReviewQueueStatus.PENDING
    assert first.revision == 1
    assert repository.events[item_id][0].revision == 1
    assert first.item.queue_item_id == item_id
    assert len(first.item.extraction_snapshot_hash) == 64
    assert len(repository.events[item_id]) == 1
    serialized = repository.events[item_id][0].model_dump_json()
    assert "submit-command-1" not in serialized
    assert "source content" not in serialized

    other_repository = MemoryEventRepository()
    other = ReviewQueueService(other_repository, clock=MutableClock(NOW))
    same = other.submit(
        result,
        actor=actor("ingestion-worker", ReviewRole.SUBMITTER),
        reason_code=ReviewReasonCode.EXTRACTION_READY,
        idempotency_key="different-secret-key",
    )
    assert same.item.queue_item_id == item_id
    assert same.item.extraction_snapshot_hash == first.item.extraction_snapshot_hash


def test_evidence_reviewer_must_claim_then_can_approve_evidence() -> None:
    service, repository, _, item_id = queue(extraction_result(kind=EvidenceServiceKind.COMMUNITY))
    reviewer = actor("evidence-reviewer", ReviewRole.EVIDENCE_REVIEWER)

    with pytest.raises(ReviewQueueTransitionError, match="claimed"):
        service.act(
            item_id,
            ReviewQueueAction.APPROVE_EVIDENCE,
            actor=reviewer,
            reason_code=ReviewReasonCode.EVIDENCE_VERIFIED,
            idempotency_key="approve-before-claim",
        )

    claimed = service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=reviewer,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="claim-evidence-1",
    )
    approved = service.act(
        item_id,
        ReviewQueueAction.APPROVE_EVIDENCE,
        actor=reviewer,
        reason_code=ReviewReasonCode.EVIDENCE_VERIFIED,
        idempotency_key="approve-evidence-1",
    )

    assert claimed.status is ReviewQueueStatus.IN_REVIEW
    assert claimed.revision == 2
    assert claimed.assignee_id == reviewer.actor_id
    assert claimed.claim_expires_at == NOW + timedelta(minutes=10)
    assert approved.status is ReviewQueueStatus.APPROVED
    assert approved.revision == 3
    assert approved.approval_scope is ApprovalScope.EVIDENCE_ONLY
    assert approved.approved_bundle is not None
    assert approved.approved_bundle.rules == []
    assert approved.decision_reason_code is ReviewReasonCode.EVIDENCE_VERIFIED
    assert [event.action for event in repository.events[item_id]] == [
        ReviewQueueAction.SUBMIT,
        ReviewQueueAction.CLAIM,
        ReviewQueueAction.APPROVE_EVIDENCE,
    ]

    repeated = service.act(
        item_id,
        ReviewQueueAction.APPROVE_EVIDENCE,
        actor=reviewer,
        reason_code=ReviewReasonCode.EVIDENCE_VERIFIED,
        idempotency_key="approve-evidence-1",
    )
    assert repeated == approved
    assert len(repository.events[item_id]) == 3


def test_only_regulation_publisher_can_publish_regulation_rules() -> None:
    service, _, _, item_id = queue()
    evidence_reviewer = actor("evidence-reviewer", ReviewRole.EVIDENCE_REVIEWER)
    publisher = actor("regulation-publisher", ReviewRole.REGULATION_PUBLISHER)

    with pytest.raises(ReviewQueueAuthorizationError, match="not authorized"):
        service.act(
            item_id,
            ReviewQueueAction.APPROVE_RULES,
            actor=evidence_reviewer,
            reason_code=ReviewReasonCode.REGULATION_VERIFIED,
            idempotency_key="unauthorized-publication",
        )

    service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=publisher,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="claim-regulation-1",
    )
    approved = service.act(
        item_id,
        ReviewQueueAction.APPROVE_RULES,
        actor=publisher,
        reason_code=ReviewReasonCode.REGULATION_VERIFIED,
        idempotency_key="publish-regulation-1",
    )

    assert approved.approval_scope is ApprovalScope.EVIDENCE_AND_RULES
    assert approved.approved_bundle is not None
    assert len(approved.approved_bundle.rules) == 1
    assert approved.approved_bundle.reviewer_id == publisher.actor_id
    assert approved.approved_bundle.reviewed_at == NOW


def test_rule_approval_fails_closed_for_non_regulation_extraction() -> None:
    result = extraction_result(kind=EvidenceServiceKind.COMMUNITY)
    service, _, _, item_id = queue(result)
    publisher = actor("regulation-publisher", ReviewRole.REGULATION_PUBLISHER)
    service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=publisher,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="claim-community-as-publisher",
    )

    with pytest.raises(ReviewQueueTransitionError, match="only regulation extraction"):
        service.act(
            item_id,
            ReviewQueueAction.APPROVE_RULES,
            actor=publisher,
            reason_code=ReviewReasonCode.REGULATION_VERIFIED,
            idempotency_key="publish-community",
        )


def test_submitter_cannot_review_own_work_even_with_a_reviewer_role() -> None:
    repository = MemoryEventRepository()
    clock = MutableClock(NOW)
    service = ReviewQueueService(repository, clock=clock)
    mixed_actor = actor(
        "same-person",
        ReviewRole.SUBMITTER,
        ReviewRole.EVIDENCE_REVIEWER,
    )
    submitted = service.submit(
        extraction_result(),
        actor=mixed_actor,
        reason_code=ReviewReasonCode.EXTRACTION_READY,
        idempotency_key="self-submit",
    )

    with pytest.raises(ReviewQueueAuthorizationError, match="own extraction"):
        service.act(
            submitted.item.queue_item_id,
            ReviewQueueAction.CLAIM,
            actor=mixed_actor,
            reason_code=ReviewReasonCode.REVIEW_STARTED,
            idempotency_key="self-claim",
        )


def test_claim_lease_enforces_current_reviewer_and_allows_audited_reclaim() -> None:
    service, repository, clock, item_id = queue()
    first = actor("publisher-1", ReviewRole.REGULATION_PUBLISHER)
    second = actor("publisher-2", ReviewRole.REGULATION_PUBLISHER)
    service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=first,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="first-claim",
    )

    with pytest.raises(ReviewQueueTransitionError, match="not expired"):
        service.act(
            item_id,
            ReviewQueueAction.CLAIM,
            actor=second,
            reason_code=ReviewReasonCode.LEASE_EXPIRED_RECLAIM,
            idempotency_key="premature-reclaim",
        )
    with pytest.raises(ReviewQueueAuthorizationError, match="current claimant"):
        service.act(
            item_id,
            ReviewQueueAction.APPROVE_RULES,
            actor=second,
            reason_code=ReviewReasonCode.REGULATION_VERIFIED,
            idempotency_key="wrong-reviewer",
        )

    clock.advance(timedelta(minutes=10))
    reclaimed = service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=second,
        reason_code=ReviewReasonCode.LEASE_EXPIRED_RECLAIM,
        idempotency_key="second-claim",
    )
    approved = service.act(
        item_id,
        ReviewQueueAction.APPROVE_RULES,
        actor=second,
        reason_code=ReviewReasonCode.REGULATION_VERIFIED,
        idempotency_key="second-publish",
    )

    assert reclaimed.assignee_id == second.actor_id
    assert approved.status is ReviewQueueStatus.APPROVED
    assert [event.action for event in repository.events[item_id]].count(
        ReviewQueueAction.CLAIM
    ) == 2


def test_reject_requires_current_claim_and_terminal_items_cannot_change() -> None:
    service, repository, _, item_id = queue()
    reviewer = actor("evidence-reviewer", ReviewRole.EVIDENCE_REVIEWER)
    service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=reviewer,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="reject-claim",
    )
    rejected = service.act(
        item_id,
        ReviewQueueAction.REJECT,
        actor=reviewer,
        reason_code=ReviewReasonCode.SOURCE_MISMATCH,
        idempotency_key="reject-decision",
    )

    assert rejected.status is ReviewQueueStatus.REJECTED
    assert rejected.decision_reason_code is ReviewReasonCode.SOURCE_MISMATCH
    with pytest.raises(ReviewQueueTransitionError, match="terminal"):
        service.act(
            item_id,
            ReviewQueueAction.CLAIM,
            actor=reviewer,
            reason_code=ReviewReasonCode.REVIEW_STARTED,
            idempotency_key="reopen-rejected",
        )
    assert replay_review_events(repository.events[item_id]) == rejected


@pytest.mark.parametrize(
    "disposition",
    [ExtractionDisposition.ACCEPTED, ExtractionDisposition.QUARANTINED],
)
def test_only_review_required_results_are_queueable(
    disposition: ExtractionDisposition,
) -> None:
    repository = MemoryEventRepository()
    service = ReviewQueueService(repository, clock=MutableClock(NOW))

    with pytest.raises(ReviewQueueTransitionError, match="only review-required"):
        service.submit(
            extraction_result(disposition=disposition),
            actor=actor("ingestion-worker", ReviewRole.SUBMITTER),
            reason_code=ReviewReasonCode.EXTRACTION_READY,
            idempotency_key=f"submit-{disposition.value.lower()}",
        )
    assert repository.events == {}


def test_idempotency_key_reuse_with_different_request_fails_closed() -> None:
    service, _, _, item_id = queue()
    reviewer = actor("evidence-reviewer", ReviewRole.EVIDENCE_REVIEWER)
    service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=reviewer,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="shared-key",
    )

    with pytest.raises(ReviewQueueConflictError, match="different review request"):
        service.act(
            item_id,
            ReviewQueueAction.RELEASE,
            actor=reviewer,
            reason_code=ReviewReasonCode.REVIEW_RELEASED,
            idempotency_key="shared-key",
        )


def test_naive_server_clock_is_rejected() -> None:
    service = ReviewQueueService(
        MemoryEventRepository(),
        clock=MutableClock(datetime(2026, 9, 21, 15)),
    )
    with pytest.raises(ValueError, match="timezone"):
        service.submit(
            extraction_result(),
            actor=actor("ingestion-worker", ReviewRole.SUBMITTER),
            reason_code=ReviewReasonCode.EXTRACTION_READY,
            idempotency_key="naive-clock",
        )


def test_non_utc_server_clock_is_canonicalized_for_durable_replay() -> None:
    repository = MemoryEventRepository()
    central_time = NOW.astimezone(timezone(timedelta(hours=-5)))
    service = ReviewQueueService(repository, clock=MutableClock(central_time))

    submitted = service.submit(
        extraction_result(),
        actor=actor("ingestion-worker", ReviewRole.SUBMITTER),
        reason_code=ReviewReasonCode.EXTRACTION_READY,
        idempotency_key="non-utc-clock",
    )

    event = repository.events[submitted.item.queue_item_id][0]
    assert event.occurred_at == NOW
    assert event.occurred_at.tzinfo is UTC
    assert replay_review_events((event,)) == submitted


def test_tampered_content_derived_evidence_identity_is_rejected() -> None:
    result = extraction_result()
    assert result.evidence is not None
    tampered_evidence = result.evidence.model_copy(
        update={"source_uri_or_identifier": "fixture://review/tampered"}
    )
    tampered = result.model_copy(update={"evidence": tampered_evidence})
    service = ReviewQueueService(MemoryEventRepository(), clock=MutableClock(NOW))

    with pytest.raises(ValueError, match="evidence_id"):
        service.submit(
            tampered,
            actor=actor("ingestion-worker", ReviewRole.SUBMITTER),
            reason_code=ReviewReasonCode.EXTRACTION_READY,
            idempotency_key="tampered-result",
        )


def test_concurrent_identical_command_replays_the_winning_event() -> None:
    base_service, base_repository, _, item_id = queue()
    submit_event = base_repository.events[item_id][0]
    reviewer = actor("evidence-reviewer", ReviewRole.EVIDENCE_REVIEWER)

    winner_repository = MemoryEventRepository()
    winner_repository.events[item_id] = [submit_event]
    winner_repository.idempotency_hashes.add(submit_event.idempotency_key_hash)
    winner_service = ReviewQueueService(
        winner_repository,
        clock=MutableClock(NOW + timedelta(minutes=1)),
        claim_lease=timedelta(minutes=10),
    )
    winner_snapshot = winner_service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=reviewer,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="concurrent-claim",
    )
    winner_event = winner_repository.events[item_id][-1]

    class ConcurrentWinnerRepository:
        def __init__(self) -> None:
            self.winner_visible = False

        def list_events(self, queue_item_id: str) -> tuple[ReviewQueueEvent, ...]:
            assert queue_item_id == item_id
            return (submit_event, winner_event) if self.winner_visible else (submit_event,)

        def append_event(
            self,
            event: ReviewQueueEvent,
            *,
            expected_revision: int,
        ) -> ReviewQueueEvent:
            assert expected_revision == 1
            assert event.request_hash == winner_event.request_hash
            assert event.idempotency_key_hash == winner_event.idempotency_key_hash
            assert event.event_id != winner_event.event_id
            self.winner_visible = True
            return winner_event

    losing_service = ReviewQueueService(
        ConcurrentWinnerRepository(),
        clock=MutableClock(NOW + timedelta(minutes=2)),
        claim_lease=timedelta(minutes=10),
    )
    replayed = losing_service.act(
        item_id,
        ReviewQueueAction.CLAIM,
        actor=reviewer,
        reason_code=ReviewReasonCode.REVIEW_STARTED,
        idempotency_key="concurrent-claim",
    )

    assert replayed == winner_snapshot
    assert replayed.claim_expires_at == NOW + timedelta(minutes=11)
    assert base_service.get(item_id).status is ReviewQueueStatus.PENDING
