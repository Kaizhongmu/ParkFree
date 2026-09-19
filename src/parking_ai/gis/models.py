from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from parking_ai.domain import (
    Evidence,
    LineStringGeometry,
    PhysicalState,
    SegmentSide,
)


class SideReferenceDirection(StrEnum):
    """Direction against which a road feature's candidate sides are expressed."""

    SOURCE_GEOMETRY = "SOURCE_GEOMETRY"
    CANONICAL_GEOMETRY = "CANONICAL_GEOMETRY"


class RoadFeature(BaseModel):
    """Provider-independent road centerline supplied to candidate generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feature_id: str = Field(min_length=1, max_length=128)
    geometry: LineStringGeometry
    street_name: str | None = Field(default=None, max_length=255)
    road_type: str = Field(min_length=1, max_length=64)
    break_indexes: frozenset[int] = Field(default_factory=frozenset)
    candidate_sides: tuple[SegmentSide, ...] = (
        SegmentSide.LEFT,
        SegmentSide.RIGHT,
    )
    side_reference_direction: SideReferenceDirection = SideReferenceDirection.SOURCE_GEOMETRY
    parking_candidate: bool | None = None
    physical_state: PhysicalState = PhysicalState.UNKNOWN

    @field_validator("street_name")
    @classmethod
    def normalize_street_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("road_type")
    @classmethod
    def normalize_road_type(cls, value: str) -> str:
        normalized = " ".join(value.split()).casefold()
        if not normalized:
            raise ValueError("road type must contain non-whitespace characters")
        return normalized

    @model_validator(mode="after")
    def validate_breaks_and_sides(self) -> "RoadFeature":
        last_index = len(self.geometry.coordinates) - 1
        if any(index < 0 or index > last_index for index in self.break_indexes):
            raise ValueError("road break indexes must refer to geometry coordinates")
        if not self.candidate_sides:
            raise ValueError("road must define at least one candidate side")
        if len(set(self.candidate_sides)) != len(self.candidate_sides):
            raise ValueError("candidate sides must be unique")
        return self


class OSMFixture(BaseModel):
    """Validated local OSM fixture after provider-specific adaptation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    observed_at: datetime
    source_uri: str
    license: str
    attribution: str
    evidence: Evidence
    roads: tuple[RoadFeature, ...]
