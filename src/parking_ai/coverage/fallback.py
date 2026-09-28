"""Composable failover for provider-independent road acquisition."""

from parking_ai.coverage.models import (
    CoverageAttemptOutcome,
    CoverageAttemptRole,
    CoverageProviderAttempt,
    RoadAcquisition,
    RoadCoverageError,
    RoadCoverageExhaustedError,
    RoadCoverageProvider,
)
from parking_ai.domain import Destination


class FailoverRoadCoverageProvider:
    """Use the fallback for typed failures or an empty primary road snapshot."""

    def __init__(
        self,
        primary: RoadCoverageProvider,
        fallback: RoadCoverageProvider,
        *,
        primary_name: str,
        fallback_name: str,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name

    def acquire(
        self,
        destination: Destination,
        max_walk_minutes: float,
    ) -> RoadAcquisition:
        try:
            primary_acquisition = self._primary.acquire(destination, max_walk_minutes)
        except RoadCoverageError:
            primary_attempt = CoverageProviderAttempt(
                provider_name=self._primary_name,
                role=CoverageAttemptRole.PRIMARY,
                outcome=CoverageAttemptOutcome.FAILED,
            )
            return self._acquire_fallback(
                destination,
                max_walk_minutes,
                primary_attempt=primary_attempt,
                empty_primary=None,
            )
        if not primary_acquisition.roads:
            primary_attempt = CoverageProviderAttempt(
                provider_name=self._primary_name,
                role=CoverageAttemptRole.PRIMARY,
                outcome=CoverageAttemptOutcome.EMPTY,
            )
            return self._acquire_fallback(
                destination,
                max_walk_minutes,
                primary_attempt=primary_attempt,
                empty_primary=primary_acquisition,
            )
        attempts = primary_acquisition.provider_attempts or (
            CoverageProviderAttempt(
                provider_name=self._primary_name,
                role=CoverageAttemptRole.PRIMARY,
                outcome=CoverageAttemptOutcome.SUCCEEDED,
            ),
        )
        return primary_acquisition.model_copy(update={"provider_attempts": attempts}, deep=True)

    def _acquire_fallback(
        self,
        destination: Destination,
        max_walk_minutes: float,
        *,
        primary_attempt: CoverageProviderAttempt,
        empty_primary: RoadAcquisition | None,
    ) -> RoadAcquisition:
        try:
            fallback_acquisition = self._fallback.acquire(destination, max_walk_minutes)
        except RoadCoverageError as fallback_error:
            attempts = (
                primary_attempt,
                CoverageProviderAttempt(
                    provider_name=self._fallback_name,
                    role=CoverageAttemptRole.FALLBACK,
                    outcome=CoverageAttemptOutcome.FAILED,
                ),
            )
            if empty_primary is not None:
                return empty_primary.model_copy(update={"provider_attempts": attempts}, deep=True)
            raise RoadCoverageExhaustedError(attempts) from fallback_error
        fallback_attempts = fallback_acquisition.provider_attempts or (
            CoverageProviderAttempt(
                provider_name=self._fallback_name,
                role=CoverageAttemptRole.FALLBACK,
                outcome=(
                    CoverageAttemptOutcome.SUCCEEDED
                    if fallback_acquisition.roads
                    else CoverageAttemptOutcome.EMPTY
                ),
            ),
        )
        return fallback_acquisition.model_copy(
            update={"provider_attempts": (primary_attempt, *fallback_attempts)},
            deep=True,
        )
