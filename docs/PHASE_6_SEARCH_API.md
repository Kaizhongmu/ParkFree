# Phase 6 — End-to-end parking search API

Phase 6 composes the completed Phase 2–5 services into one deterministic, replayable request. It
does not add a frontend, live routing, live OSM retrieval, model training, or AI extraction.

## Request and response

`POST /v1/parking/search` accepts:

- an origin latitude/longitude;
- exactly one destination query or destination ID;
- an aware `arrival_time` or the literal `now`;
- a required parking duration from 1 through 1,440 minutes;
- `free_only`, a walking limit greater than 0 and at most 30 minutes, and at most 20 candidates;
- vehicle type and zero or more permit types.

Unknown fields, naive timestamps, coercive booleans, invalid bounds, duplicate permits, and blank
selectors are rejected at the HTTP boundary. `Idempotency-Key` is optional, trimmed, nonblank, and
limited to 128 characters.

The response contains the resolved destination and arrival, every candidate's legality/payment
decision, optional availability prediction, exclusion reason, explicit unknown IDs, the selected
contingent route, guaranteed fallback, warnings, and all rule/model/matrix/optimizer versions.

## Deterministic flow

1. Resolve the persisted destination and access points.
2. Read persisted Phase 2 segments, apply the Phase 2 walking constraint, and sort deterministically.
3. Load normalized rules/evidence for exactly those segments and evaluate the whole requested stay.
4. Exclude `ILLEGAL` and `UNKNOWN` legality. Exclude unknown payment. When `free_only=true`, also
   exclude `PAID`.
5. Predict availability only for the remaining candidates.
6. Build a request-bound local matrix and run the existing deterministic optimizer.
7. Persist the complete execution in the same database transaction, then return the response.

Candidate ordering, IDs, evaluation IDs, prediction IDs, matrix IDs, route IDs, warnings, and
snapshot hashes are deterministic for equivalent content. Session IDs and observation timestamps
remain execution identifiers; an idempotency retry replays the original execution.

## Matrix and fallback

`local-straight-line-matrix-v1` is an offline adapter. It computes great-circle distances between
the origin, arithmetic center of each LineString, and fallback, then divides by
`LOCAL_DRIVING_SPEED_M_PER_MIN` (default 400). It is reproducible, but it is not street routing.
Every response includes `LOCAL_STRAIGHT_LINE_ROUTE_COST_APPROXIMATION`.

Search is unavailable until both `PARKING_FALLBACK_LATITUDE` and
`PARKING_FALLBACK_LONGITUDE` are configured. `PARKING_FALLBACK_ID` and
`PARKING_FALLBACK_DESCRIPTION` must identify a location that the operator has verified actually
guarantees parking. No bundled coordinate is claimed to do so.

## Unknown and fixture policy

The local SMU OSM fixture has ODbL provenance documented in `PHASE_2_GIS.md`; it provides road
geometry, not legal parking rules. Phase 6 does not infer legality or free parking from the
absence of a rule, an OSM highway tag, or a stale database column. With only the bundled fixture,
all candidate decisions are `UNKNOWN`, no availability prediction is made, and the optimizer
returns the configured fallback. Tests use synthetic regulation records with test-only source
identifiers.

## Persistence and idempotency

Migration `0002_phase6_search_replay` adds nullable snapshot fields to `search_sessions`, preserving
old rows with `replayable=false`, and adds nullable decision IDs/window metadata to route steps.
New Phase 6 sessions are marked replayable only when every required snapshot and version is present.

Only the SHA-256 digest of an idempotency key is stored. The request hash includes the normalized
command; the logical token `now` is retained for hash comparison while the first resolved arrival
is stored and replayed. The aggregate artifact hash covers candidate decisions, exact route matrix,
route, and response. Replay revalidates schemas and cross-checks hashes, versions, IDs, metadata,
matrix binding, and normalized route-step rows.

## Commands

Run database-independent checks:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pytest -q -m 'not integration'
```

Run migrations and all tests against a dedicated PostGIS database:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/DEDICATED_TEST_DB'
.venv/bin/alembic upgrade head
.venv/bin/pytest -q
```

The integration fixture downgrades the dedicated test database to `base`, migrates it to `head`,
runs geometry/migration/persistence/API checks, and downgrades it afterward. Never point it at a
database containing data that must be retained.

## Current phase boundary

Phase 6 is the current implemented phase. There is no Phase 7 map UI, Phase 8 AI/LLM extraction,
live OSM or routing call, paid service, regulation administration API, outcome API, or learned
availability model in this phase.
