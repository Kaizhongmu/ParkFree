# Parking Intelligence System

Phase 0 through Phase 3 foundation for a provider-independent parking intelligence backend.
The repository includes infrastructure, domain schemas, persistence models, stable service
contracts, a deterministic SMU candidate-segment GIS slice, and a deterministic parking
regulation engine. Phase 4 and all later availability, routing, search API, AI, and frontend
behavior are not implemented.

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
semantics and truth-table coverage.

## Project structure

```text
alembic/                 database migrations
docs/                    architecture specification and assumptions
src/parking_ai/api/      HTTP routes
src/parking_ai/database/ SQLAlchemy models and session setup
src/parking_ai/domain/   provider-independent schemas, enums, and interfaces
src/parking_ai/gis/      local fixture adapter, deterministic generator, and persistence
src/parking_ai/regulations/ deterministic rule evaluation
tests/unit/              schema and HTTP tests
tests/integration/       PostGIS persistence and migration tests
```

## Implemented phase

The current implementation is complete through Phase 3. It provides the health API,
configuration and logging foundation, typed domain contracts, PostgreSQL/PostGIS persistence,
Alembic migrations, an offline deterministic SMU candidate-segment generator, and an
evidence-ranked deterministic regulation engine. The engine evaluates a requested stay without
mutating the persisted GIS-owned segment state. Availability prediction begins in Phase 4 and is
not implemented.
