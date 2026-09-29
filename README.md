# Parking Intelligence System

Phase 0 through Phase 8 foundation for a provider-independent parking intelligence system.
The repository includes infrastructure, domain schemas, persistence models, stable service
contracts, a deterministic SMU candidate-segment GIS slice, and a deterministic parking
regulation engine. It also includes a deterministic, versioned availability baseline and an
offline evaluation harness, provider-independent route matrix, deterministic contingent-search
optimizer, a replayable end-to-end parking-search API, a dependency-free local map UI, and bounded
AI evidence-service contracts with explicit human review before persistence.
The post-Phase 8 V1A hardening slice adds a durable least-privilege review queue and append-only
audit trail for trusted backend operators; it intentionally exposes no public review endpoint.
The nationwide-readiness work now includes zero-cost, US-only destination discovery plus a
separate bounded on-demand road-coverage path. The public web demo offers two explicit modes for a
selected place: `INSTANT` gets official Census TIGERweb road geometry directly, while `RESEARCH`
tries Overpass/OpenStreetMap road and parking-tag context first and falls back to TIGERweb. Both
modes generate provisional curb leads without pre-seeding the destination, resolve its timezone
offline, and run the same versioned V0 conditional-vacancy baseline. The strict deterministic
parking-search API remains isolated from live providers.

## Requirements

- Python 3.11 or newer
- Docker Engine with Docker Compose v2
- PostgreSQL 16 with PostGIS 3.4 when running outside Docker

## Environment setup

Create a virtual environment and install the project with development tools:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
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
docker compose run --rm api parking-ai-seed-smu
```

Verify the API:

```bash
curl http://localhost:8000/health
```

The response is `{"status":"ok"}`.

Zero-cost US destination discovery uses public Nominatim and requires an identifying
application/contact string. Overpass is optional and enriches only `RESEARCH` requests. Neither
requires an API key or billing account:

```text
NOMINATIM_USER_AGENT=ParkFree/0.1 (contact: operator@example.com)
OVERPASS_USER_AGENT=ParkFree/0.1 (contact: operator@example.com)
```

After configuration, explicitly submit a lookup (the public service must not be used for
type-ahead autocomplete):

```bash
curl -X POST http://localhost:8000/v1/destinations/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"Seattle Center"}'
```

Destination search resolves places only. After a user selects an exact match, the separate
`POST /v1/parking/on-demand` endpoint revalidates that match and generates provisional curb leads.
Its two request modes are:

- `INSTANT`: query Census TIGERweb directly; enhanced OSM/parking-tag enrichment is not requested.
- `RESEARCH`: try Overpass first when configured, then use TIGERweb if Overpass fails or returns no
  roads. Without `OVERPASS_USER_AGENT`, it uses TIGERweb and reports enrichment as not configured.

See [`docs/ZERO_COST_DESTINATION_DISCOVERY.md`](docs/ZERO_COST_DESTINATION_DISCOVERY.md).

The web demo exposes this lookup through an explicit “Find this US place” action. Selecting the
intended match enables “Estimate now” (`INSTANT`) and “Research APIs, then estimate” (`RESEARCH`);
selection alone makes no road-provider call. The response reports both `research_mode` and
`enrichment_status`: `NOT_REQUESTED` when fast mode skipped enhancement, `APPLIED` when Overpass
supplied the research snapshot, `DEGRADED` when TIGERweb supplied a research fallback,
`NOT_CONFIGURED` when research had no distinct Overpass source, or `FAILED` when a distinct
research chain produced no usable snapshot. `FAILED` can accompany `PROVIDER_UNAVAILABLE` or a
`NO_CANDIDATES` result from empty/failed sources. These values describe provider execution, not
parking quality or model accuracy.

Every returned lead remains explicitly `UNKNOWN` for legality and payment because road geometry
and parking tags are not authoritative proof of a legal, free curb. The leads are not sent to the
strict optimizer and never inherit SMU candidates or its fallback. Both modes run the same
`UNCALIBRATED_HEURISTIC` V0 availability model, conditional on the curb being legal and usable;
`RESEARCH` does not use a more accurate model and neither result is a free-parking probability or
verified ranking.

The same flow can be exercised directly after copying the selected `query` and `match_id` from the
destination-search response:

```bash
curl -X POST http://localhost:8000/v1/parking/on-demand \
  -H 'Content-Type: application/json' \
  -d '{
    "origin": {"lat": 32.842, "lon": -96.784},
    "destination": {"query": "Seattle Center", "match_id": "geo_replace_me"},
    "research_mode": "RESEARCH",
    "arrival_time": "now",
    "parking_duration_minutes": 60,
    "free_only": true,
    "max_walk_minutes": 8.0,
    "vehicle_profile": {"type": "passenger", "permit_types": []},
    "max_candidates": 20
  }'
```

Open the Phase 7 interface at [`http://localhost:8000/`](http://localhost:8000/). The page is
served by FastAPI and needs no separate frontend build or package manager. Do not open
`src/parking_ai/web/index.html` directly: the UI intentionally requires the same HTTP origin as
the API. The page now shows a persistent diagnostic with a link to the supported HTTP entrypoint
if its JavaScript never starts. The HTML and three local assets use `no-store` plus versioned asset
URLs in this local demo, so `/` cannot silently retain an older UI than a cache-busted URL.

