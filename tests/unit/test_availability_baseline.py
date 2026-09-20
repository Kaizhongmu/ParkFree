from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from parking_ai.availability import (
    AvailabilityDataLeakageError,
    AvailabilityPopulationMismatchError,
    DeterministicAvailabilityBaseline,
    UnsupportedAvailabilityFeatureError,
)
from parking_ai.domain import (
    AvailabilityCapacitySource,
    AvailabilityContext,
    AvailabilityObservationSummary,
    AvailabilityReasonCode,
    AvailabilityTimeBucket,
    FreeState,
    LegalState,
    LineStringGeometry,
    ParkingSegment,
    PhysicalState,
    SearchConstraints,
)
from parking_ai.gis import DeterministicCandidateSegmentService, load_smu_gis_fixture

CHICAGO = ZoneInfo("America/Chicago")
ARRIVAL = datetime(2026, 9, 21, 10, tzinfo=CHICAGO)
PREDICTED_AT = datetime(2026, 9, 20, 12, tzinfo=UTC)


def segment(**updates: object) -> ParkingSegment:
    base = ParkingSegment(
        segment_id="segment-1",
        geometry=LineStringGeometry(coordinates=[(-96.784, 32.842), (-96.783, 32.843)]),
        street_name="Test Street",
        side="LEFT",
        length_m=56.0,
        estimated_capacity=None,
        road_type="residential",
        physical_state=PhysicalState.UNKNOWN,
        data_freshness=PREDICTED_AT,
    )
    return base.model_copy(update=updates)


def context(**updates: object) -> AvailabilityContext:
    base = AvailabilityContext(arrival_time=ARRIVAL)
    return base.model_copy(update=updates)


def service(
    *,
    model_version: str = "availability-heuristic-v0.1.0",
    uncertainty_method: str = "beta-normal-95-v1",
) -> DeterministicAvailabilityBaseline:
    return DeterministicAvailabilityBaseline(
        model_version=model_version,
        uncertainty_method=uncertainty_method,
        clock=lambda: PREDICTED_AT,
    )


def observation(
    *,
    successes: int,
    trials: int,
    as_of: datetime,
    segment_id: str = "segment-1",
    time_bucket: AvailabilityTimeBucket = AvailabilityTimeBucket.WEEKDAY_PEAK,
    target_window_seconds: int = 90,
) -> AvailabilityObservationSummary:
    return AvailabilityObservationSummary(
        segment_id=segment_id,
        time_bucket=time_bucket,
        successes=successes,
        trials=trials,
        target_window_seconds=target_window_seconds,
        as_of=as_of,
    )


def test_missing_data_prior_is_deterministic_and_fully_snapshotted() -> None:
    predictor = service()

    first = predictor.predict_availability(segment(), context())
    second = predictor.predict_availability(segment(), context())

    assert first == second
    assert first.probability == pytest.approx(0.1536)
    assert first.interval is not None
    assert first.interval[0] <= first.probability <= first.interval[1]
    assert first.feature_snapshot is not None
    assert first.feature_snapshot.capacity_source is AvailabilityCapacitySource.LENGTH_FALLBACK
    assert first.feature_snapshot.effective_capacity == pytest.approx(2.0)
    assert first.feature_snapshot.time_bucket is AvailabilityTimeBucket.WEEKDAY_PEAK
    assert first.feature_snapshot.arrival_time_utc == ARRIVAL.astimezone(UTC)
    assert first.reason_codes == [
        AvailabilityReasonCode.HEURISTIC_PRIOR_ONLY,
        AvailabilityReasonCode.MISSING_CAPACITY,
        AvailabilityReasonCode.UNKNOWN_PHYSICAL_STATE,
    ]


def test_explicit_capacity_is_monotonic() -> None:
    predictor = service()

    probabilities = [
        predictor.predict_availability(segment(estimated_capacity=value), context()).probability
        for value in (1.0, 2.0, 5.0, 8.0)
    ]

    assert probabilities == sorted(probabilities)
    assert len(set(probabilities)) == len(probabilities)


@pytest.mark.parametrize(
    ("arrival", "expected"),
    [
        (datetime(2026, 9, 21, 7, 59, tzinfo=CHICAGO), AvailabilityTimeBucket.WEEKDAY_SHOULDER),
        (datetime(2026, 9, 21, 8, 0, tzinfo=CHICAGO), AvailabilityTimeBucket.WEEKDAY_PEAK),
        (datetime(2026, 9, 21, 18, 0, tzinfo=CHICAGO), AvailabilityTimeBucket.WEEKDAY_SHOULDER),
        (datetime(2026, 9, 26, 10, 0, tzinfo=CHICAGO), AvailabilityTimeBucket.WEEKEND_DAY),
        (datetime(2026, 9, 26, 23, 0, tzinfo=CHICAGO), AvailabilityTimeBucket.WEEKEND_NIGHT),
    ],
)
def test_time_bucket_boundaries(
    arrival: datetime,
    expected: AvailabilityTimeBucket,
) -> None:
    result = service().predict_availability(segment(), context(arrival_time=arrival))

    assert result.feature_snapshot is not None
    assert result.feature_snapshot.time_bucket is expected


