# Codex Task — Phase 0 + Phase 1

Read these files completely before making any changes:

1. `AGENTS.md`
2. `docs/IMPLEMENTATION_SPEC.md`

Treat them as authoritative.

## Scope

Implement **Phase 0 and Phase 1 only**.

Do **not** proceed to Phase 2 or later.

Do not add an LLM provider, external map provider, frontend, Reddit integration, vision system, ML training pipeline, live OSM call, or production routing provider.

The purpose of this task is to create a clean, tested foundation that later phases can extend without architecture redesign.

---

# Phase 0 — Repository and infrastructure

Create a minimal production-oriented Python backend foundation.

Required:

- Python project using `pyproject.toml`
- `src/` layout
- FastAPI application
- `GET /health`
- Dockerfile
- Docker Compose
- PostgreSQL with PostGIS
- SQLAlchemy
- Alembic
- pytest
- `.env.example`
- `.gitignore`
- README with exact local setup instructions
- database session/configuration layer
- test configuration
- basic structured logging
- no hardcoded secrets

Preferred package:

```text
src/parking_ai/
```

Minimum health response:

```json
{"status": "ok"}
```

---

# Phase 1 — Domain and persistence layer

Implement typed schemas for at least:

```text
Destination
DestinationAccessPoint
ParkingSegment
ParkingRule
Evidence
LegalityEvaluation
AvailabilityPrediction
SearchRouteStep
SearchRoute
SearchSession
ParkingOutcome
```

Use explicit enums.

At minimum:

```text
LegalState:
LEGAL
ILLEGAL
UNKNOWN

FreeState:
FREE
PAID
UNKNOWN

PhysicalState:
PARKABLE
NOT_PARKABLE
UNKNOWN

SegmentSide:
LEFT
RIGHT
UNKNOWN

EvidenceReliabilityTier:
A
B
C
D

EvidenceStoragePolicy:
PERSIST
EPHEMERAL
REFERENCE_ONLY
```

Parking rule types must support:

```text
NO_PARKING
TIME_LIMIT
PAID
PERMIT_ONLY
LOADING
STREET_CLEANING
EVENT_RESTRICTION
OTHER
```

Use timezone-aware datetimes.

Represent persisted geometry with PostGIS-compatible spatial types.

Do not add provider-specific fields to core domain objects.

---

# Persistence

Create SQLAlchemy models and Alembic migrations for at least:

```text
destinations
destination_access_points
street_segments
parking_rules
parking_sources
search_sessions
search_route_steps
parking_outcomes
```

If `occupancy_predictions` is necessary in Phase 1 to preserve a clean contract, it may be included, but explain why.

Use foreign keys and indexes where appropriate.
Use a spatial index where appropriate.
Do not over-engineer future-phase fields.

---

# Architecture skeleton

Create package/module locations consistent with the implementation specification.

Future modules may contain only minimal interfaces or `__init__.py` files when needed.

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

For this phase, these may be Protocols, abstract interfaces, typed service stubs, or documented contracts.

Do not implement Phase 2+ business logic.

---

# Tests

Add tests for:

## Schema validation

- valid `ParkingSegment`
- invalid probability outside `[0,1]`
- invalid uncertainty interval
- enum validation
- timezone-aware datetime expectations
- evidence tier/storage-policy validation

## Database round trips

- destination
- access point
- spatial parking segment
- parking rule
- evidence/source
- search session
- route step
- parking outcome

## Relationships

Verify important foreign-key relationships.

## Migration

The test/dev setup should prove that migrations can create the schema from a fresh database.

If full migration testing is impractical in the environment, document exactly what was run and why.

---

# Required repository documentation

Create:

```text
README.md
docs/assumptions.md
```

`README.md` must include:

- requirements
- environment setup
- `.env` setup
- Docker Compose startup
- migration commands
- application startup
- test commands
- project structure
- current implemented phase
- explicit statement that Phase 2+ is not implemented

`docs/assumptions.md` must contain architecture/product assumptions introduced during this task.

---

# Recommended structure after this task

```text
parking-intelligence/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── .gitignore
├── docs/
│   ├── IMPLEMENTATION_SPEC.md
│   ├── assumptions.md
│   └── architecture_decisions/
├── src/
│   └── parking_ai/
│       ├── main.py
│       ├── config.py
│       ├── api/
│       ├── database/
│       ├── domain/
│       ├── orchestrator/
│       ├── geocoding/
│       ├── gis/
│       ├── regulations/
│       ├── agents/
│       ├── availability/
│       ├── routing/
│       └── evidence/
├── alembic/
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
```

You may adjust this structure if there is a concrete engineering reason. Explain any material change.

---

# Important constraints

1. Do not implement the whole product.
2. Do not add an LLM.
3. Do not add frontend code.
4. Do not add a multi-agent framework.
5. Do not add a production routing provider.
6. Do not add live OSM calls.
7. Do not add availability ML.
8. Do not silently change architecture.
9. Do not commit secrets.
10. Do not use SQLite as the main project database; use PostgreSQL/PostGIS.
11. Tests must not depend on paid external services.
12. Use provider-independent domain types.
13. Keep future phases replaceable.
14. Favor clean contracts over feature count.

---

# Definition of done

This task is complete only when:

- FastAPI starts successfully.
- `GET /health` works.
- PostgreSQL/PostGIS starts from Docker Compose.
- Alembic can create the initial schema.
- typed schemas exist for the required domain objects.
- initial SQLAlchemy models exist.
- spatial types are represented correctly.
- schema and persistence tests pass.
- README contains exact run commands.
- assumptions are documented.
- no Phase 2+ feature was implemented.
- repository is ready for Phase 2 without architecture redesign.

---

# Execution workflow

Before editing:

1. Inspect repository.
2. Read `AGENTS.md`.
3. Read `docs/IMPLEMENTATION_SPEC.md`.
4. Present a short implementation plan.

Then implement.

After implementation:

1. Run the full relevant test suite.
2. Run migration/setup commands.
3. Fix failures.
4. Do not stop at the first failing test.
5. Report completion using the format required by `AGENTS.md`.

At the very end include:

```text
Implemented
Changed files
Tests run
Result
Assumptions
Decisions needing approval
```

Do not proceed to Phase 2.
