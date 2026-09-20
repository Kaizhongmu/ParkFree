"""Provider-independent deterministic availability baseline and evaluation tools."""

from parking_ai.availability.baseline import (
    BASE_TARGET_WINDOW_SECONDS,
    FEATURE_SCHEMA_VERSION,
    MODEL_VERSION,
    UNCERTAINTY_METHOD,
    AvailabilityDataLeakageError,
    AvailabilityPopulationMismatchError,
    DeterministicAvailabilityBaseline,
    UnsupportedAvailabilityFeatureError,
)
from parking_ai.availability.evaluation import (
    AvailabilityEvaluationContractError,
    evaluate_availability_predictions,
)

__all__ = [
    "BASE_TARGET_WINDOW_SECONDS",
    "FEATURE_SCHEMA_VERSION",
    "MODEL_VERSION",
    "UNCERTAINTY_METHOD",
    "AvailabilityDataLeakageError",
    "AvailabilityEvaluationContractError",
    "AvailabilityPopulationMismatchError",
    "DeterministicAvailabilityBaseline",
    "UnsupportedAvailabilityFeatureError",
    "evaluate_availability_predictions",
]
