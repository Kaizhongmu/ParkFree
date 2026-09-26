"""Strict HTTP schemas for the parking-search API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from parking_ai.domain import GeoPoint, UserProfile
from parking_ai.domain.schemas import PermitTypes
from parking_ai.orchestrator.schemas import (
    DestinationSelector,
    ParkingSearchCommand,
    ParkingSearchResponse,
)

__all__ = [
    "DestinationRequest",
    "OriginRequest",
    "ParkingSearchRequest",
    "ParkingSearchResponse",
    "VehicleProfileRequest",
]

PositiveInteger = Annotated[int, Field(strict=True, gt=0)]
CandidateLimit = Annotated[int, Field(strict=True, gt=0, le=20)]


class ApiModel(BaseModel):
    """Base model for public request bodies; unknown fields are always rejected."""

    model_config = ConfigDict(extra="forbid")


class OriginRequest(ApiModel):
    lat: float = Field(strict=True, ge=-90, le=90)
    lon: float = Field(strict=True, ge=-180, le=180)


class DestinationRequest(ApiModel):
    query: str | None = Field(default=None, max_length=255)
    destination_id: str | None = Field(default=None, max_length=64)

    @field_validator("query", "destination_id")
    @classmethod
    def require_nonblank_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("destination values must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_exactly_one_selector(self) -> "DestinationRequest":
        if (self.query is None) == (self.destination_id is None):
            raise ValueError("provide exactly one of query or destination_id")
        return self


class VehicleProfileRequest(ApiModel):
    type: str = Field(default="passenger", min_length=1, max_length=64)
    permit_types: PermitTypes = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def normalize_vehicle_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("vehicle type must not be blank")
        return normalized


class ParkingSearchRequest(ApiModel):
    origin: OriginRequest
    destination: DestinationRequest
    arrival_time: datetime | Literal["now"] = "now"
    parking_duration_minutes: PositiveInteger = Field(le=1_440)
    free_only: StrictBool = True
    max_walk_minutes: float = Field(default=8.0, strict=True, gt=0, le=30)
    vehicle_profile: VehicleProfileRequest = Field(default_factory=VehicleProfileRequest)
    max_candidates: CandidateLimit = 20

    @field_validator("arrival_time")
    @classmethod
    def require_aware_arrival_time(
        cls, value: datetime | Literal["now"]
    ) -> datetime | Literal["now"]:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("arrival_time must include timezone information")
        return value

    def to_command(self, *, resolved_now: datetime) -> ParkingSearchCommand:
        """Translate public names into the provider-independent orchestration command."""

        if resolved_now.tzinfo is None or resolved_now.utcoffset() is None:
            raise ValueError("resolved_now must include timezone information")
        arrival_time = resolved_now if self.arrival_time == "now" else self.arrival_time
        return ParkingSearchCommand(
            origin=GeoPoint(latitude=self.origin.lat, longitude=self.origin.lon),
            destination=DestinationSelector(
                query=self.destination.query,
                destination_id=self.destination.destination_id,
            ),
            arrival_time=arrival_time,
            arrival_time_was_now=self.arrival_time == "now",
            parking_duration_minutes=self.parking_duration_minutes,
            free_only=self.free_only,
            max_walk_minutes=self.max_walk_minutes,
            vehicle_profile=UserProfile(
                vehicle_type=self.vehicle_profile.type,
                permit_types=self.vehicle_profile.permit_types,
                requested_parking_duration_min=self.parking_duration_minutes,
            ),
            max_candidates=self.max_candidates,
        )
