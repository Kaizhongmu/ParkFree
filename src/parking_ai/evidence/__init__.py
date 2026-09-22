"""Approved evidence persistence boundary."""

from parking_ai.evidence.persistence import (
    EvidencePersistenceConflictError,
    persist_approved_evidence,
    validate_persisted_approved_evidence,
)
from parking_ai.evidence.review_persistence import SQLAlchemyReviewQueueRepository
from parking_ai.evidence.review_queue import (
    ReviewActor,
    ReviewQueueAction,
    ReviewQueueAuthorizationError,
    ReviewQueueConflictError,
    ReviewQueueError,
    ReviewQueueNotFoundError,
    ReviewQueueService,
    ReviewQueueStatus,
    ReviewQueueTransitionError,
    ReviewReasonCode,
    ReviewRole,
)

__all__ = [
    "EvidencePersistenceConflictError",
    "ReviewActor",
    "ReviewQueueAction",
    "ReviewQueueAuthorizationError",
    "ReviewQueueConflictError",
    "ReviewQueueError",
    "ReviewQueueNotFoundError",
    "ReviewQueueService",
    "ReviewQueueStatus",
    "ReviewQueueTransitionError",
    "ReviewReasonCode",
    "ReviewRole",
    "SQLAlchemyReviewQueueRepository",
    "persist_approved_evidence",
    "validate_persisted_approved_evidence",
]
