"""Provider-independent deterministic parking regulation evaluation."""

from parking_ai.regulations.engine import (
    DEFAULT_EVIDENCE_MAX_AGE,
    DEFAULT_REGULATION_TIMEZONE,
    RULE_ENGINE_VERSION,
    DeterministicRegulationEngine,
)

__all__ = [
    "DEFAULT_EVIDENCE_MAX_AGE",
    "DEFAULT_REGULATION_TIMEZONE",
    "RULE_ENGINE_VERSION",
    "DeterministicRegulationEngine",
]
