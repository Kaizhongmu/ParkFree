# Phase 7 — Minimal map UI

Phase 7 is a same-origin, dependency-free interface for the completed Phase 6 search API. It shows
candidate curb geometry, separate legal/payment states, availability estimates, route order,
fallback, warnings, evidence references, and decision versions. It does not make legal decisions
or recompute route order in the browser.

## Run

Configure and migrate the Phase 6 application as described in `PHASE_6_SEARCH_API.md`, then run:

```bash
.venv/bin/uvicorn parking_ai.main:app --reload
```

Open `http://127.0.0.1:8000/`. No `npm install`, frontend build, remote tile, CDN, API key, or paid
service is required.

## Search workflow

The form collects origin coordinates, destination name, arrival time, parking duration, walking
limit, optional permits, candidate limit, and free-only preference. Browser geolocation is
requested only when the user presses the location button. A search posts the existing strict
Phase 6 JSON contract.

An identical failed request keeps its in-memory idempotency key for a safe retry. Changed form
content receives a different key. Once a request succeeds, its key is discarded so another `now`
search is a fresh execution. Inputs, coordinates, keys, and results are not stored in URLs,
cookies, localStorage, or analytics.

## Map and semantics

The SVG map projects validated WGS84 LineStrings into a local viewport. It displays:

- legal/free, legal/paid, illegal, and unknown segments with both labels and distinct line styles;
- availability probability and interval where the backend produced one;
- numbered route stops and a dashed schematic connection;
- the destination and a local fallback marker when it lies within the candidate viewport;
- OSM contributor and ODbL fixture attribution.

The map is not a road network, turn-by-turn route, or legal guarantee. A remote fallback does not
shrink the local map; its description and coordinates remain in the fallback card. A semantic
ordered route and candidate list exposes the same information without relying on color or pointer
interaction. UNKNOWN remains visible with a signage-verification warning.

Candidate state is read only from `candidate_decisions[].legality`; availability is read only from
`candidate_decisions[].availability`. The UI deliberately ignores legacy contextual state on the
embedded `segment` snapshot.

## Provenance boundary

The replayable Phase 6 response exposes evidence reference IDs, regulation reason codes,
evaluation time, confidence, and rule/model/optimizer versions. Phase 7 displays those values as
its minimal provenance view. It does not reinterpret GIS freshness as regulation freshness and
does not invent publisher names or source links that are absent from the API contract.

## Accessibility and security

- Every form field has a visible label; search status uses polite/assertive live regions.
- The route is an ordered list and every map candidate has an equivalent keyboard-operable card.
- State uses text, line style, and color; probability always has numeric text.
- Layout collapses to one column, supports 320-pixel viewports, and respects reduced motion.
- Dynamic strings use DOM text nodes, never `innerHTML`, HTML templates, or evaluated code.
- Coordinates are revalidated before SVG rendering; CSS state classes come from fixed enums.
- The document sets CSP, `nosniff`, `no-referrer`, frame denial, and a restricted permissions policy.
- Starting a new request clears prior results; an error cannot leave an old plan presented as new.

## Verification

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pytest -q -m 'not integration'
TEST_DATABASE_URL='<dedicated-postgis-url>' .venv/bin/pytest -q
```

Browser acceptance covers desktop and 320-pixel layouts, mixed legal/free/illegal/unknown states,
multi-step routes, fallback-only behavior, malicious HTML-like source text, safe 503 behavior,
keyboard-visible semantics, and an empty console. Python tests additionally verify asset MIME
types, package visibility, security headers, and that existing health/search endpoints are unchanged.

## Current phase boundary

Phase 7 is the current implemented phase. Phase 8 AI/LLM evidence extraction, live routing/maps,
publisher/source-detail APIs, outcome collection, accounts, analytics, and learned availability
models are not implemented.
