from __future__ import annotations

import math
from collections.abc import Sequence

from parking_ai.domain.schemas import (
    AvailabilityCalibrationBin,
    AvailabilityEvaluationRecord,
    AvailabilityEvaluationReport,
)


class AvailabilityEvaluationContractError(ValueError):
    """Raised when records cannot be evaluated as one versioned target population."""


def evaluate_availability_predictions(
    records: Sequence[AvailabilityEvaluationRecord],
    *,
    calibration_bin_count: int = 10,
) -> AvailabilityEvaluationReport:
    """Evaluate already-labeled predictions without training or database access."""

    if calibration_bin_count <= 0:
        raise ValueError("calibration_bin_count must be positive")
    if not records:
        return AvailabilityEvaluationReport(sample_count=0)

    contract = {
        (item.model_version, item.feature_schema_version, item.target_window_seconds)
        for item in records
    }
    if len(contract) != 1:
        raise AvailabilityEvaluationContractError(
            "evaluation records must share model, feature schema, and target window versions"
        )
    outcome_ids = [item.outcome_id for item in records]
    if len(outcome_ids) != len(set(outcome_ids)):
        raise AvailabilityEvaluationContractError("evaluation outcome_id values must be unique")
    model_version, feature_schema_version, target_window_seconds = next(iter(contract))

    sample_count = len(records)
    squared_errors: list[float] = []
    log_losses: list[float] = []
    probabilities: list[float] = []
    outcomes: list[float] = []
    interval_widths: list[float] = []
    bin_members: list[list[AvailabilityEvaluationRecord]] = [
        [] for _ in range(calibration_bin_count)
    ]

    epsilon = 1e-15
    for record in records:
        outcome = 1.0 if record.outcome else 0.0
        probability = record.probability
        squared_errors.append((probability - outcome) ** 2)
        clipped = max(epsilon, min(probability, 1.0 - epsilon))
        log_losses.append(
            -(outcome * math.log(clipped) + (1.0 - outcome) * math.log(1.0 - clipped))
        )
        probabilities.append(probability)
        outcomes.append(outcome)
        if record.interval is not None:
            interval_widths.append(record.interval[1] - record.interval[0])
        bin_index = min(int(probability * calibration_bin_count), calibration_bin_count - 1)
        bin_members[bin_index].append(record)

    bins: list[AvailabilityCalibrationBin] = []
    expected_calibration_error = 0.0
    for bin_index, members in enumerate(bin_members):
        lower = bin_index / calibration_bin_count
        upper = (bin_index + 1) / calibration_bin_count
        if members:
            mean_probability = sum(item.probability for item in members) / len(members)
            observed_rate = sum(1.0 if item.outcome else 0.0 for item in members) / len(members)
            expected_calibration_error += (
                len(members) / sample_count * abs(mean_probability - observed_rate)
            )
        else:
            mean_probability = None
            observed_rate = None
        bins.append(
            AvailabilityCalibrationBin(
                bin_index=bin_index,
                lower_bound=lower,
                upper_bound=upper,
                count=len(members),
                mean_probability=mean_probability,
                observed_rate=observed_rate,
            )
        )

    return AvailabilityEvaluationReport(
        sample_count=sample_count,
        model_version=model_version,
        feature_schema_version=feature_schema_version,
        target_window_seconds=target_window_seconds,
        brier_score=sum(squared_errors) / sample_count,
        log_loss=sum(log_losses) / sample_count,
        expected_calibration_error=expected_calibration_error,
        mean_probability=sum(probabilities) / sample_count,
        observed_rate=sum(outcomes) / sample_count,
        mean_interval_width=(
            sum(interval_widths) / len(interval_widths) if interval_widths else None
        ),
        calibration_bins=bins,
    )
