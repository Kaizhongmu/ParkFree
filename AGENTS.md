# AGENTS.md — Parking Intelligence System

This file defines mandatory instructions for any coding agent working in this repository.

## 1. Read before editing

Before implementing any feature:

1. Read `docs/IMPLEMENTATION_SPEC.md`.
2. Read relevant existing source files and tests.
3. Read `docs/assumptions.md` if it exists.
4. Read relevant ADRs under `docs/architecture_decisions/` if they exist.

Treat `docs/IMPLEMENTATION_SPEC.md` as the authoritative product and architecture specification unless the user explicitly overrides it.

---

## 2. Core architecture rules

1. Legality and parking availability are separate concepts.
2. `LEGAL / ILLEGAL / UNKNOWN` is decided by the deterministic regulation engine.
3. `FREE / PAID / UNKNOWN` is stored separately from legality.
4. Statistical/ML models predict parking availability, not legality.
5. LLMs/AI extract structured evidence; they do not make final authoritative legal decisions.
6. GIS owns spatial representation and candidate-segment generation.
7. The route optimizer owns search order.
8. Routing providers only provide travel costs / route matrices.
9. All external providers must be hidden behind adapters.
10. Core domain models must remain provider-independent.
11. AI outputs must be validated against schemas before use.
12. Unknown or conflicting regulation evidence must remain explicit.
13. Every material legal/free conclusion must retain provenance.
14. Model, rule-engine, and optimizer versions must be traceable.

---

## 3. Stable public interfaces

Preserve these conceptual interfaces:

```python
get_candidate_segments(destination, search_constraints) -> list[ParkingSegment]
```

```python
evaluate_legality(segment, user_profile, datetime) -> LegalityEvaluation
```

```python
predict_availability(segment, context) -> AvailabilityPrediction
```

```python
plan_search_route(candidates, origin, destination, route_matrix) -> SearchRoute
```

Do not rename, remove, or materially alter these contracts without explicitly flagging the change and obtaining approval.

---

## 4. Development strategy

Implement one phase at a time.

```text
Phase 0 — repository/infrastructure
Phase 1 — domain/data layer
Phase 2 — SMU candidate-segment GIS slice
Phase 3 — regulation engine
Phase 4 — availability baseline
Phase 5 — route matrix + optimizer
Phase 6 — end-to-end API
Phase 7 — minimal map UI
Phase 8 — AI evidence services
```

Only implement the phase(s) explicitly requested in the current task.

Do not preemptively add future-phase frameworks or dependencies unless they are required to preserve a clean contract.

---

## 5. Working procedure for every task

Before code changes:

1. Inspect repository structure.
2. Inspect relevant tests.
3. Identify affected public interfaces.
4. State a concise implementation plan.
5. Note any assumption that materially affects architecture.

During implementation:

1. Keep changes scoped.
2. Add or update tests.
3. Add database migrations where needed.
4. Use typed schemas.
5. Keep provider-specific logic isolated.
6. Avoid hidden global mutable state.
7. Use timezone-aware datetimes.
8. Keep deterministic logic deterministic.

Before considering the task complete:

1. Run relevant unit tests.
2. Run relevant integration tests.
3. Run formatting/linting/type checks if configured.
4. Fix failures caused by the change.
5. Report all commands/tests executed.
6. Report changed files.
7. Report assumptions.
8. Report unresolved product/architecture decisions.

---

## 6. Data-model rules

The canonical spatial unit is `ParkingSegment`.

A segment must have a stable persistent ID. Do not use list position, dataframe index, or database row order as a permanent ID.

Core entities should include, as the project matures:

```text
Destination
DestinationAccessPoint
ParkingSegment
ParkingRule
Evidence
LegalityEvaluation
AvailabilityPrediction
SearchRoute
SearchRouteStep
SearchSession
ParkingOutcome
```

Use Pydantic for validation, SQLAlchemy for persistence, Alembic for migrations, and PostgreSQL/PostGIS for persistent spatial storage.

---

## 7. Regulation-engine rules

The deterministic regulation engine must preserve:

