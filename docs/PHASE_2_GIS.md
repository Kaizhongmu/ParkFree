# Phase 2 — SMU Candidate-Segment GIS Slice

## Scope and current phase

Phase 2 is implemented. It generates and persists plausible curb candidates around the canonical
SMU Fondren Library destination. It does not determine whether parking is legal or free. Every
generated segment therefore has `legal_state=UNKNOWN`, `free_state=UNKNOWN`, and
`legal_confidence=0.0`.

The implementation does not include live OSM retrieval, regulation evaluation, availability
prediction, route optimization, a search API, a frontend, or AI services.

## Fixtures

`src/parking_ai/gis/data/smu_fondren_destination_v1.json` contains the canonical Fondren Library
destination and two manually configured access points. All named point fields are
latitude/longitude. SMU's official library page confirms the destination name and address at
6414 Robert S. Hyer Lane:
`https://www.smu.edu/libraries/locations/fondren`. The point geometry is traceable to OSM building
way `443633904`: the destination is its planar WGS84 centroid and the access points are tagged
entrance nodes `6080676255` and `11289496087`. The labels "West entrance" and "East entrance" are
relative spatial descriptions, not official SMU entrance names.

`src/parking_ai/gis/data/smu_osm_fixture_v1.json` is a package-local, reduced OSM-derived road
snapshot. It contains 419 real OSM nodes and 102 clipped fragments of real OSM ways. Way fragment
identifiers retain the source way ID as `<osm-way-id>#<fragment-number>`, and source road tags are
preserved. The fixture bounds are longitude `[-96.7900222, -96.7766534]` and latitude
`[32.8384510, 32.8506252]`; every retained vertex is within 699 m of the configured destination.
This is a focused Phase 2 slice, not full coverage of the eventual 1–1.5 km MVP geography. Tests
and runtime candidate generation read only the committed local files and never call a network
service.

The road fixture is a reduced derivative intended for deterministic development tests:

- source and attribution: © OpenStreetMap contributors;
- data license: Open Data Commons Open Database License 1.0 (ODbL-1.0),
  `https://opendatacommons.org/licenses/odbl/1-0/`;
- attribution and license requirements: `https://www.openstreetmap.org/copyright`;
- source snapshot request:
  `https://api.openstreetmap.org/api/0.6/map?bbox=-96.795,32.834,-96.773,32.852`;
- snapshot retrieval time: `2026-09-19T13:58:38Z`;
- downloaded raw XML SHA-256:
  `b5f1692296bf16ab277adc3b68a50fb66df1cb505f04a50ce0a0e4a80b592321`;
- reduction rule: retain eligible highway ways touching a 550 m radius, clip at existing OSM nodes
  within 700 m, and mark clipped endpoints as fixture boundaries;
- storage policy: `PERSIST` in the repository and evidence store;
- evidence tier: B (`OSM`) for road-centerline geometry and tags only;
- freshness: the fixture's fixed timezone-aware snapshot time;
- limitation: the snapshot is not authoritative or current parking/curb evidence.

The committed reduced JSON is the runtime source of truth. The raw XML is not required at test or
request time. To audit the upstream snapshot retrieval and checksum:

```bash
curl -fsSL --max-time 60 -A 'ParkFreePhase2Fixture/1.0' \
  -o /tmp/parkfree_smu_bbox.osm \
  'https://api.openstreetmap.org/api/0.6/map?bbox=-96.795,32.834,-96.773,32.852'
shasum -a 256 /tmp/parkfree_smu_bbox.osm
```

The live endpoint may change after the recorded retrieval time; the committed fixture and its own
evidence content hash preserve the exact offline test input.

The adapter validates the provider-shaped node/way JSON and emits provider-independent
`RoadFeature` values. OSM-specific node references and tags do not enter the core generator.

## Deterministic generation

The generator implements the existing `CandidateSegmentService.get_candidate_segments`
contract. Its behavior is deterministic:

1. Reject explicitly excluded ways and road classes outside the configured candidate set.
2. Split ways at shared intersection nodes and explicit fixture boundaries.
3. Round WGS84 coordinates to seven decimal places, remove adjacent duplicates, and choose a
   canonical direction by lexicographic coordinate order.
4. Emit left and right side records separately.
5. Calculate length as the sum of haversine distances using mean Earth radius 6,371,008.8 m.
6. Create `seg_` IDs from a SHA-256 digest of ID-scheme version, normalized geometry, normalized
   street name, road type, and side.
7. Attach fixture evidence and fixed freshness while preserving explicit unknown states. OSM road
   class alone does not establish curb feasibility, so every fixture road defaults to
   `physical_state=UNKNOWN`.
8. Rank by straight-line distance to the nearest entrance, then stable segment ID; constrain the
   result using walking minutes and `max_candidates`.

The initial fixture yields 284 side-specific candidates, all within eight minutes of the
configured entrances at the documented straight-line 80 m/minute walking approximation.
`free_only` has no Phase 2 effect because free/paid evaluation belongs to Phase 3.

## Persistence

`upsert_gis_slice` uses the existing Phase 1 tables and PostgreSQL conflict handling. It upserts
the destination, access points, evidence, and street segments, then inserts evidence-to-segment
links with conflict-ignore semantics. The caller owns the transaction and commit. Re-ingesting the
same fixture updates matching stable records and creates no duplicates. Segment conflict updates
are limited to GIS-owned spatial and physical columns, so later regulation and availability
results are not reset by fixture re-ingestion.

No Phase 2 migration is required: migration `0001_initial_schema` already supplies all needed
tables, PostGIS geometries, primary keys, provenance links, enums, and spatial indexes. The Phase 2
integration test starts from the freshly migrated schema and exercises the ingestion twice.

Operators can run the same idempotent path after applying migrations:

```bash
parking-ai-seed-smu
```

The command reads `DATABASE_URL`, commits one transaction, prints only the destination ID and
segment count, and is safe to repeat across transactions. It enforces the canonical 284 unique
segments within the documented eight-minute search constraint before writing. It does not load
parking rules, turn unknown candidates into legal/free recommendations, or prune other existing
rows whose stable IDs are outside this fixture projection.

## Verification commands

Run focused Phase 2 tests without a database:

```bash
.venv/bin/pytest tests/unit/test_smu_gis_fixture.py tests/unit/test_candidate_segment_generator.py
```

Run the real PostGIS migration and idempotency gate against a dedicated test database:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/parking_test'
.venv/bin/pytest -m integration
```

Run all configured checks:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pytest
```
