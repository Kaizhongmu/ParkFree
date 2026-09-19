# Phase 3 deterministic regulation engine

## Scope

Phase 3 implements provider-independent deterministic evaluation for `NO_PARKING`, `TIME_LIMIT`,
`PAID`, `PERMIT_ONLY`, `LOADING`, `STREET_CLEANING`, `EVENT_RESTRICTION`, and `OTHER`. It does not
implement availability prediction, route optimization, an end-to-end search API, live evidence
retrieval, AI extraction, or a frontend.

The public contract remains:

```python
evaluate_legality(segment, user_profile, datetime) -> LegalityEvaluation
```

The requested duration is supplied by `UserProfile.requested_parking_duration_min`. The engine is
constructed with validated `ParkingRule` and `Evidence` snapshots, preserving provider and
persistence independence.

## Deterministic semantics

- Legality (`LEGAL / ILLEGAL / UNKNOWN`) and payment (`FREE / PAID / UNKNOWN`) are resolved as
  separate axes.
- Schedules use `America/Chicago` by default. Input datetimes must be timezone-aware and may use a
  different timezone.
- Requested duration is added on the UTC timeline. Schedule occurrences are created from local
  wall times and then compared in UTC, including DST gaps and repeated hours. A short window
  wholly inside a repeated hour occurs separately in each fold rather than spanning between them.
- Requested stays and rule windows are half-open: `[start, end)`. A stay ending exactly when a
  restriction starts does not overlap it; a stay starting exactly when it ends does not match it.
- Empty `days` means every day. Overnight windows are anchored to their start weekday and start
  effective date. Effective start/end dates are inclusive.
- A prohibition overlapping any part of a stay makes it illegal unless a matching supported
  exception removes that prohibition.
- A time limit is illegal when the requested duration exceeds the applicable maximum. Positive
  legality requires the applicable rule to cover the complete requested stay.
- `PAID` is a designated-parking rule and may establish legality when it covers the full stay.
  Its explicit `payment_required` field independently controls the payment axis.
- `PERMIT_ONLY` requires exact case-insensitive permit-type matching. If the required permit type
  is missing while the user presents a permit, the result remains unknown rather than accepting
  an arbitrary permit.
- `LOADING` is illegal for ordinary vehicles. A vehicle is allowed only through a matching
  structured `VEHICLE_TYPE` exception.
- Street-cleaning and event restrictions are prohibitions while active. `OTHER` never creates
  positive legality; it may only carry explicit payment information.
- Without a requested duration, definite arrival-time prohibitions and payment requirements are
  still returned, but positive `LEGAL` and `FREE` conclusions remain unknown.

## Evidence, conflicts, and confidence

The request is partitioned at every active rule boundary. Evidence tiers are ordered A, B, C, D,
and precedence is applied separately to the legality and payment axes within each atomic time
slice. Lower-tier claims do not override higher-tier conclusions in the same slice, but a
lower-tier restriction in a different slice is not discarded. Direct same-tier contradictions
that cannot be composed deterministically return `UNKNOWN` for the affected axis. An active
prohibition composes with a time limit or permission and makes the stay illegal.

Evidence freshness is deterministic and source-specific. `observed_at` is used when present,
otherwise `retrieved_at` is used. Official code is governed by rule effective dates and has no
automatic age expiry. Default maximum ages are 365 days for official GIS and university data, 180
days for verified signs and OSM, 90 days for web and imagery evidence, and 30 days for community
evidence. Freshness must hold through the requested departure. A stale active source fails closed
to `UNKNOWN`; caller overrides merge into the defaults for a different jurisdiction. Evidence
observed or retrieved after the requested arrival also fails closed, preventing future
information from authorizing a historical request.

Every result includes typed reason codes and deterministic, sorted evidence references. The
reported confidence is the minimum extraction confidence among the evidence selected for known
axes. It is `0.0` for unresolved conflicts or when neither axis is known; it is evidence metadata,
not a probability of legal correctness.

The engine copies rules/evidence at construction, validates unique identifiers and complete
provenance links, and derives a stable evaluation ID from the query and normalized output. It has
no hidden mutable state and does not write to the database.

## Persistence and migrations

The existing `parking_rules` and `parking_sources` schema contains all Phase 3 inputs. No database
column, persisted enum, or relationship changed, so Phase 3 adds no Alembic migration. Evaluation
is contextual to time, duration, vehicle, permits, and engine version; it is therefore not written
back to the canonical Phase 2 `street_segments` row.

The PostgreSQL/PostGIS gate still runs the existing migration from a fresh dedicated database and
the Phase 1/2 persistence tests to verify that the schema and GIS upsert guarantees remain intact.

## Verification commands

```text
.venv/bin/pytest -q tests/unit/test_regulation_engine.py
.venv/bin/pytest -m "not integration"
TEST_DATABASE_URL=<dedicated-postgis-url> .venv/bin/pytest -m integration
TEST_DATABASE_URL=<dedicated-postgis-url> .venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
```

The truth-table suite covers weekday/weekend schedules, exact boundaries, overnight rules,
effective dates, interval crossings, overlaps, duration limits, permits, payment windows, explicit
free evidence, conflicts, insufficient coverage, authority tiers, provenance, input-order
independence, timezone validation, and both DST transition directions.
