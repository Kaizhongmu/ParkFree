from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from parking_ai.domain import EvidenceStoragePolicy, GeoPoint
from parking_ai.domain.schemas import AwareDateTime


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


class GeocodingBounds(BaseModel):
    """Provider-independent longitude/latitude search bounds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def validate_order(self) -> GeocodingBounds:
        if self.west >= self.east:
            raise ValueError("geocoding west bound must be less than east bound")
        if self.south >= self.north:
            raise ValueError("geocoding south bound must be less than north bound")
        return self


class GeocodingRequest(BaseModel):
    """A bounded US-only destination lookup independent of provider syntax."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=255)
    country_code: Literal["US"] = "US"
    language: str = Field(default="en", min_length=2, max_length=35, pattern=r"^[A-Za-z0-9-]+$")
    limit: int = Field(default=5, ge=1, le=10)
    viewbox: GeocodingBounds | None = None
    bounded: bool = False

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = _normalize_text(value)
        if not normalized:
            raise ValueError("geocoding query must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_bounded_search(self) -> GeocodingRequest:
        if self.bounded and self.viewbox is None:
            raise ValueError("bounded geocoding requires a viewbox")
        return self


class GeocodingMatch(BaseModel):
    """Normalized provider-independent destination candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    match_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    formatted_address: str = Field(min_length=1, max_length=1_024)
    location: GeoPoint
    destination_type: str | None = Field(default=None, max_length=64)
    country_code: Literal["US"] = "US"
    source_reference: str = Field(min_length=1, max_length=255)


class GeocodingStatus(StrEnum):
    NO_MATCH = "NO_MATCH"
    UNIQUE = "UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"


class GeocodingProviderMetadata(BaseModel):
    """Attribution and retention policy attached to every provider result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    attribution: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=255)
    source_uri: str = Field(min_length=1, max_length=2_048)
    raw_storage_policy: EvidenceStoragePolicy
    normalized_storage_policy: EvidenceStoragePolicy
    retrieved_at: AwareDateTime


class GeocodingResult(BaseModel):
    """Explicitly preserve no-match and ambiguous provider outcomes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: GeocodingStatus
    matches: tuple[GeocodingMatch, ...]
    metadata: GeocodingProviderMetadata
    cache_hit: bool = False

    @model_validator(mode="after")
    def validate_status(self) -> GeocodingResult:
        match_count = len(self.matches)
        expected = (
            GeocodingStatus.NO_MATCH
            if match_count == 0
            else GeocodingStatus.UNIQUE
            if match_count == 1
            else GeocodingStatus.AMBIGUOUS
        )
        if self.status is not expected:
            raise ValueError("geocoding status must match the number of returned matches")
        match_ids = [match.match_id for match in self.matches]
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("geocoding matches must have unique stable IDs")
        return self


class Geocoder(Protocol):
    def geocode(self, request: GeocodingRequest) -> GeocodingResult: ...


def stable_geocoding_match_id(
    *,
    name: str,
    formatted_address: str,
    location: GeoPoint,
    destination_type: str | None,
    country_code: Literal["US"] = "US",
) -> str:
    """Return an order- and process-independent identity for normalized match content."""

    normalized_type = _normalize_text(destination_type).casefold() if destination_type else None
    payload = {
        "country_code": country_code,
        "destination_type": normalized_type,
        "formatted_address": _normalize_text(formatted_address).casefold(),
        "location": [round(location.longitude, 7), round(location.latitude, 7)],
        "name": _normalize_text(name).casefold(),
        "version": 1,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"geo_{digest[:32]}"


def normalized_geocoding_cache_key(request: GeocodingRequest) -> str:
    """Hash every material normalized lookup input without retaining the query in the key."""

    payload = request.model_dump(mode="json")
    payload["query"] = request.query.casefold()
    payload["language"] = request.language.casefold()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def geocoding_status(matches: tuple[GeocodingMatch, ...]) -> GeocodingStatus:
    if not matches:
        return GeocodingStatus.NO_MATCH
    if len(matches) == 1:
        return GeocodingStatus.UNIQUE
    return GeocodingStatus.AMBIGUOUS


def require_aware_wall_clock(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("geocoding wall clock must be timezone-aware")
    return value