The browser sends the destination query, selected match ID, origin coordinates, and preferences to
the same-origin API. The API sends the destination query to Nominatim, then sends the selected
destination area to TIGERweb in `INSTANT` mode or to Overpass followed by TIGERweb fallback in
`RESEARCH` mode. The current road-provider calls do not receive the user's origin coordinate.
Raw provider responses are ephemeral and normalized caches are in memory, but this is still a
live third-party lookup. Avoid sensitive destinations and origins, especially through a public
tunnel.

Every `/v1/` response is marked `Cache-Control: no-store` and receives `no-referrer` and `nosniff`
headers. Set `ENVIRONMENT=production` for public exposure; production disables `/docs`, `/redoc`,
and `/openapi.json`. These controls do not add authentication.

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

For the two-mode on-demand demo, export the local configuration and run FastAPI from the project
virtual environment:

```bash
set -a
source .env
set +a
.venv/bin/python -m uvicorn parking_ai.main:app --host 127.0.0.1 --port 8000 --reload
```

The on-demand demo does not require PostGIS migrations or the SMU seed. To exercise the separate
strict Phase 6 API locally, start PostGIS and initialize it with exact module commands:

```bash
docker compose up -d db
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m parking_ai.gis.seed
```

`parking-ai-seed-smu` is an idempotent initialization command. It loads the canonical destination,
two access points, OSM fixture evidence, and 284 candidate segments. It does not invent or load
parking regulations; candidates remain explicitly `UNKNOWN` until authoritative rule evidence is
separately ingested. The command enforces the fixture's exact 284-unique-segment/eight-minute
contract and fails before writing if it changes unexpectedly. It upserts those stable identities
but does not prune unrelated or historical database rows.

## Migrations

Apply or inspect migrations through the project environment:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current
.venv/bin/python -m alembic history
```

Create future schema migrations only after updating SQLAlchemy models:

```bash
.venv/bin/python -m alembic revision --autogenerate -m "describe schema change"
```

## Temporary public demo with a Quick Tunnel

For a short-lived demonstration, start the API without development reload in one terminal:

```bash
set -a
source .env
set +a
ENVIRONMENT=production .venv/bin/python -m uvicorn parking_ai.main:app \
  --host 127.0.0.1 --port 8000
```

Then start a separate Cloudflare Quick Tunnel process:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Open the generated `https://*.trycloudflare.com` URL. The URL is temporary, changes when the tunnel
restarts, and works only while both local processes remain running. Quick Tunnels provide no
ParkFree authentication, durable hostname, availability guarantee, or production privacy boundary;
anyone with the URL can submit live provider requests through this app. The provider adapters have
bounded in-process caches, serialization/rate gates where documented, and request limits, but the
app has no per-client ingress rate limiter. Traffic traverses Cloudflare-managed infrastructure,
and the public Nominatim/Overpass usage limits still apply. Do not use this setup for sensitive
locations, sustained traffic, multi-worker deployment, or production. A named authenticated
tunnel and production provider/cache/ingress-control design are separate deployment work.

## Tests and checks

Unit tests do not require a database:

```bash
.venv/bin/python -m pytest -m "not integration"
```

Integration tests require a dedicated PostGIS database. The default Compose database can be
used on a fresh development checkout:

```bash
export TEST_DATABASE_URL="$DATABASE_URL"
.venv/bin/python -m pytest -m integration
```

Run the complete verification suite:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest
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
See [`docs/V1A_DURABLE_REVIEW_QUEUE.md`](docs/V1A_DURABLE_REVIEW_QUEUE.md) for roles, leases,
state transitions, transaction ownership, audit integrity, retention, and verification commands.
See [`docs/ZERO_COST_DESTINATION_DISCOVERY.md`](docs/ZERO_COST_DESTINATION_DISCOVERY.md) for the
free Nominatim adapter, privacy/rate limits, and the boundary between place discovery and parking
coverage.
See [`docs/ON_DEMAND_PARKING.md`](docs/ON_DEMAND_PARKING.md) for direct TIGERweb `INSTANT`
acquisition, `RESEARCH`-mode Overpass enrichment with Census TIGERweb failover,
provisional candidate semantics, zero-cost constraints, and exact verification commands.

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
src/parking_ai/coverage/ bounded live road acquisition and provisional candidate orchestration
src/parking_ai/web/      dependency-free Phase 7 map UI assets
src/parking_ai/agents/   bounded Phase 8 evidence extraction and review services
src/parking_ai/evidence/ approved evidence/rule persistence boundary
tests/unit/              schema and HTTP tests
tests/integration/       PostGIS persistence and migration tests
```

## Implemented phase

The current implementation is complete through Phase 8 plus the V1A durable-review hardening
slice. It provides the health and search APIs,
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
persisted. It configures no paid model provider. The new on-demand endpoint performs live place
and road-data acquisition, but it does not yet run a general web-search crawler or local language
model. Its output is deliberately provisional until the reviewed-evidence boundary has trustworthy
regulation facts.
V1A durably stores normalized review proposals and an append-only audit chain, enforces
least-privilege reviewer roles and claim leases, and atomically publishes approved evidence/rules.
It adds no authenticated review API; deployments must supply and verify trusted operator identity
before exposing any management surface. A post-Phase 8 safety hardening also requires every rule's
source evidence to be explicitly associated with that same segment, both when constructing the
deterministic engine and through a deferred composite PostgreSQL foreign key. Additional safety
guards bind every review projection revision to its audit event, reject stale regulation results
or ineligible planner output, reject causally invalid or source-elevated evidence at the domain,
engine, and database boundaries, verify normalized replay provenance, and expose the HTML UI only
through its security-header-protected entrypoint.