def test_road_class_penalties_are_transparent_and_monotonic() -> None:
    predictor = service()
    local = predictor.predict_availability(segment(road_type="residential"), context())
    tertiary = predictor.predict_availability(segment(road_type="tertiary"), context())
    secondary = predictor.predict_availability(segment(road_type="secondary"), context())

    assert local.probability > tertiary.probability > secondary.probability


def test_road_class_normalization_ignores_case_and_whitespace() -> None:
    predictor = service()

    canonical = predictor.predict_availability(segment(road_type="residential"), context())
    untidy = predictor.predict_availability(segment(road_type="  ReSiDeNtIaL  "), context())

    assert canonical.probability == untidy.probability
    assert canonical.feature_snapshot is not None
    assert untidy.feature_snapshot is not None
    assert canonical.feature_snapshot.road_type_bucket == untidy.feature_snapshot.road_type_bucket


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"physical_state": PhysicalState.NOT_PARKABLE}, AvailabilityReasonCode.NOT_PARKABLE),
        ({"estimated_capacity": 0.0}, AvailabilityReasonCode.ZERO_ESTIMATED_CAPACITY),
    ],
)
def test_no_physical_capacity_returns_zero_without_touching_legality(
    updates: dict[str, object],
    reason: AvailabilityReasonCode,
) -> None:
    original = segment(legal_state=LegalState.LEGAL, free_state=FreeState.FREE, **updates)
    before = original.model_dump()

    result = service().predict_availability(original, context())

    assert result.probability == 0.0
    assert result.interval == (0.0, 0.0)
    assert reason in result.reason_codes
    assert original.model_dump() == before


def test_regulation_and_previous_prediction_fields_do_not_affect_availability() -> None:
    predictor = service()
    baseline = segment(
        legal_state=LegalState.LEGAL,
        free_state=FreeState.FREE,
        availability_probability=0.99,
        availability_interval=(0.98, 1.0),
    )
    changed = baseline.model_copy(
        update={
            "legal_state": LegalState.ILLEGAL,
            "free_state": FreeState.PAID,
            "legal_confidence": 1.0,
            "availability_probability": 0.01,
            "availability_interval": (0.0, 0.02),
        }
    )

    assert predictor.predict_availability(baseline, context()) == predictor.predict_availability(
        changed, context()
    )


def test_equivalent_instants_normalize_to_the_same_features_and_prediction_id() -> None:
    utc_arrival = ARRIVAL.astimezone(UTC)
    predictor = service()

    local = predictor.predict_availability(segment(), context(arrival_time=ARRIVAL))
    utc = predictor.predict_availability(segment(), context(arrival_time=utc_arrival))

    assert local.probability == utc.probability
    assert local.feature_snapshot == utc.feature_snapshot
    assert local.prediction_id == utc.prediction_id


def test_longer_search_window_monotonically_increases_probability() -> None:
    predictor = service()
    probabilities = [
        predictor.predict_availability(segment(), context(search_window_seconds=window)).probability
        for window in (45, 90, 180)
    ]

    assert probabilities == sorted(probabilities)


def test_sparse_observations_are_shrunk_and_large_sample_narrows_interval() -> None:
    predictor = service()
    as_of = datetime(2026, 9, 19, 12, tzinfo=UTC)
    prior = predictor.predict_availability(segment(), context())
    one_success = predictor.predict_availability(
        segment(),
        context(observation_summary=observation(successes=1, trials=1, as_of=as_of)),
    )
    large_sample = predictor.predict_availability(
        segment(),
        context(observation_summary=observation(successes=80, trials=100, as_of=as_of)),
    )

    assert prior.probability < one_success.probability < 1.0
    assert prior.probability < large_sample.probability < 0.8
    assert prior.interval is not None and large_sample.interval is not None
    assert (
        large_sample.interval[1] - large_sample.interval[0] < prior.interval[1] - prior.interval[0]
    )
    assert AvailabilityReasonCode.SHRUNK_HISTORICAL_DATA in large_sample.reason_codes


def test_observation_window_and_future_data_are_rejected() -> None:
    predictor = service()
    wrong_window = observation(
        successes=1,
        trials=2,
        target_window_seconds=60,
        as_of=datetime(2026, 9, 19, tzinfo=UTC),
    )
    future_data = observation(
        successes=1,
        trials=2,
        as_of=PREDICTED_AT.replace(day=21),
    )

    with pytest.raises(ValueError, match="90-second"):
        predictor.predict_availability(segment(), context(observation_summary=wrong_window))
    with pytest.raises(AvailabilityDataLeakageError):
        predictor.predict_availability(segment(), context(observation_summary=future_data))


