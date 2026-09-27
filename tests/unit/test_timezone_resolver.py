from __future__ import annotations

import pytest

from parking_ai.coverage import OfflineDestinationTimezoneResolver, TimezoneResolutionError
from parking_ai.domain import GeoPoint


class _Finder:
    def __init__(self, timezone_name: str | None) -> None:
        self.timezone_name = timezone_name

    def timezone_at(self, *, lng: float, lat: float) -> str | None:
        del lng, lat
        return self.timezone_name


def test_offline_timezone_resolver_uses_bundled_coordinate_polygons() -> None:
    resolver = OfflineDestinationTimezoneResolver()

    assert resolver.resolve(GeoPoint(latitude=32.856698, longitude=-96.766458)) == (
        "America/Chicago"
    )
    assert resolver.resolve(GeoPoint(latitude=47.6205, longitude=-122.3493)) == (
        "America/Los_Angeles"
    )
    assert resolver.resolve(GeoPoint(latitude=40.7484, longitude=-73.9857)) == ("America/New_York")


@pytest.mark.parametrize("timezone_name", [None, "Not/A_Timezone"])
def test_offline_timezone_resolver_rejects_missing_or_invalid_names(
    timezone_name: str | None,
) -> None:
    resolver = OfflineDestinationTimezoneResolver(_Finder(timezone_name))

    with pytest.raises(TimezoneResolutionError):
        resolver.resolve(GeoPoint(latitude=32.856698, longitude=-96.766458))
