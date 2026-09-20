from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from parking_ai.api.schemas import ParkingSearchRequest, ParkingSearchResponse
from parking_ai.orchestrator.schemas import ParkingSearchCommand
from parking_ai.orchestrator.search import (
    DestinationNotFoundError,
    IdempotencyConflictError,
    SearchUnavailableError,
)

logger = logging.getLogger(__name__)

SearchHandler = Callable[[ParkingSearchCommand, str | None], ParkingSearchResponse]
Clock = Callable[[], datetime]

SEARCH_HANDLER_STATE_KEY = "parking_search_handler"
SEARCH_CLOCK_STATE_KEY = "parking_search_clock"

_NOT_FOUND_DETAIL = "Destination was not found"
_CONFLICT_DETAIL = "Idempotency key conflicts with an existing search"
_UNAVAILABLE_DETAIL = "Parking search is temporarily unavailable"

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post(
    "/v1/parking/search",
    response_model=ParkingSearchResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": _NOT_FOUND_DETAIL},
        status.HTTP_409_CONFLICT: {"description": _CONFLICT_DETAIL},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": _UNAVAILABLE_DETAIL},
    },
)
def search_parking(
    payload: ParkingSearchRequest,
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^\S(?:.*\S)?$",
        ),
    ] = None,
) -> ParkingSearchResponse:
    handler: SearchHandler | None = getattr(request.app.state, SEARCH_HANDLER_STATE_KEY, None)
    clock: Clock = getattr(request.app.state, SEARCH_CLOCK_STATE_KEY, _utc_now)
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        )

    try:
        command = payload.to_command(resolved_now=clock())
        return ParkingSearchResponse.model_validate(handler(command, idempotency_key))
    except DestinationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        ) from exc
    except SearchUnavailableError as exc:
        logger.warning("Parking search unavailable", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        logger.exception("Parking search returned invalid internal data")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected parking search failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)
