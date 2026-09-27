from __future__ import annotations

from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from timezonefinder import TimezoneFinder

from parking_ai.domain import GeoPoint


class TimezoneResolutionError(LookupError):
    """No usable IANA timezone could be resolved for a destination coordinate."""


class CoordinateTimezoneFinder(Protocol):
    def timezone_at(self, *, lng: float, lat: float) -> str | None: ...


class OfflineDestinationTimezoneResolver:
    """Resolve WGS84 coordinates against bundled timezone polygons without network I/O."""

    def __init__(self, finder: CoordinateTimezoneFinder | None = None) -> None:
        self._finder = finder or TimezoneFinder(in_memory=False)

    def resolve(self, location: GeoPoint) -> str:
        timezone_name = self._finder.timezone_at(
            lng=location.longitude,
            lat=location.latitude,
        )
        if timezone_name is None:
            raise TimezoneResolutionError("destination timezone could not be resolved")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise TimezoneResolutionError("resolved destination timezone is unavailable") from error
        return timezone_name


__all__ = ["OfflineDestinationTimezoneResolver", "TimezoneResolutionError"]
