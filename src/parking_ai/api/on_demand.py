"""HTTP boundary for bounded, on-demand parking candidate discovery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from parking_ai.api.schemas import OriginRequest, VehicleProfileRequest
from parking_ai.coverage import (
    OnDemandParkingCommand,
    OnDemandParkingResponse,
    SelectedDestinationNotFoundError,
)
from parking_ai.domain import GeoPoint, UserProfile
from parking_ai.geocoding import GeocodingError

logger = logging.getLogger(__name__)

OnDemandParkingHandler = Callable[[OnDemandParkingCommand], OnDemandParkingResponse]
Clock = Callable[[], datetime]

ON_DEMAND_HANDLER_STATE_KEY = "on_demand_parking_handler"
ON_DEMAND_CLOCK_STATE_KEY = "on_demand_parking_clock"

_UNAVAILABLE_DETAIL = "On-demand parking discovery is temporarily unavailable"
_STALE_SELECTION_DETAIL = "Selected destination is no longer available; search again"

router = APIRouter()


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectedDestinationRequest(ApiModel):
    query: str = Field(min_length=2, max_length=255)
    match_id: str = Field(min_length=1, max_length=64)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("destination query must contain at least two characters")
        return normalized

    @field_validator("match_id")
    @classmethod
    def normalize_match_id(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("destination match ID must not be blank")
        return normalized


class OnDemandParkingRequest(ApiModel):
    origin: OriginRequest
    destination: SelectedDestinationRequest
    arrival_time: datetime | Literal["now"] = "now"
    parking_duration_minutes: int = Field(strict=True, gt=0, le=1_440)
    free_only: StrictBool = True
    max_walk_minutes: float = Field(default=8.0, strict=True, gt=0, le=15)
    vehicle_profile: VehicleProfileRequest = Field(default_factory=VehicleProfileRequest)
    max_candidates: int = Field(default=20, strict=True, gt=0, le=20)

    @field_validator("arrival_time")
    @classmethod
    def require_aware_arrival_time(
        cls,
        value: datetime | Literal["now"],
    ) -> datetime | Literal["now"]:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("arrival_time must include timezone information")
        return value

    def to_command(self, *, resolved_now: datetime) -> OnDemandParkingCommand:
        if resolved_now.tzinfo is None or resolved_now.utcoffset() is None:
            raise ValueError("resolved_now must include timezone information")
        arrival_time = resolved_now if self.arrival_time == "now" else self.arrival_time
        return OnDemandParkingCommand(
            origin=GeoPoint(latitude=self.origin.lat, longitude=self.origin.lon),
            destination_query=self.destination.query,
            destination_match_id=self.destination.match_id,
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


@router.post(
    "/v1/parking/on-demand",
    response_model=OnDemandParkingResponse,
    responses={
        status.HTTP_409_CONFLICT: {"description": _STALE_SELECTION_DETAIL},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": _UNAVAILABLE_DETAIL},
    },
)
def search_on_demand_parking(
    payload: OnDemandParkingRequest,
    request: Request,
) -> OnDemandParkingResponse:
    handler: OnDemandParkingHandler | None = getattr(
        request.app.state,
        ON_DEMAND_HANDLER_STATE_KEY,
        None,
    )
    clock: Clock = getattr(request.app.state, ON_DEMAND_CLOCK_STATE_KEY, _utc_now)
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        )

    try:
        command = payload.to_command(resolved_now=clock())
        return OnDemandParkingResponse.model_validate(handler(command))
    except SelectedDestinationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_STALE_SELECTION_DETAIL,
        ) from exc
    except GeocodingError as exc:
        logger.warning("On-demand destination revalidation unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        logger.exception("On-demand parking discovery returned invalid internal data")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected on-demand parking discovery failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ON_DEMAND_CLOCK_STATE_KEY",
    "ON_DEMAND_HANDLER_STATE_KEY",
    "OnDemandParkingHandler",
    "OnDemandParkingRequest",
    "router",
]
