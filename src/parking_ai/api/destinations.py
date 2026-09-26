"""Public, provider-independent destination discovery endpoint."""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from parking_ai.geocoding import (
    GeocodingError,
    GeocodingRequest,
    GeocodingResult,
)

logger = logging.getLogger(__name__)

DestinationSearchHandler = Callable[[GeocodingRequest], GeocodingResult]

DESTINATION_SEARCH_HANDLER_STATE_KEY = "destination_search_handler"
_UNAVAILABLE_DETAIL = "Destination search is temporarily unavailable"

router = APIRouter()


class DestinationSearchRequest(BaseModel):
    """Small privacy-preserving body for an explicit, non-autocomplete search."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=255)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("destination query must contain at least two characters")
        return normalized


@router.post(
    "/v1/destinations/search",
    response_model=GeocodingResult,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": _UNAVAILABLE_DETAIL},
    },
)
def search_destinations(
    request: Request,
    payload: DestinationSearchRequest,
) -> GeocodingResult:
    """Resolve an explicitly submitted US place query without choosing an ambiguous match."""

    handler: DestinationSearchHandler | None = getattr(
        request.app.state,
        DESTINATION_SEARCH_HANDLER_STATE_KEY,
        None,
    )
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        )

    try:
        return GeocodingResult.model_validate(handler(GeocodingRequest(query=payload.query)))
    except GeocodingError as exc:
        logger.warning("Destination discovery provider unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        logger.error("Destination discovery returned invalid internal data")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc


__all__ = [
    "DESTINATION_SEARCH_HANDLER_STATE_KEY",
    "DestinationSearchHandler",
    "DestinationSearchRequest",
    "router",
]
