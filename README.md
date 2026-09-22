# Parking Intelligence System

Phase 0 through Phase 8 foundation for a provider-independent parking intelligence system.
The repository includes infrastructure, domain schemas, persistence models, stable service
contracts, a deterministic SMU candidate-segment GIS slice, and a deterministic parking
regulation engine. It also includes a deterministic, versioned availability baseline and an
offline evaluation harness, provider-independent route matrix, deterministic contingent-search
optimizer, a replayable end-to-end parking-search API, a dependency-free local map UI, and bounded
AI evidence-service contracts with explicit human review before persistence.

## Requirements

- Python 3.11 or newer
- Docker Engine with Docker Compose v2
- PostgreSQL 16 with PostGIS 3.4 when running outside Docker

## Environment setup

Create a virtual environment and install the project with development tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Create local environment configuration from the committed placeholders, then replace
`change-me` with a local-only password:

```bash
cp .env.example .env
```

Never commit `.env`.

## Docker Compose startup

Start PostgreSQL/PostGIS and the API:

```bash
docker compose up --build -d
docker compose ps
```

Run migrations against the Compose database:

```bash
docker compose run --rm api alembic upgrade head
```

Verify the API:

```bash
curl http://localhost:8000/health
```

The response is `{"status":"ok"}`.

Open the Phase 7 interface at [`http://localhost:8000/`](http://localhost:8000/). The page is
served by FastAPI and needs no separate frontend build or package manager.

Phase 6 search also requires an explicitly configured fallback location. The sample fallback
values are commented out in `.env.example` because a deployment must verify that the location
actually guarantees parking. Once configured and after the SMU fixture plus regulation evidence
have been ingested, submit a search with:

```bash
curl -X POST http://localhost:8000/v1/parking/search \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-search-001' \
  -d '{
    "origin": {"lat": 32.842, "lon": -96.784},
    "destination": {"destination_id": "smu-fondren-library"},
    "arrival_time": "2026-09-20T15:00:00-05:00",
    "parking_duration_minutes": 60,
    "free_only": true,
    "max_walk_minutes": 8.0,
    "vehicle_profile": {"type": "passenger", "permit_types": []},
    "max_candidates": 20
  }'
```

The bundled Phase 2 fixture intentionally contains no authoritative parking rules. Without
separately ingested, valid regulation evidence, candidates remain explicitly `UNKNOWN` and the
route uses the configured fallback; the API never treats missing evidence as legal or free.

Stop the services without deleting database data:

```bash
docker compose down
```

## Local application startup

Start only PostGIS, export the local configuration, migrate, and run FastAPI:

```bash
docker compose up -d db
set -a
source .env
set +a
alembic upgrade head
uvicorn parking_ai.main:app --reload
```

## Migrations

Apply or inspect migrations:

```bash
alembic upgrade head
alembic current
alembic history
```

Create future schema migrations only after updating SQLAlchemy models:

```bash
alembic revision --autogenerate -m "describe schema change"
```

## Tests and checks

Unit tests do not require a database:

```bash
pytest -m "not integration"
```

Integration tests require a dedicated PostGIS database. The default Compose database can be
used on a fresh development checkout:

```bash
export TEST_DATABASE_URL="$DATABASE_URL"
pytest -m integration
```

Run the complete verification suite:

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

The PostgreSQL/PostGIS tests are skipped unless `TEST_DATABASE_URL` is set. See
[`docs/PHASE_2_GIS.md`](docs/PHASE_2_GIS.md) for the Phase 2 fixture, generation, persistence,
and verification details. See
[`docs/PHASE_3_REGULATION_ENGINE.md`](docs/PHASE_3_REGULATION_ENGINE.md) for the Phase 3 rule
semantics and truth-table coverage. See
[`docs/PHASE_4_AVAILABILITY_BASELINE.md`](docs/PHASE_4_AVAILABILITY_BASELINE.md) for the Phase 4
event definition, heuristic coefficients, uncertainty method, and evaluation metrics. See
[`docs/PHASE_5_ROUTE_OPTIMIZER.md`](docs/PHASE_5_ROUTE_OPTIMIZER.md) for the directed matrix,
expected-time objective, fallback semantics, and greedy/beam strategies.
See [`docs/PHASE_6_SEARCH_API.md`](docs/PHASE_6_SEARCH_API.md) for the end-to-end request,
filtering, local matrix, persistence, idempotency, and replay contracts.
See [`docs/PHASE_7_MAP_UI.md`](docs/PHASE_7_MAP_UI.md) for the local SVG map, accessibility,
privacy, security, provenance display, and browser-verification policy.
See [`docs/PHASE_8_AI_EVIDENCE.md`](docs/PHASE_8_AI_EVIDENCE.md) for the strict extractor schemas,
source/storage policy, quarantine behavior, human-review boundary, and approved persistence flow.

## Project structure

```text
alembic/                 database migrations
docs/                    architecture specification and assumptions
src/parking_ai/api/      HTTP routes
src/parking_ai/database/ SQLAlchemy models and session setup
src/parking_ai/domain/   provider-independent schemas, enums, and interfaces
src/parking_ai/gis/      local fixture adapter, deterministic generator, and persistence
src/parking_ai/regulations/ deterministic rule evaluation
src/parking_ai/availability/ deterministic baseline prediction and evaluation
src/parking_ai/routing/  synthetic matrix, expected-cost evaluation, greedy and beam planning
src/parking_ai/orchestrator/ end-to-end Phase 6 composition and replay persistence
src/parking_ai/web/      dependency-free Phase 7 map UI assets
src/parking_ai/agents/   bounded Phase 8 evidence extraction and review services
src/parking_ai/evidence/ approved evidence/rule persistence boundary
tests/unit/              schema and HTTP tests
tests/integration/       PostGIS persistence and migration tests
```

## Implemented phase

The current implementation is complete through Phase 8. It provides the health and search APIs,
configuration and logging foundation, typed domain contracts, PostgreSQL/PostGIS persistence,
Alembic migrations, an offline deterministic SMU candidate-segment generator, and an
evidence-ranked deterministic regulation engine. The engine evaluates a requested stay without
mutating the persisted GIS-owned segment state. The availability service returns a versioned,
fully snapshotted V0 heuristic with an explicit uncertainty band and typed reason codes, without
deciding legality or mutating persisted state. The route planner consumes pre-evaluated candidates
and directed matrix costs to produce a versioned contingent route with expected time, success
probability, and guaranteed-fallback accounting. Phase 6 composes those services, explicitly
filters unknown/illegal/payment-incompatible candidates, persists complete replay snapshots, and
supports hashed idempotency keys. The Phase 7 UI renders those results as a responsive schematic
SVG map plus an equivalent semantic route/candidate list. It uses no CDN, live map tiles, external
fonts, or separate build chain and keeps unknown legality prominent. The default offline route
matrix remains a documented straight-line approximation. A real, deployment-verified fallback and
authoritative regulation ingestion remain operator responsibilities. Phase 8 validates
provider-independent regulation, community, and vision extraction output, quarantines invalid
responses, and requires explicit human approval before normalized evidence or rules can be
persisted. It configures no live/paid model provider and never invokes AI during a parking search.
