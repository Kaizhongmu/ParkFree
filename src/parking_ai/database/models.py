from datetime import UTC, date, datetime, time
from typing import Any
from uuid import uuid4

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parking_ai.database.base import Base
from parking_ai.domain.enums import (
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    LegalState,
    ParkingRuleType,
    PhysicalState,
    SearchSessionStatus,
    SegmentSide,
)


def _uuid_str() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _domain_enum(enum_type: type[Any], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        values_callable=lambda values: [item.value for item in values],
        validate_strings=True,
    )


parking_source_segments = Table(
    "parking_source_segments",
    Base.metadata,
    Column(
        "evidence_id",
        String(64),
        ForeignKey("parking_sources.evidence_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "segment_id",
        String(64),
        ForeignKey("street_segments.segment_id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class DestinationModel(Base):
    __tablename__ = "destinations"

    destination_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=False
    )
    destination_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    access_points: Mapped[list["DestinationAccessPointModel"]] = relationship(
        back_populates="destination", cascade="all, delete-orphan"
    )
    search_sessions: Mapped[list["SearchSessionModel"]] = relationship(back_populates="destination")


Index("ix_destinations_geometry_gist", DestinationModel.geometry, postgresql_using="gist")


class DestinationAccessPointModel(Base):
    __tablename__ = "destination_access_points"

    access_point_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.destination_id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255))
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=False
    )

    destination: Mapped[DestinationModel] = relationship(back_populates="access_points")


Index(
    "ix_destination_access_points_geometry_gist",
    DestinationAccessPointModel.geometry,
    postgresql_using="gist",
)


class ParkingSegmentModel(Base):
    __tablename__ = "street_segments"
    __table_args__ = (
        CheckConstraint("length_m > 0", name="length_m_positive"),
        CheckConstraint(
            "estimated_capacity IS NULL OR estimated_capacity >= 0",
            name="estimated_capacity_nonnegative",
        ),
        CheckConstraint(
            "legal_confidence >= 0 AND legal_confidence <= 1",
            name="legal_confidence_probability",
        ),
        CheckConstraint(
            "availability_probability IS NULL OR "
            "(availability_probability >= 0 AND availability_probability <= 1)",
            name="availability_probability_range",
        ),
    )

    segment_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=False
    )
    street_name: Mapped[str | None] = mapped_column(String(255))
    side: Mapped[SegmentSide] = mapped_column(
        _domain_enum(SegmentSide, "segment_side"), nullable=False, default=SegmentSide.UNKNOWN
    )
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_capacity: Mapped[float | None] = mapped_column(Float)
    road_type: Mapped[str | None] = mapped_column(String(64))
    physical_state: Mapped[PhysicalState] = mapped_column(
        _domain_enum(PhysicalState, "physical_state"),
        nullable=False,
        default=PhysicalState.UNKNOWN,
    )
    legal_state: Mapped[LegalState] = mapped_column(
        _domain_enum(LegalState, "legal_state"), nullable=False, default=LegalState.UNKNOWN
    )
    free_state: Mapped[FreeState] = mapped_column(
        _domain_enum(FreeState, "free_state"), nullable=False, default=FreeState.UNKNOWN
    )
    legal_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    availability_probability: Mapped[float | None] = mapped_column(Float)
    availability_interval: Mapped[list[float] | None] = mapped_column(JSONB)
    data_freshness: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    rules: Mapped[list["ParkingRuleModel"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["EvidenceModel"]] = relationship(
        secondary=parking_source_segments, back_populates="segments"
    )
    route_steps: Mapped[list["SearchRouteStepModel"]] = relationship(back_populates="segment")
    outcomes: Mapped[list["ParkingOutcomeModel"]] = relationship(back_populates="segment")


Index("ix_street_segments_geometry_gist", ParkingSegmentModel.geometry, postgresql_using="gist")
Index("ix_street_segments_street_name", ParkingSegmentModel.street_name)


class EvidenceModel(Base):
    __tablename__ = "parking_sources"

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    source_type: Mapped[EvidenceSourceType] = mapped_column(
        _domain_enum(EvidenceSourceType, "evidence_source_type"), nullable=False
    )
    source_uri_or_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_storage_policy: Mapped[EvidenceStoragePolicy] = mapped_column(
        _domain_enum(EvidenceStoragePolicy, "evidence_storage_policy"), nullable=False
    )
    normalized_claims: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    reliability_tier: Mapped[EvidenceReliabilityTier] = mapped_column(
        _domain_enum(EvidenceReliabilityTier, "evidence_reliability_tier"), nullable=False
    )
    extractor_version: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str | None] = mapped_column(String(128), index=True)

    segments: Mapped[list[ParkingSegmentModel]] = relationship(
        secondary=parking_source_segments, back_populates="evidence"
    )
    rules: Mapped[list["ParkingRuleModel"]] = relationship(back_populates="source_evidence")


