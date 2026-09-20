# ADR 0003: Deterministic availability baseline semantics

## Status

Accepted for Phase 4.

## Context

Phase 4 needs a reproducible probability and uncertainty output before local training data or live
context providers exist. Availability must remain separate from legal/payment decisions, and a
later optimizer needs versioned, auditable features rather than an opaque score. Phase 2 segments
also lack curated capacity and curb-feasibility data, so missing inputs must not be hidden.

## Decision

Implement a pure V0 predictor behind the existing
`AvailabilityService.predict_availability(segment, context)` contract. Treat its probability as
conditional on legality having been established upstream; ignore all legality, payment, and prior
prediction fields. Do not perform I/O, mutate the segment, or persist contextual predictions.

Build a transparent 90-second heuristic from a local time bucket, road-class multiplier, and
effective one-sided capacity. Use explicit capacity when supplied; otherwise use a conservative,
bounded length fallback and disclose that fallback in the snapshot and reason codes. Return zero
only for explicit zero capacity or `NOT_PARKABLE`, not merely for unknown physical state.

Accept optional historical evidence only as a typed success/trial summary bound to the same
segment, local time bucket, target event, and versioned aggregation scope. Apply deterministic
Beta shrinkage around the heuristic prior and return a versioned normal approximation band. Reject
population mismatches, future-dated summaries, and untyped open-ended context features. Convert
other search windows through a documented constant-hazard transform.

Persist the complete normalized feature snapshot in the output value and derive a stable
prediction ID from the segment ID, snapshot, output, reason codes, model version, and uncertainty
method. Keep the wall-clock prediction timestamp out of that content ID. Supply a read-only
evaluation harness for Brier score, clipped log loss, calibration error, calibration bins, and
interval width. Require every nonempty evaluation batch to share its model version, feature-schema
version, and target window, and reject duplicate outcome IDs.

## Consequences

- The V0 score is reproducible and explainable, but its coefficients and interval are uncalibrated
  assumptions until real outcomes exist.
- Callers must evaluate legality separately before treating a predicted opportunity as legal.
- Missing capacity and curb state reduce prior strength and remain explicit.
- Provider features require a future typed schema, provenance, timestamp policy, and version
  change rather than silently entering `context.features`.
- Phase 4 needs no database migration and cannot overwrite GIS or regulation-owned state.
- Training, calibration fitting, live providers, routing, and API orchestration remain later-phase
  work.
