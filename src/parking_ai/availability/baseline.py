from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections.abc import Callable
from typing import Final
from zoneinfo import ZoneInfo

from parking_ai.domain.enums import (
    AvailabilityCapacitySource,
    AvailabilityObservationScope,
    AvailabilityReasonCode,
    AvailabilityTimeBucket,
    DayOfWeek,
    PhysicalState,
)
from parking_ai.domain.schemas import (
    AvailabilityContext,
    AvailabilityFeatureSnapshot,
    AvailabilityPrediction,
    ParkingSegment,
)

MODEL_VERSION: Final[str] = "availability-heuristic-v0.1.0"
FEATURE_SCHEMA_VERSION: Final[str] = "availability-features-v1"
UNCERTAINTY_METHOD: Final[str] = "beta-normal-95-v1"
DEFAULT_FEATURE_TIMEZONE: Final[dt.tzinfo] = ZoneInfo("America/Chicago")
BASE_TARGET_WINDOW_SECONDS: Final[int] = 90

_SPACE_PRIOR_BY_BUCKET = {
    AvailabilityTimeBucket.WEEKDAY_PEAK: 0.08,
    AvailabilityTimeBucket.WEEKDAY_SHOULDER: 0.14,
    AvailabilityTimeBucket.WEEKDAY_NIGHT: 0.22,
    AvailabilityTimeBucket.WEEKEND_DAY: 0.16,
    AvailabilityTimeBucket.WEEKEND_NIGHT: 0.24,
}
_ROAD_MULTIPLIER = {
    "local": 1.0,
    "unclassified": 0.85,
    "tertiary": 0.70,
    "secondary": 0.60,
    "other": 0.80,
}
_DAY_BY_WEEKDAY = {
    0: DayOfWeek.MON,
    1: DayOfWeek.TUE,
    2: DayOfWeek.WED,
    3: DayOfWeek.THU,
    4: DayOfWeek.FRI,
    5: DayOfWeek.SAT,
    6: DayOfWeek.SUN,
}


class UnsupportedAvailabilityFeatureError(ValueError):
    """Raised when V0 receives open-ended context features it cannot reproduce."""


class AvailabilityDataLeakageError(ValueError):
    """Raised when an observation summary contains information from after prediction cutoff."""


class AvailabilityPopulationMismatchError(ValueError):
    """Raised when historical observations do not describe this segment and feature bucket."""


