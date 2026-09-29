"""On-demand road-coverage contracts and orchestration."""

from parking_ai.coverage.fallback import FailoverRoadCoverageProvider
from parking_ai.coverage.models import (
    CoverageAttemptOutcome,
    CoverageAttemptRole,
    CoverageProviderAttempt,
    CoverageProviderMetadata,
    CoverageSummary,
    DestinationTimezoneResolver,
    OnDemandParkingCommand,
    OnDemandParkingResponse,
    OnDemandParkingStatus,
    OnDemandResearchMode,
    ResearchEnrichmentStatus,
    RoadAcquisition,
    RoadCoverageError,
    RoadCoverageExhaustedError,
    RoadCoverageProvider,
    RoadCoverageProviderError,
    RoadCoverageResponseError,
    SelectedDestinationNotFoundError,
)
from parking_ai.coverage.overpass import OverpassRoadCoverageProvider
from parking_ai.coverage.service import OnDemandParkingService
from parking_ai.coverage.tigerweb import TIGERwebRoadCoverageProvider
from parking_ai.coverage.timezones import (
    OfflineDestinationTimezoneResolver,
    TimezoneResolutionError,
)

__all__ = [
    "CoverageAttemptOutcome",
    "CoverageAttemptRole",
    "CoverageProviderAttempt",
    "CoverageProviderMetadata",
    "CoverageSummary",
    "DestinationTimezoneResolver",
    "FailoverRoadCoverageProvider",
    "OfflineDestinationTimezoneResolver",
    "OnDemandParkingCommand",
    "OnDemandParkingResponse",
    "OnDemandParkingService",
    "OnDemandParkingStatus",
    "OnDemandResearchMode",
    "OverpassRoadCoverageProvider",
    "ResearchEnrichmentStatus",
    "RoadAcquisition",
    "RoadCoverageError",
    "RoadCoverageExhaustedError",
    "RoadCoverageProvider",
    "RoadCoverageProviderError",
    "RoadCoverageResponseError",
    "SelectedDestinationNotFoundError",
    "TIGERwebRoadCoverageProvider",
    "TimezoneResolutionError",
]
