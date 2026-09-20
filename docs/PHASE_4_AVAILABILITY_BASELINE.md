# Phase 4 availability baseline

## Scope and event definition

Phase 4 implements a deterministic, provider-independent V0 availability service behind the
existing contract:

```python
predict_availability(segment, context) -> AvailabilityPrediction
```

The output estimates the chance of finding at least one physically available parking opportunity
on the segment within the configured search window, conditional on the caller having already
established that the segment is legal for the requested stay. The predictor never interprets
parking rules and deliberately ignores `legal_state`, `free_state`, legal confidence, and any
previous availability value stored on a segment. It performs no database or network I/O and does
not mutate its inputs.

This phase does not add model training, live observations, weather or event providers, prediction
persistence, route optimization, an end-to-end search API, or a frontend.

## V0 heuristic

Arrival instants are converted to `America/Chicago` by default and assigned to one transparent
time bucket. The starting per-space 90-second priors are:

| Time bucket | Local time | Prior |
| --- | --- | ---: |
| Weekday peak | 08:00–18:00 | 0.08 |
| Weekday shoulder | 06:00–08:00, 18:00–22:00 | 0.14 |
| Weekday night | 22:00–06:00 | 0.22 |
| Weekend day | 08:00–22:00 | 0.16 |
| Weekend night | 22:00–08:00 | 0.24 |

The prior is multiplied by `1.00` for local roads (`residential` and `living_street`), `0.85` for
`unclassified`, `0.70` for `tertiary`, `0.60` for `secondary`, and `0.80` for all other or missing
road classes. These coefficients are assumptions, not learned SMU calibration results.

An explicit nonnegative `estimated_capacity` is used when present. Otherwise V0 uses one
effective opportunity per 28 metres of the Phase 2 one-sided segment, clamped to `[1, 4]`. The
segment prior is:

```text
p90 = 1 - (1 - per_space_prior) ** effective_capacity
```

`NOT_PARKABLE` and explicit zero-capacity segments produce zero. Missing capacity and unknown
physical state remain visible as typed reason codes rather than being silently treated as known.

## Optional observation summaries and uncertainty

The only historical input accepted by V0 is a typed aggregate of successes and trials for the
same segment, local time bucket, and 90-second target event. Each summary declares its segment ID,
`SEGMENT_TIME_BUCKET` scope, bucket, and aggregation version; mismatches are rejected. Open-ended
`context.features` are rejected because their definitions, timestamps, and provenance cannot be
reproduced safely. Observation summaries dated after either the prediction time or requested
arrival are rejected to prevent look-ahead.

V0 applies Beta shrinkage around the heuristic prior. Prior strength is `6` when explicit capacity
and parkability are both known, `3` when one is known, and `2` when neither is known. The interval
is a deterministic, clipped normal approximation around the resulting Beta mean. It is labeled
`beta-normal-95-v1`; it is a heuristic uncertainty band, not a validated frequentist confidence
interval.

For a search window other than 90 seconds, the point estimate and interval endpoints use:

```text
p(window) = 1 - (1 - p90) ** (window_seconds / 90)
```

This assumes independent, constant opportunities over time. The reason code
`SEARCH_WINDOW_ADJUSTED` makes the assumption observable.

## Reproducibility and versioning

Every prediction includes:

- model version `availability-heuristic-v0.1.0`;
- feature schema version `availability-features-v1`;
- normalized UTC arrival plus local weekday, hour, UTC offset, and DST fold;
- the capacity source, road bucket, physical state, prior, prior strength, and observation cutoff;
- typed reason codes and uncertainty method;
- a content-derived prediction ID that also includes the uncertainty-method version.

The content ID is independent of the wall-clock prediction timestamp. Equivalent arrival instants
in different timezone representations produce the same snapshot and ID. Changing the model
version changes the ID. The injected clock exists for deterministic tests and for a traceable
`predicted_at` timestamp.

## Evaluation harness

`evaluate_availability_predictions` accepts already-labeled, validated records and reports sample
count, Brier score, epsilon-clipped log loss, equal-width-bin expected calibration error, mean
prediction, observed success rate, mean available interval width, and the calibration bins. Each
record carries a unique outcome ID, segment ID, model version, feature-schema version, and target
window. Duplicate outcomes and mixed evaluation contracts are rejected instead of being silently
pooled. The harness is read-only and does not train or tune the baseline. Empty input returns an
explicit zero-sample report with unavailable metrics.

## Persistence and migrations

Phase 4 adds no database schema or Alembic migration. Predictions are contextual and are returned
as typed values; they are not written back to the GIS-owned `street_segments` rows. Existing
availability columns remain compatible with later search-session snapshots, but this phase does
not define their persistence lifecycle.

## Verification commands

```text
.venv/bin/pytest -q tests/unit/test_availability_baseline.py
.venv/bin/pytest -q tests/unit/test_availability_evaluation.py
.venv/bin/pytest -m "not integration"
TEST_DATABASE_URL=<dedicated-postgis-url> .venv/bin/pytest -m integration
TEST_DATABASE_URL=<dedicated-postgis-url> .venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
```

Tests cover deterministic repeatability, model-version sensitivity, time-bucket boundaries,
timezone and DST-fold normalization, road-class normalization, capacity and road effects,
physical-state handling, search-window
monotonicity, shrinkage, uncertainty narrowing, look-ahead rejection, feature rejection,
population-scope validation, non-mutation, independence from legal/payment state, compatibility
with an actual Phase 2 SMU segment, and analytically checkable version-homogeneous evaluation
metrics.