class DeterministicAvailabilityBaseline:
    """Transparent V0 availability prior with optional Beta shrinkage.

    The service predicts availability only. It deliberately ignores regulation states and prior
    prediction fields, performs no I/O, and never mutates the supplied segment or context.
    """

    def __init__(
        self,
        *,
        model_version: str = MODEL_VERSION,
        uncertainty_method: str = UNCERTAINTY_METHOD,
        timezone: dt.tzinfo = DEFAULT_FEATURE_TIMEZONE,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        if not model_version:
            raise ValueError("model_version must not be empty")
        if not uncertainty_method:
            raise ValueError("uncertainty_method must not be empty")
        self._model_version = model_version
        self._uncertainty_method = uncertainty_method
        self._timezone = timezone
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def uncertainty_method(self) -> str:
        return self._uncertainty_method

    def predict_availability(
        self,
        segment: ParkingSegment,
        context: AvailabilityContext,
    ) -> AvailabilityPrediction:
        if context.features:
            keys = ", ".join(sorted(context.features))
            raise UnsupportedAvailabilityFeatureError(
                f"V0 does not consume open-ended availability features: {keys}"
            )

        predicted_at = _require_aware(self._clock(), "clock result").astimezone(dt.UTC)
        arrival_utc = context.arrival_time.astimezone(dt.UTC)
        local_arrival = arrival_utc.astimezone(self._timezone)
        local_offset = local_arrival.utcoffset()
        if local_offset is None:
            raise ValueError("availability timezone must provide a UTC offset")

        bucket = _time_bucket(local_arrival)
        road_bucket = _road_bucket(segment.road_type)
        effective_capacity, capacity_source = _effective_capacity(segment)
        prior_strength = _prior_strength(segment, capacity_source)
        per_space_prior = _clamp(
            _SPACE_PRIOR_BY_BUCKET[bucket] * _ROAD_MULTIPLIER[road_bucket],
            0.01,
            0.50,
        )
        prior_90 = 1.0 - (1.0 - per_space_prior) ** effective_capacity

        reasons: set[AvailabilityReasonCode] = set()
        if capacity_source is AvailabilityCapacitySource.LENGTH_FALLBACK:
            reasons.add(AvailabilityReasonCode.MISSING_CAPACITY)
        if segment.physical_state is PhysicalState.UNKNOWN:
            reasons.add(AvailabilityReasonCode.UNKNOWN_PHYSICAL_STATE)

        summary = context.observation_summary
        successes = 0
        trials = 0
        observation_as_of: dt.datetime | None = None
        observation_scope: AvailabilityObservationScope | None = None
        observation_aggregation_version: str | None = None
        if summary is not None:
            if summary.target_window_seconds != BASE_TARGET_WINDOW_SECONDS:
                raise ValueError("V0 observation summaries must target the 90-second event")
            if summary.segment_id != segment.segment_id:
                raise AvailabilityPopulationMismatchError(
                    "observation summary segment_id does not match prediction segment"
                )
            if summary.scope is not AvailabilityObservationScope.SEGMENT_TIME_BUCKET:
                raise AvailabilityPopulationMismatchError(
                    "V0 requires segment-and-time-bucket observation scope"
                )
            if summary.time_bucket is not bucket:
                raise AvailabilityPopulationMismatchError(
                    "observation summary time bucket does not match prediction features"
                )
            cutoff = min(predicted_at, arrival_utc)
            if summary.as_of.astimezone(dt.UTC) > cutoff:
                raise AvailabilityDataLeakageError(
                    "observation summary must not include data after the prediction cutoff"
                )
            successes = summary.successes
            trials = summary.trials
            observation_as_of = summary.as_of.astimezone(dt.UTC)
            observation_scope = summary.scope
            observation_aggregation_version = summary.aggregation_version

        snapshot = AvailabilityFeatureSnapshot(
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            arrival_time_utc=arrival_utc,
            local_timezone=_timezone_name(self._timezone),
            local_weekday=_DAY_BY_WEEKDAY[local_arrival.weekday()],
            local_hour=local_arrival.hour,
            local_utc_offset_minutes=int(local_offset.total_seconds() // 60),
            local_fold=local_arrival.fold,
            time_bucket=bucket,
            search_window_seconds=context.search_window_seconds,
            segment_length_m=segment.length_m,
            effective_capacity=effective_capacity,
            capacity_source=capacity_source,
            road_type_bucket=road_bucket,
            physical_state=segment.physical_state,
            observation_successes=successes,
            observation_trials=trials,
            observation_as_of=observation_as_of,
            observation_scope=observation_scope,
            observation_aggregation_version=observation_aggregation_version,
            prior_90_probability=_rounded(prior_90),
            prior_strength=prior_strength,
        )

        if segment.physical_state is PhysicalState.NOT_PARKABLE:
            probability = 0.0
            interval = (0.0, 0.0)
            reasons.add(AvailabilityReasonCode.NOT_PARKABLE)
        elif segment.estimated_capacity == 0:
            probability = 0.0
            interval = (0.0, 0.0)
            reasons.add(AvailabilityReasonCode.ZERO_ESTIMATED_CAPACITY)
        else:
            alpha = prior_strength * prior_90 + successes
            beta = prior_strength * (1.0 - prior_90) + trials - successes
            probability_90 = alpha / (alpha + beta)
            sigma = math.sqrt(alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1.0)))
            interval_90 = (
                _clamp(probability_90 - 1.96 * sigma, 0.0, 1.0),
                _clamp(probability_90 + 1.96 * sigma, 0.0, 1.0),
            )
            probability = _window_transform(probability_90, context.search_window_seconds)
            interval = (
                _window_transform(interval_90[0], context.search_window_seconds),
                _window_transform(interval_90[1], context.search_window_seconds),
            )
            reasons.add(
                AvailabilityReasonCode.SHRUNK_HISTORICAL_DATA
                if trials
                else AvailabilityReasonCode.HEURISTIC_PRIOR_ONLY
            )

        if context.search_window_seconds != BASE_TARGET_WINDOW_SECONDS:
            reasons.add(AvailabilityReasonCode.SEARCH_WINDOW_ADJUSTED)

        rounded_probability = _rounded(probability)
        rounded_interval = (_rounded(interval[0]), _rounded(interval[1]))
        reason_codes = [reason for reason in AvailabilityReasonCode if reason in reasons]
        prediction_id = _prediction_id(
            segment.segment_id,
            snapshot,
            rounded_probability,
            rounded_interval,
            reason_codes,
            self._model_version,
            self._uncertainty_method,
        )
        return AvailabilityPrediction(
            prediction_id=prediction_id,
            segment_id=segment.segment_id,
            probability=rounded_probability,
            interval=rounded_interval,
            model_version=self._model_version,
            predicted_at=predicted_at,
            feature_snapshot=snapshot,
            uncertainty_method=self._uncertainty_method,
            reason_codes=reason_codes,
        )


