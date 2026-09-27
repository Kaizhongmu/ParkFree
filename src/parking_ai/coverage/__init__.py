"""On-demand road-coverage contracts and orchestration."""

from parking_ai.coverage.models import (
    CoverageProviderMetadata,
    CoverageSummary,
    DestinationTimezoneResolver,
    OnDemandParkingCommand,
    OnDemandParkingResponse,
    OnDemandParkingStatus,
    RoadAcquisition,
    RoadCoverageError,
    RoadCoverageProvider,
    RoadCoverageProviderError,
    RoadCoverageResponseError,
    SelectedDestinationNotFoundError,
)
from parking_ai.coverage.overpass import OverpassRoadCoverageProvider
from parking_ai.coverage.service import OnDemandParkingService
from parking_ai.coverage.timezones import (
    OfflineDestinationTimezoneResolver,
    TimezoneResolutionError,
)

__all__ = [
    "CoverageProviderMetadata",
    "CoverageSummary",
    "DestinationTimezoneResolver",
    "OfflineDestinationTimezoneResolver",
    "OnDemandParkingCommand",
    "OnDemandParkingResponse",
    "OnDemandParkingService",
    "OnDemandParkingStatus",
    "OverpassRoadCoverageProvider",
    "RoadAcquisition",
    "RoadCoverageError",
    "RoadCoverageProvider",
    "RoadCoverageProviderError",
    "RoadCoverageResponseError",
    "SelectedDestinationNotFoundError",
    "TimezoneResolutionError",
]