class ParkingRuleModel(Base):
    __tablename__ = "parking_rules"
    __table_args__ = (
        CheckConstraint(
            "max_duration_min IS NULL OR max_duration_min > 0",
            name="max_duration_positive",
        ),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="extraction_confidence_probability",
        ),
        CheckConstraint(
            "effective_start_date IS NULL OR effective_end_date IS NULL "
            "OR effective_start_date <= effective_end_date",
            name="effective_date_order",
        ),
    )

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("street_segments.segment_id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_type: Mapped[ParkingRuleType] = mapped_column(
        _domain_enum(ParkingRuleType, "parking_rule_type"), nullable=False
    )
    days: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    effective_start_date: Mapped[date | None] = mapped_column(Date)
    effective_end_date: Mapped[date | None] = mapped_column(Date)
    max_duration_min: Mapped[int | None] = mapped_column(Integer)
    payment_required: Mapped[bool | None] = mapped_column(Boolean)
    permit_required: Mapped[bool | None] = mapped_column(Boolean)
    permit_type: Mapped[str | None] = mapped_column(String(128))
    exceptions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_evidence_id: Mapped[str] = mapped_column(
        ForeignKey("parking_sources.evidence_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    segment: Mapped[ParkingSegmentModel] = relationship(back_populates="rules")
    source_evidence: Mapped[EvidenceModel] = relationship(back_populates="rules")


class EvidenceReviewQueueModel(Base):
    """Durable, normalized extraction result awaiting an authorized review decision."""

    __tablename__ = "evidence_review_queue"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED')",
            name="status_value",
        ),
        CheckConstraint(
            "service_kind IN ('REGULATION', 'COMMUNITY', 'VISION')",
            name="service_kind_value",
        ),
        CheckConstraint(
            "extraction_disposition = 'REVIEW_REQUIRED'",
            name="extraction_disposition_value",
        ),
        CheckConstraint(
            "source_type IN ('OFFICIAL_CODE', 'OFFICIAL_GIS', 'VERIFIED_SIGN', "
            "'UNIVERSITY', 'OSM', 'COMMUNITY', 'WEB', 'IMAGERY_INFERENCE')",
            name="source_type_value",
        ),
        CheckConstraint(
            "raw_storage_policy IN ('PERSIST', 'EPHEMERAL', 'REFERENCE_ONLY')",
            name="raw_storage_policy_value",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint(
            "result_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="result_snapshot_hash_sha256",
        ),
        CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="content_hash_sha256",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object' AND "
            "NOT (result_snapshot ?| ARRAY['content', 'raw_content', 'source_content'])",
            name="result_snapshot_safe_object",
        ),
        CheckConstraint(
            "jsonb_typeof(review_reason_codes) = 'array'",
            name="review_reason_codes_array",
        ),
        CheckConstraint("jsonb_typeof(error_codes) = 'array'", name="error_codes_array"),
        CheckConstraint(
            "published_at IS NULL OR published_at <= retrieved_at",
            name="published_not_after_retrieved",
        ),
        CheckConstraint(
            "observed_at IS NULL OR observed_at <= retrieved_at",
            name="observed_not_after_retrieved",
        ),
        CheckConstraint(
            "updated_at >= submitted_at AND "
            "(claimed_at IS NULL OR claimed_at >= submitted_at) AND "
            "(resolved_at IS NULL OR resolved_at >= submitted_at)",
            name="timestamp_order",
        ),
        CheckConstraint(
            "(assigned_reviewer_id IS NULL) = (claimed_at IS NULL) AND "
            "(claimed_at IS NULL) = (claim_expires_at IS NULL)",
            name="claim_assignment_complete",
        ),
        CheckConstraint(
            "claim_expires_at IS NULL OR claim_expires_at > claimed_at",
            name="claim_expiry_after_claim",
        ),
        CheckConstraint(
            "(status = 'PENDING' AND assigned_reviewer_id IS NULL) OR "
            "(status IN ('IN_REVIEW', 'APPROVED', 'REJECTED') AND "
            "assigned_reviewer_id IS NOT NULL)",
            name="assignee_matches_status",
        ),
        CheckConstraint(
            "(status IN ('APPROVED', 'REJECTED') AND resolved_at IS NOT NULL) OR "
            "(status IN ('PENDING', 'IN_REVIEW') AND resolved_at IS NULL)",
            name="resolution_matches_status",
        ),
        CheckConstraint(
            "(status = 'APPROVED' AND "
            "approval_scope IN ('EVIDENCE_ONLY', 'EVIDENCE_AND_RULES') AND "
            "approval_id IS NOT NULL AND approved_evidence_id IS NOT NULL) OR "
            "(status <> 'APPROVED' AND approval_scope IS NULL AND "
            "approval_id IS NULL AND approved_evidence_id IS NULL)",
            name="approval_complete",
        ),
        UniqueConstraint(
            "extraction_result_id", name="uq_evidence_review_queue_extraction_result_id"
        ),
        UniqueConstraint("approval_id", name="uq_evidence_review_queue_approval_id"),
        UniqueConstraint(
            "approved_evidence_id", name="uq_evidence_review_queue_approved_evidence_id"
        ),
    )

    review_item_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    extraction_result_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    service_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri_or_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_storage_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    snapshot_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    error_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    assigned_reviewer_id: Mapped[str | None] = mapped_column(String(128))
    approval_scope: Mapped[str | None] = mapped_column(String(32))
    approval_id: Mapped[str | None] = mapped_column(String(64))
    approved_evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("parking_sources.evidence_id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    approved_evidence: Mapped[EvidenceModel | None] = relationship()
    events: Mapped[list["EvidenceReviewEventModel"]] = relationship(
        back_populates="review_item",
        order_by="EvidenceReviewEventModel.queue_revision",
    )


Index(
    "ix_evidence_review_queue_status_submitted_at",
    EvidenceReviewQueueModel.status,
    EvidenceReviewQueueModel.submitted_at,
)
Index(
    "ix_evidence_review_queue_assignee_status",
    EvidenceReviewQueueModel.assigned_reviewer_id,
    EvidenceReviewQueueModel.status,
)
Index(
    "ix_evidence_review_queue_service_source",
    EvidenceReviewQueueModel.service_kind,
    EvidenceReviewQueueModel.source_type,
)
Index("ix_evidence_review_queue_extractor_version", EvidenceReviewQueueModel.extractor_version)


class EvidenceReviewEventModel(Base):
    """Append-only authorization and state-transition audit record for one review item."""

    __tablename__ = "evidence_review_events"
    __table_args__ = (
        CheckConstraint("queue_revision > 0", name="queue_revision_positive"),
        CheckConstraint(
            "action IN ('SUBMIT', 'CLAIM', 'RELEASE', "
            "'APPROVE_EVIDENCE', 'APPROVE_RULES', 'REJECT')",
            name="action_value",
        ),
        CheckConstraint(
            "previous_status IS NULL OR previous_status IN "
            "('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED')",
            name="previous_status_value",
        ),
        CheckConstraint(
            "new_status IN ('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED')",
            name="new_status_value",
        ),
        CheckConstraint(
            "jsonb_typeof(actor_roles) = 'array' AND jsonb_array_length(actor_roles) > 0",
            name="actor_roles_nonempty_array",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code ~ '^[A-Z][A-Z0-9_]{0,127}$'",
            name="reason_code_safe",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata_snapshot) = 'object' AND "
            "NOT (metadata_snapshot ?| ARRAY['content', 'raw_content', 'source_content'])",
            name="metadata_snapshot_safe_object",
        ),
        CheckConstraint(
            "(action = 'SUBMIT' AND queue_revision = 1) OR "
            "(action <> 'SUBMIT' AND queue_revision > 1)",
            name="initial_revision_matches_action",
        ),
        CheckConstraint(
            "(action = 'SUBMIT' AND previous_status IS NULL AND new_status = 'PENDING') OR "
            "(action = 'CLAIM' AND new_status = 'IN_REVIEW' AND "
            "(previous_status = 'PENDING' OR "
            "(previous_status = 'IN_REVIEW' AND "
            "reason_code = 'LEASE_EXPIRED_RECLAIM'))) OR "
            "(action = 'RELEASE' AND previous_status = 'IN_REVIEW' AND "
            "new_status = 'PENDING') OR "
            "(action IN ('APPROVE_EVIDENCE', 'APPROVE_RULES') AND "
            "previous_status = 'IN_REVIEW' AND new_status = 'APPROVED') OR "
            "(action = 'REJECT' AND previous_status = 'IN_REVIEW' AND new_status = 'REJECTED')",
            name="action_status_transition",
        ),
        CheckConstraint(
            "(action = 'CLAIM' AND lease_expires_at IS NOT NULL AND "
            "lease_expires_at > occurred_at) OR "
            "(action <> 'CLAIM' AND lease_expires_at IS NULL)",
            name="lease_matches_action",
        ),
        CheckConstraint(
            "(action IN ('APPROVE_EVIDENCE', 'APPROVE_RULES') AND "
            "approval_id IS NOT NULL AND approved_evidence_id IS NOT NULL) OR "
            "(action NOT IN ('APPROVE_EVIDENCE', 'APPROVE_RULES') AND "
            "approval_id IS NULL AND approved_evidence_id IS NULL)",
            name="approval_references_match_action",
        ),
        CheckConstraint(
            "action_idempotency_key_hash ~ '^[0-9a-f]{64}$' AND "
            "action_request_hash ~ '^[0-9a-f]{64}$' AND "
            "event_hash ~ '^[0-9a-f]{64}$' AND "
            "(prior_event_hash IS NULL OR prior_event_hash ~ '^[0-9a-f]{64}$')",
            name="audit_hashes_sha256",
        ),
        CheckConstraint(
            "(queue_revision = 1 AND prior_event_hash IS NULL) OR "
            "(queue_revision > 1 AND prior_event_hash IS NOT NULL)",
            name="prior_hash_matches_revision",
        ),
        UniqueConstraint(
            "review_item_id",
            "queue_revision",
            name="uq_evidence_review_events_item_revision",
        ),
        UniqueConstraint(
            "action_idempotency_key_hash",
            name="uq_evidence_review_events_action_idempotency_key_hash",
        ),
        UniqueConstraint("event_hash", name="uq_evidence_review_events_event_hash"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    review_item_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_review_queue.review_item_id", ondelete="RESTRICT"), nullable=False
    )
    queue_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action_idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_id: Mapped[str | None] = mapped_column(String(64))
    approved_evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("parking_sources.evidence_id", ondelete="RESTRICT")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    review_item: Mapped[EvidenceReviewQueueModel] = relationship(back_populates="events")
    approved_evidence: Mapped[EvidenceModel | None] = relationship()


Index(
    "ix_evidence_review_events_item_occurred_at",
    EvidenceReviewEventModel.review_item_id,
    EvidenceReviewEventModel.occurred_at,
)
Index(
    "ix_evidence_review_events_actor_occurred_at",
    EvidenceReviewEventModel.actor_id,
    EvidenceReviewEventModel.occurred_at,
)
Index(
    "ix_evidence_review_events_action_occurred_at",
    EvidenceReviewEventModel.action,
    EvidenceReviewEventModel.occurred_at,
)


class SearchSessionModel(Base):
    __tablename__ = "search_sessions"
    __table_args__ = (
        CheckConstraint("max_walk_minutes > 0", name="max_walk_minutes_positive"),
        CheckConstraint("max_candidates > 0", name="max_candidates_positive"),
        CheckConstraint(
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
            name="replayable_snapshot_complete",
        ),
        UniqueConstraint("idempotency_key_hash", name="uq_search_sessions_idempotency_key_hash"),
        UniqueConstraint("route_id", name="uq_search_sessions_route_id"),
    )

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.destination_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    origin: Mapped[WKBElement] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=False
    )
    requested_arrival_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    free_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_walk_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    max_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    candidate_segment_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[SearchSessionStatus] = mapped_column(
        _domain_enum(SearchSessionStatus, "search_session_status"),
        nullable=False,
        default=SearchSessionStatus.CREATED,
    )
    rule_engine_version: Mapped[str | None] = mapped_column(String(128))
    availability_model_version: Mapped[str | None] = mapped_column(String(128))
    route_matrix_version: Mapped[str | None] = mapped_column(String(128))
    optimizer_version: Mapped[str | None] = mapped_column(String(128))
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    replayable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    snapshot_schema_version: Mapped[str | None] = mapped_column(String(128))
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    candidate_decisions_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    route_matrix_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    optimizer_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    route_id: Mapped[str | None] = mapped_column(String(64))
    route_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artifact_hash: Mapped[str | None] = mapped_column(String(64))
    route_matrix_provider_version: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    destination: Mapped[DestinationModel] = relationship(back_populates="search_sessions")
    route_steps: Mapped[list["SearchRouteStepModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SearchRouteStepModel.step_order",
    )
    outcomes: Mapped[list["ParkingOutcomeModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


Index("ix_search_sessions_origin_gist", SearchSessionModel.origin, postgresql_using="gist")
Index("ix_search_sessions_created_at", SearchSessionModel.created_at)


class SearchRouteStepModel(Base):
    __tablename__ = "search_route_steps"
    __table_args__ = (
        UniqueConstraint("session_id", "step_order", name="uq_search_route_steps_session_order"),
        CheckConstraint("step_order >= 0", name="step_order_nonnegative"),
        CheckConstraint(
            "legal_confidence >= 0 AND legal_confidence <= 1",
            name="legal_confidence_probability",
        ),
        CheckConstraint(
            "availability_probability IS NULL OR "
            "(availability_probability >= 0 AND availability_probability <= 1)",
            name="availability_probability_range",
        ),
        CheckConstraint("drive_eta_min >= 0", name="drive_eta_nonnegative"),
        CheckConstraint("walk_min >= 0", name="walk_nonnegative"),
        CheckConstraint(
            "availability_target_window_seconds IS NULL OR availability_target_window_seconds > 0",
            name="availability_target_window_positive",
        ),
    )

    route_step_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("search_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("street_segments.segment_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_state: Mapped[LegalState] = mapped_column(
        _domain_enum(LegalState, "legal_state"), nullable=False
    )
    free_state: Mapped[FreeState] = mapped_column(
        _domain_enum(FreeState, "free_state"), nullable=False
    )
    legal_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    availability_probability: Mapped[float | None] = mapped_column(Float)
    drive_eta_min: Mapped[float] = mapped_column(Float, nullable=False)
    walk_min: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rule_engine_version: Mapped[str] = mapped_column(String(128), nullable=False)
    availability_model_version: Mapped[str | None] = mapped_column(String(128))
    legality_evaluation_id: Mapped[str | None] = mapped_column(String(64))
    availability_prediction_id: Mapped[str | None] = mapped_column(String(64))
    availability_target_window_seconds: Mapped[int | None] = mapped_column(Integer)

    session: Mapped[SearchSessionModel] = relationship(back_populates="route_steps")
    segment: Mapped[ParkingSegmentModel] = relationship(back_populates="route_steps")


class ParkingOutcomeModel(Base):
    __tablename__ = "parking_outcomes"
    __table_args__ = (
        CheckConstraint(
            "search_duration_seconds IS NULL OR search_duration_seconds >= 0",
            name="search_duration_nonnegative",
        ),
        CheckConstraint(
            "route_step_order IS NULL OR route_step_order >= 0",
            name="route_step_order_nonnegative",
        ),
    )

    outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("search_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[str | None] = mapped_column(
        ForeignKey("street_segments.segment_id", ondelete="SET NULL"), index=True
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    search_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    route_step_order: Mapped[int | None] = mapped_column(Integer)

    session: Mapped[SearchSessionModel] = relationship(back_populates="outcomes")
    segment: Mapped[ParkingSegmentModel | None] = relationship(back_populates="outcomes")