def _effective_capacity(
    segment: ParkingSegment,
) -> tuple[float, AvailabilityCapacitySource]:
    if segment.estimated_capacity is not None:
        return segment.estimated_capacity, AvailabilityCapacitySource.EXPLICIT
    # Phase 2 segments represent one curb side. One conservative effective opportunity per 28 m,
    # bounded to keep incomplete curb/driveway data from producing overconfident priors.
    return _clamp(segment.length_m / 28.0, 1.0, 4.0), AvailabilityCapacitySource.LENGTH_FALLBACK


def _prior_strength(
    segment: ParkingSegment,
    capacity_source: AvailabilityCapacitySource,
) -> float:
    known_capacity = capacity_source is AvailabilityCapacitySource.EXPLICIT
    known_parkability = segment.physical_state is PhysicalState.PARKABLE
    if known_capacity and known_parkability:
        return 6.0
    if known_capacity or known_parkability:
        return 3.0
    return 2.0


def _time_bucket(local_arrival: dt.datetime) -> AvailabilityTimeBucket:
    is_weekend = local_arrival.weekday() >= 5
    hour = local_arrival.hour
    if is_weekend:
        return (
            AvailabilityTimeBucket.WEEKEND_DAY
            if 8 <= hour < 22
            else AvailabilityTimeBucket.WEEKEND_NIGHT
        )
    if 8 <= hour < 18:
        return AvailabilityTimeBucket.WEEKDAY_PEAK
    if 6 <= hour < 8 or 18 <= hour < 22:
        return AvailabilityTimeBucket.WEEKDAY_SHOULDER
    return AvailabilityTimeBucket.WEEKDAY_NIGHT


def _road_bucket(road_type: str | None) -> str:
    normalized = " ".join(road_type.split()).casefold() if road_type is not None else None
    if normalized in {"residential", "living_street"}:
        return "local"
    if normalized in {"unclassified", "tertiary", "secondary"}:
        return normalized
    return "other"


def _window_transform(probability_90: float, window_seconds: int) -> float:
    return float(1.0 - (1.0 - probability_90) ** (window_seconds / BASE_TARGET_WINDOW_SECONDS))


def _prediction_id(
    segment_id: str,
    snapshot: AvailabilityFeatureSnapshot,
    probability: float,
    interval: tuple[float, float],
    reasons: list[AvailabilityReasonCode],
    model_version: str,
    uncertainty_method: str,
) -> str:
    payload = {
        "feature_snapshot": snapshot.model_dump(mode="json"),
        "interval": list(interval),
        "model_version": model_version,
        "probability": probability,
        "reason_codes": [reason.value for reason in reasons],
        "segment_id": segment_id,
        "uncertainty_method": uncertainty_method,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"pred_{digest[:32]}"


def _timezone_name(timezone: dt.tzinfo) -> str:
    return getattr(timezone, "key", str(timezone))


def _require_aware(value: dt.datetime, label: str) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include timezone information")
    return value


def _rounded(value: float) -> float:
    return round(_clamp(value, 0.0, 1.0), 12)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))