```text
LEGAL
ILLEGAL
UNKNOWN
```

and:

```text
FREE
PAID
UNKNOWN
```

Never convert missing information into `LEGAL`.
Never convert missing payment information into `FREE`.

Tests must include time boundaries, weekday/weekend, overlapping rules, permits, paid periods, expired rules, contradictory evidence, unknown evidence, parking duration crossing rule boundaries, and timezone/DST cases.

---

## 8. AI-agent rules

AI/LLM modules are bounded extractors.

Allowed responsibilities:

- parse municipal text;
- parse parking webpages/documents;
- extract parking restrictions;
- interpret community reports;
- parse parking signs;
- normalize evidence into structured records.

Forbidden responsibilities:

- final authoritative legality decision;
- direct mutation of another module's result;
- route optimization;
- replacing deterministic time/math/GIS logic;
- returning unvalidated prose as machine state.

Every AI output must have a schema, be validated, retain source references, include extractor/model version where applicable, and be rejected/quarantined on schema failure.

---

## 9. Availability-model rules

The initial target event is conceptually:

> The user reaches a segment and finds at least one legal space within the configured search window.

Availability output must include probability, uncertainty/interval when available, and model version.

Calibration matters because optimizer decisions consume probabilities.

Keep model training separate from online inference.

---

## 10. Routing and optimizer rules

Routing provider output is an input to our optimizer.

Do not let an external routing provider become the parking decision engine.

The optimizer should eventually minimize expected time-to-park.

V0 may use transparent greedy logic. V1 should support beam search over candidate sequences.

Write synthetic tests where the best route is analytically obvious.

---

## 11. Provider-adapter rules

All external systems must be behind adapters, for example:

```text
OSMAdapter
DallasCityAdapter
UniversityAdapter
RoutingAdapter
WeatherAdapter
EventsAdapter
CommunitySourceAdapter
StreetViewAdapter
```

Core code should not depend on provider-specific response formats.

Each adapter should have an explicit policy for caching, retention, persistent storage, attribution, timeouts, retry behavior, and freshness.

Do not scrape or persist provider content without an explicit policy.

---

## 12. Observability and reproducibility

Persist or log enough information to reconstruct important decisions.

For a search session, retain when practical:

```text
query
destination resolution
candidate segment IDs
legality evaluations
availability model version
availability predictions
route-matrix provider/version
optimizer version
chosen route
fallback
timestamp
```

Avoid logging secrets or unnecessary personal information.

---

## 13. Database changes

Every schema change must use an Alembic migration.

When changing a persisted enum or contract:

1. explain migration impact;
2. add migration;
3. add tests;
4. update documentation.

---

## 14. Secrets

Never commit secrets.
Use environment variables.
Provide `.env.example` with placeholder values only.
Do not print secrets into logs, tests, or README examples.

---

## 15. Code quality

Priority:

```text
correctness
> clear contracts
> testability
> observability
> modularity
> performance
> feature breadth
```

Prefer small functions, explicit types, pure deterministic functions where practical, dependency injection for external adapters, fixtures for external systems, and explicit error types.

Avoid god classes, implicit provider coupling, untyped nested dictionaries for stable domain data, silent exception swallowing, premature distributed architecture, and unnecessary multi-agent frameworks.

---

## 16. Scope control

If the requested task is Phase 0/1, do not also build frontend, Reddit integration, vision, Google integrations, ML training pipelines, advanced routing, or multi-agent orchestration.

Create only interfaces/placeholders needed to preserve architecture.

---

## 17. Completion report format

At the end of each coding task, report:

### Implemented
- concise description

### Changed files
- file list

### Tests run
```text
exact commands
```

### Result
- pass/fail
- important output

### Assumptions
- explicit assumptions

### Decisions needing approval
- only unresolved decisions that materially affect future design

Do not claim a task is complete if tests were not run, unless the environment prevented running them. In that case, explain exactly why.

---

## 18. Project motto

> AI reads the world. GIS describes space. Rules determine legality. Statistics estimates availability. Optimization decides where to search next.
