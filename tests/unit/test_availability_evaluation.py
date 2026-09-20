import math

import pytest

from parking_ai.availability import (
    AvailabilityEvaluationContractError,
    evaluate_availability_predictions,
)
from parking_ai.domain import AvailabilityEvaluationRecord


def record(
    prediction_id: str,
    probability: float,
    outcome: bool,
    interval: tuple[float, float] | None = None,
    *,
    model_version: str = "availability-v1",
    target_window_seconds: int = 90,
    outcome_id: str | None = None,
) -> AvailabilityEvaluationRecord:
    return AvailabilityEvaluationRecord(
        outcome_id=outcome_id or f"outcome-{prediction_id}",
        prediction_id=prediction_id,
        segment_id=f"segment-{prediction_id}",
        model_version=model_version,
        feature_schema_version="features-v1",
        target_window_seconds=target_window_seconds,
        probability=probability,
        outcome=outcome,
        interval=interval,
    )


def test_empty_evaluation_is_explicit() -> None:
    report = evaluate_availability_predictions([])

    assert report.sample_count == 0
    assert report.brier_score is None
    assert report.log_loss is None
    assert report.calibration_bins == []


def test_metrics_match_analytically_obvious_example() -> None:
    report = evaluate_availability_predictions(
        [
            record("low", 0.25, False, (0.1, 0.4)),
            record("high", 0.75, True, (0.6, 0.9)),
        ],
        calibration_bin_count=2,
    )

    assert report.sample_count == 2
    assert report.model_version == "availability-v1"
    assert report.feature_schema_version == "features-v1"
    assert report.target_window_seconds == 90
    assert report.brier_score == pytest.approx(0.0625)
    assert report.log_loss == pytest.approx(-math.log(0.75))
    assert report.expected_calibration_error == pytest.approx(0.25)
    assert report.mean_probability == pytest.approx(0.5)
    assert report.observed_rate == pytest.approx(0.5)
    assert report.mean_interval_width == pytest.approx(0.3)
    assert [item.count for item in report.calibration_bins] == [1, 1]


def test_perfect_endpoint_predictions_have_finite_log_loss() -> None:
    report = evaluate_availability_predictions(
        [record("zero", 0.0, False), record("one", 1.0, True)],
        calibration_bin_count=2,
    )

    assert report.brier_score == 0.0
    assert report.log_loss is not None and report.log_loss < 1e-12
    assert report.expected_calibration_error == 0.0
    assert report.calibration_bins[-1].count == 1


def test_interval_width_uses_only_records_with_intervals() -> None:
    report = evaluate_availability_predictions(
        [record("with", 0.5, True, (0.25, 0.75)), record("without", 0.5, False)]
    )

    assert report.mean_interval_width == pytest.approx(0.5)


def test_bin_count_must_be_positive_even_for_empty_input() -> None:
    with pytest.raises(ValueError, match="positive"):
        evaluate_availability_predictions([], calibration_bin_count=0)


def test_evaluation_rejects_mixed_contracts() -> None:
    with pytest.raises(AvailabilityEvaluationContractError, match="share model"):
        evaluate_availability_predictions(
            [
                record("first", 0.4, False),
                record("second", 0.6, True, model_version="availability-v2"),
            ]
        )


def test_evaluation_rejects_duplicate_outcomes() -> None:
    with pytest.raises(AvailabilityEvaluationContractError, match="outcome_id"):
        evaluate_availability_predictions(
            [
                record("first", 0.4, False, outcome_id="same-outcome"),
                record("second", 0.6, True, outcome_id="same-outcome"),
            ]
        )