def test_observations_must_match_segment_and_time_bucket() -> None:
    predictor = service()
    as_of = datetime(2026, 9, 19, 12, tzinfo=UTC)

    with pytest.raises(AvailabilityPopulationMismatchError, match="segment_id"):
        predictor.predict_availability(
            segment(),
            context(
                observation_summary=observation(
                    successes=1,
                    trials=2,
                    as_of=as_of,
                    segment_id="other-segment",
                )
            ),
        )
    with pytest.raises(AvailabilityPopulationMismatchError, match="time bucket"):
        predictor.predict_availability(
            segment(),
            context(
                observation_summary=observation(
                    successes=1,
                    trials=2,
                    as_of=as_of,
                    time_bucket=AvailabilityTimeBucket.WEEKDAY_NIGHT,
                )
            ),
        )


def test_retrospective_prediction_rejects_observations_after_arrival() -> None:
    retrospective_arrival = datetime(2026, 9, 19, 10, tzinfo=CHICAGO)
    after_arrival_before_prediction = retrospective_arrival.astimezone(UTC).replace(hour=16)

    with pytest.raises(AvailabilityDataLeakageError):
        service().predict_availability(
            segment(),
            context(
                arrival_time=retrospective_arrival,
                observation_summary=observation(
                    successes=1,
                    trials=2,
                    as_of=after_arrival_before_prediction,
                    time_bucket=AvailabilityTimeBucket.WEEKEND_DAY,
                ),
            ),
        )


def test_open_ended_context_features_fail_explicitly() -> None:
    with pytest.raises(UnsupportedAvailabilityFeatureError, match="nearby_event"):
        service().predict_availability(segment(), context(features={"nearby_event": True}))


def test_model_version_changes_content_id_but_not_probability() -> None:
    first = service(model_version="availability-test-v1").predict_availability(segment(), context())
    second = service(model_version="availability-test-v2").predict_availability(
        segment(), context()
    )

    assert first.probability == second.probability
    assert first.prediction_id != second.prediction_id


def test_uncertainty_method_changes_content_id_but_not_probability() -> None:
    first = service(uncertainty_method="band-v1").predict_availability(segment(), context())
    second = service(uncertainty_method="band-v2").predict_availability(segment(), context())

    assert first.probability == second.probability
    assert first.interval == second.interval
    assert first.prediction_id != second.prediction_id


def test_wall_clock_timestamp_is_traceable_but_not_part_of_content_id() -> None:
    first = service().predict_availability(segment(), context())
    later_predictor = DeterministicAvailabilityBaseline(
        clock=lambda: PREDICTED_AT.replace(day=21, hour=14)
    )
    later = later_predictor.predict_availability(segment(), context())

    assert first.predicted_at != later.predicted_at
    assert first.prediction_id == later.prediction_id
    assert first.probability == later.probability


def test_spring_forward_normalization_records_real_offset_and_skipped_hour() -> None:
    before = service().predict_availability(
        segment(), context(arrival_time=datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
    )
    after = service().predict_availability(
        segment(), context(arrival_time=datetime(2026, 3, 8, 8, 30, tzinfo=UTC))
    )

    assert before.feature_snapshot is not None and after.feature_snapshot is not None
    assert (
        before.feature_snapshot.local_hour,
        before.feature_snapshot.local_utc_offset_minutes,
    ) == (
        1,
        -360,
    )
    assert (after.feature_snapshot.local_hour, after.feature_snapshot.local_utc_offset_minutes) == (
        3,
        -300,
    )


def test_fall_back_folds_are_distinct_and_reproducible() -> None:
    first_fold = service().predict_availability(
        segment(), context(arrival_time=datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    )
    second_fold = service().predict_availability(
        segment(), context(arrival_time=datetime(2026, 11, 1, 7, 30, tzinfo=UTC))
    )

    assert first_fold.feature_snapshot is not None and second_fold.feature_snapshot is not None
    assert first_fold.feature_snapshot.local_hour == second_fold.feature_snapshot.local_hour == 1
    assert first_fold.feature_snapshot.local_fold == 0
    assert second_fold.feature_snapshot.local_fold == 1
    assert first_fold.feature_snapshot.local_utc_offset_minutes == -300
    assert second_fold.feature_snapshot.local_utc_offset_minutes == -360
    assert first_fold.prediction_id != second_fold.prediction_id


def test_naive_clock_is_rejected() -> None:
    predictor = DeterministicAvailabilityBaseline(clock=lambda: datetime(2026, 9, 20, 12))

    with pytest.raises(ValueError, match="timezone"):
        predictor.predict_availability(segment(), context())


def test_phase_two_smu_segment_uses_length_fallback() -> None:
    fixture = load_smu_gis_fixture()
    candidate_service = DeterministicCandidateSegmentService(
        fixture.osm.roads,
        evidence_id=fixture.osm.evidence.evidence_id,
        data_freshness=fixture.osm.observed_at,
    )
    smu_segment = candidate_service.get_candidate_segments(
        fixture.destination,
        SearchConstraints(max_walk_minutes=30, max_candidates=1),
    )[0]

    result = service().predict_availability(smu_segment, context())

    assert 0.0 < result.probability < 1.0
    assert result.feature_snapshot is not None
    assert result.feature_snapshot.capacity_source is AvailabilityCapacitySource.LENGTH_FALLBACK
