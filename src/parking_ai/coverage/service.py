"""Bounded orchestration for provisional, on-demand parking leads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from parking_ai.availability import DeterministicAvailabilityBaseline
from parking_ai.coverage.models import (
    CoverageAttemptOutcome,
    CoverageAttemptRole,
    CoverageProviderAttempt,
    CoverageSummary,
    DestinationTimezoneResolver,
    OnDemandParkingCommand,
    OnDemandParkingResponse,
    OnDemandParkingStatus,
    RoadAcquisition,
    RoadCoverageError,
    RoadCoverageExhaustedError,
    RoadCoverageProvider,
    SelectedDestinationNotFoundError,
)
from parking_ai.coverage.timezones import TimezoneResolutionError
from parking_ai.domain import (
    AvailabilityContext,
    AvailabilityPrediction,
    Destination,
    DestinationAccessPoint,
    SearchConstraints,
)
from parking_ai.geocoding import Geocoder, GeocodingMatch, GeocodingRequest
from parking_ai.gis import DeterministicCandidateSegmentService

_PROVISIONAL_WARNING = (
    "These are road-derived leads, not verified legal or free parking. "
    "Legality and payment state remain UNKNOWN until trusted regulation evidence is evaluated."
)
_PROVIDER_WARNING = "Road coverage could not be acquired; no parking candidates were inferred."
_NO_CANDIDATES_WARNING = (
    "The bounded road snapshot produced no eligible candidate segments. "
    "This does not prove that parking is unavailable."
)
_PREDICTION_WARNING = (
    "Availability is a statistical prior conditional on the curb being legal and usable. "
    "It is not a free-parking or legality probability."
)
_PREDICTION_UNAVAILABLE_WARNING = (
    "The destination timezone could not be resolved, so conditional availability was not estimated."
)
_MIN_USABLE_CURB_LENGTH_M = 6.0
_CANDIDATE_POOL_MULTIPLIER = 5
_MAX_CANDIDATE_POOL = 100


class OnDemandParkingService:
    """Revalidate a place and generate candidates from a fresh bounded road snapshot."""

    def __init__(
        self,
        geocoder: Geocoder,
        road_provider: RoadCoverageProvider,
        *,
        timezone_resolver: DestinationTimezoneResolver | None = None,
        prediction_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._geocoder = geocoder
        self._road_provider = road_provider
        self._timezone_resolver = timezone_resolver
        self._prediction_clock = prediction_clock or (lambda: datetime.now(UTC))

    def search(self, command: OnDemandParkingCommand) -> OnDemandParkingResponse:
        geocoding = self._geocoder.geocode(
            GeocodingRequest(query=command.destination_query, limit=10)
        )
        selected_match = next(
            (
                match
                for match in geocoding.matches
                if match.match_id == command.destination_match_id
            ),
            None,
        )
        if selected_match is None:
            raise SelectedDestinationNotFoundError(
                "selected destination was not present in the canonical re-query"
            )
        destination = _destination_from_match(selected_match)

        try:
            acquisition = self._road_provider.acquire(
                destination,
                command.max_walk_minutes,
            )
        except RoadCoverageExhaustedError as error:
            return OnDemandParkingResponse(
                status=OnDemandParkingStatus.PROVIDER_UNAVAILABLE,
                destination=destination,
                resolved_arrival_time=command.arrival_time,
                candidate_segments=(),
                coverage=None,
                provider_attempts=error.attempts,
                warnings=(_PROVIDER_WARNING,),
                attribution=(geocoding.metadata.attribution,),
            )
        except RoadCoverageError:
            provider_name = getattr(
                self._road_provider,
                "provider_name",
                "Road coverage API",
            )
            return OnDemandParkingResponse(
                status=OnDemandParkingStatus.PROVIDER_UNAVAILABLE,
                destination=destination,
                resolved_arrival_time=command.arrival_time,
                candidate_segments=(),
                coverage=None,
                provider_attempts=(
                    CoverageProviderAttempt(
                        provider_name=provider_name,
                        role=CoverageAttemptRole.PRIMARY,
                        outcome=CoverageAttemptOutcome.FAILED,
                    ),
                ),
                warnings=(_PROVIDER_WARNING,),
                attribution=(geocoding.metadata.attribution,),
            )

        candidate_pool_size = min(
            command.max_candidates * _CANDIDATE_POOL_MULTIPLIER,
            _MAX_CANDIDATE_POOL,
        )
        candidate_pool = DeterministicCandidateSegmentService(
            acquisition.roads,
            evidence_id=_acquisition_evidence_id(acquisition),
            data_freshness=acquisition.metadata.retrieved_at,
        ).get_candidate_segments(
            destination,
            SearchConstraints(
                free_only=command.free_only,
                max_walk_minutes=command.max_walk_minutes,
                max_candidates=candidate_pool_size,
            ),
        )
        # Radius clipping can create tiny boundary fragments. They are useful provenance but not
        # credible curb opportunities and must not receive the baseline's one-space minimum prior.
        segments = tuple(
            segment for segment in candidate_pool if segment.length_m >= _MIN_USABLE_CURB_LENGTH_M
        )[: command.max_candidates]
        coverage = CoverageSummary(
            metadata=acquisition.metadata,
            road_count=len(acquisition.roads),
            tagged_road_count=sum(
                any(key.casefold().startswith("parking:") for key in tags)
                for tags in acquisition.tags_by_feature_id.values()
            ),
            provider_attempts=acquisition.provider_attempts
            or (
                CoverageProviderAttempt(
                    provider_name=acquisition.metadata.provider_name,
                    role=CoverageAttemptRole.PRIMARY,
                    outcome=CoverageAttemptOutcome.SUCCEEDED,
                ),
            ),
            cache_hit=acquisition.cache_hit,
        )
        status = (
            OnDemandParkingStatus.PROVISIONAL_LEADS
            if segments
            else OnDemandParkingStatus.NO_CANDIDATES
        )
        warning = _PROVISIONAL_WARNING if segments else _NO_CANDIDATES_WARNING
        predictions: tuple[AvailabilityPrediction, ...] = ()
        prediction_assumption = None
        calibration_status = None
        destination_timezone = None
        warnings = [warning]
        if segments and self._timezone_resolver is not None:
            try:
                destination_timezone = self._timezone_resolver.resolve(destination.location)
                timezone = ZoneInfo(destination_timezone)
            except (TimezoneResolutionError, ZoneInfoNotFoundError):
                destination_timezone = None
                warnings.append(_PREDICTION_UNAVAILABLE_WARNING)
            else:
                prediction_instant = self._prediction_clock()
                predictor = DeterministicAvailabilityBaseline(
                    timezone=timezone,
                    clock=lambda: prediction_instant,
                )
                context = AvailabilityContext(arrival_time=command.arrival_time)
                predictions = tuple(
                    predictor.predict_availability(segment, context) for segment in segments
                )
                prediction_assumption = "CONDITIONAL_ON_LEGAL_AND_USABLE_CURB"
                calibration_status = "UNCALIBRATED_HEURISTIC"
                warnings.append(_PREDICTION_WARNING)
        return OnDemandParkingResponse(
            status=status,
            destination=destination,
            resolved_arrival_time=command.arrival_time,
            candidate_segments=tuple(segments),
            availability_predictions=predictions,
            availability_assumption=prediction_assumption,
            calibration_status=calibration_status,
            destination_timezone=destination_timezone,
            coverage=coverage,
            provider_attempts=coverage.provider_attempts,
            warnings=tuple(warnings),
            attribution=tuple(
                dict.fromkeys((geocoding.metadata.attribution, acquisition.metadata.attribution))
            ),
        )


def _destination_from_match(match: GeocodingMatch) -> Destination:
    identity = hashlib.sha256(f"on-demand-destination-v1:{match.match_id}".encode()).hexdigest()
    destination_id = f"ond_{identity[:32]}"
    access_identity = hashlib.sha256(f"access-v1:{destination_id}".encode()).hexdigest()
    return Destination(
        destination_id=destination_id,
        name=match.name,
        location=match.location,
        destination_type=match.destination_type,
        access_points=[
            DestinationAccessPoint(
                access_point_id=f"oda_{access_identity[:32]}",
                destination_id=destination_id,
                name="Canonical destination location",
                location=match.location,
            )
        ],
    )


def _acquisition_evidence_id(acquisition: RoadAcquisition) -> str:
    payload = {
        "provider": acquisition.metadata.provider_name,
        "provider_version": acquisition.metadata.provider_version,
        "roads": [road.model_dump(mode="json") for road in acquisition.roads],
        "tags": acquisition.tags_by_feature_id,
        "version": 1,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"cov_{digest[:32]}"
