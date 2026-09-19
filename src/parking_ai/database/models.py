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


class SearchSessionModel(Base):
    __tablename__ = "search_sessions"
    __table_args__ = (
        CheckConstraint("max_walk_minutes > 0", name="max_walk_minutes_positive"),
        CheckConstraint("max_candidates > 0", name="max_candidates_positive"),
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
