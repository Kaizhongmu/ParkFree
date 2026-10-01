# Phase 7 Map Workspace

Phase 7 began as a same-origin, dependency-free interface for the Phase 6 search API. The current
three-panel workspace uses that interface for the separate post-Phase-8 on-demand endpoint. It
shows search settings on the left, a numbered map and candidate sequence in the center, and the
selected curb's evidence and model trace on the right. It does not make legal decisions, claim
that a curb is free, or create a verified parking route from provisional coverage.

The strict replayable `POST /v1/parking/search` API still exists, but the current browser form does
not submit it or expose the former SMU demo action.

## Run

Create `.env`, configure at least an identifying `NOMINATIM_USER_AGENT`, then run the exact project
environment command:

```bash
set -a
source .env
set +a
.venv/bin/python -m uvicorn parking_ai.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/`. No PostGIS seed, `npm install`, frontend build, CDN, API key, or paid
service is required for the two-mode on-demand demo. Satellite mode loads public USGS tiles; Plan
mode remains available without that basemap. `OVERPASS_USER_AGENT` is optional; without it,
enhanced research is reported as not configured and uses TIGERweb directly.

Do not open `src/parking_ai/web/index.html` with a `file://` URL. Assets and API requests are
same-origin by design. If JavaScript does not initialize, a default-visible diagnostic links back
to the server root. The HTTP `/` and `/index.html` entries return the same secured document. The
document and its versioned local assets use `Cache-Control: no-store`. The non-JavaScript form uses
POST so a failed script load does not copy coordinates and preferences into the URL.

## Two-mode workflow

The form collects origin coordinates, destination text, arrival time, parking duration, walking
limit, optional permits, candidate limit, and free-only preference. Browser geolocation is
requested only after the user presses “Use my location.”

Destination discovery is an explicit `POST /v1/destinations/search` action; the UI never performs
type-ahead requests or silently chooses a match. Selecting a result draws that destination and
enables two submit buttons. Selection alone does not start road acquisition.

- **Instant** sends `research_mode=INSTANT`. The backend revalidates the destination through
  Nominatim, then calls official Census TIGERweb directly for road geometry.
- **Research** sends `research_mode=RESEARCH`. The backend revalidates through
  Nominatim, tries Overpass/OpenStreetMap road and parking-tag context when configured, and falls
  back to TIGERweb after an empty or failed Overpass result.

Both buttons call `POST /v1/parking/on-demand`. Neither calls the strict optimizer or uses an SMU
candidate/fallback. Both run the same versioned V0 availability baseline, which is displayed as
`UNCALIBRATED_HEURISTIC` and conditional on a curb being legal and usable. `RESEARCH` may provide a
richer road snapshot but does not select a different model or guarantee a more accurate estimate.

All provisional candidates remain `legal_state=UNKNOWN`, `free_state=UNKNOWN`, and
`legal_confidence=0`. OSM parking tags and TIGER road centerlines do not establish legal or free
parking. The UI preserves and numbers proximity order while labeling it as provisional rather
than presenting it as a verified parking route.

The scheduled-arrival field interprets `datetime-local` in the user's device timezone and labels
that behavior. Destination-local wall-time input and explicit DST gap/fold handling remain outside
the current contract.

Each request owns an abortable in-memory token. Editing the form, starting a new destination
search, selecting another match, or accepting a new geolocation invalidates the pending request.
Only the current request may update results, status, or loading state. Starting a new request clears
old results, and a failure cannot leave stale leads presented as current.

## Source and enrichment states

The result renders sanitized provider attempts plus the selected mode. `research_mode` is
`INSTANT` or `RESEARCH`; `enrichment_status` is:

- `NOT_REQUESTED` when `INSTANT` intentionally skips Overpass;
- `APPLIED` when Overpass supplied the `RESEARCH` snapshot;
- `DEGRADED` when `RESEARCH` fell back from Overpass to TIGERweb;
- `NOT_CONFIGURED` when `RESEARCH` had no distinct Overpass provider and used TIGERweb; or
- `FAILED` when a distinct enhanced provider chain produced no usable snapshot. This includes
  all-source failures and empty/failed or empty/empty outcomes that return `NO_CANDIDATES`.

The road-acquisition headline separately reports ready, fallback used, no roads returned, or all
road sources failed. An exhausted source chain is research failure, not proof that parking is
absent. Enrichment status reports provider execution only; it is not a confidence score for
legality, payment, or availability.

## Map and semantics

The SVG projects validated WGS84 LineStrings into a local viewport. Its default satellite mode
uses public USGS National Map imagery tiles and a matching Web Mercator projection; the plan mode
keeps the local diagram available without a remote basemap. For on-demand results it displays:

- unknown provisional curb leads with a text label and distinct line style;
- left/right records offset from their shared centerline so both remain selectable;
- the conditional V0 probability and heuristic interval where the backend produced one;
- the selected destination; and
- provider/contributor attribution supplied by the response.

Every candidate receives a visible order marker. Evaluated plans use optimizer route order;
provisional results use the backend's deterministic proximity order. Selecting either a map curb
or list row updates both selection states and the right-side detail panel. The detail panel shows
the conditional probability, curb length/capacity, legal/free state, reason codes, evidence
references, prediction basis, and available model versions.

The selected candidate exposes a Google Maps satellite link and a driving-navigation link. Both
use the midpoint of the selected candidate geometry as the destination. Google Maps uses the
device's current location when available and performs the actual road routing; the displayed
ParkFree sequence is not turn-by-turn navigation.

The map is not a legal guarantee or verified ranking. A complete keyboard-operable candidate list
exposes the same information without relying on color or pointer interaction. `UNKNOWN` remains
visible with a signage-verification warning.

## Provenance boundary

The on-demand response exposes sanitized provider names, primary/fallback roles, attempt outcomes,
coverage metadata, attribution, availability/model provenance, `research_mode`, and
`enrichment_status`. The UI does not expose raw provider payloads, exception text, or query URLs,
and it does not invent regulation evidence, publisher names, or source links.

## Privacy and security

- The browser sends destination, selected match ID, origin, and preferences to the same-origin API
  in POST bodies; it does not store them in URLs, cookies, localStorage, or analytics.
- Nominatim receives the destination query during lookup and canonical revalidation.
- TIGERweb receives the selected destination area in `INSTANT` mode. In `RESEARCH` mode, Overpass
  receives that area first and TIGERweb may receive it as fallback. Current road-provider calls do
  not receive the user's origin coordinate.
- Satellite mode requests public USGS tiles for the displayed destination area. Opening a Google
  Maps action sends the selected candidate coordinates to Google; navigation may also use the
  user's device location under Google's settings and permissions.
- Offline timezone resolution makes no external call. Raw provider responses are ephemeral and
  normalized caches are bounded in memory, but live third-party calls are not private; avoid
  sensitive locations.
- Every form field has a visible label. Search, destination, and geolocation status use appropriate
  live regions, and the results panel exposes busy state.
- The SVG candidate paths have group semantics and equivalent keyboard-operable cards.
- State uses text, line style, and color; probability always has numeric text.
- Layout supports 320-pixel viewports and reduced motion.
- Dynamic strings use DOM text nodes, never `innerHTML`, HTML templates, or evaluated code.
- Coordinates are revalidated before SVG rendering; CSS state classes come from fixed enums.
- The document sets CSP, `nosniff`, `no-referrer`, frame denial, and a restricted permissions policy.
- Only named CSS, JavaScript, and favicon files are exposed below `/assets`.
- Every `/v1/` response is `Cache-Control: no-store` and also sets `no-referrer` and `nosniff`.
- `ENVIRONMENT=production` disables `/docs`, `/redoc`, and `/openapi.json`; it does not add user
  authentication.

## Temporary Quick Tunnel

For a short-lived public demonstration, start the API without development reload in one terminal:

```bash
set -a
source .env
set +a
ENVIRONMENT=production .venv/bin/python -m uvicorn parking_ai.main:app \
  --host 127.0.0.1 --port 8000
```

Then start a second process:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Open the generated `https://*.trycloudflare.com` URL. The hostname is temporary, changes after a
restart, and works only while both local processes remain alive. A Quick Tunnel supplies no
ParkFree authentication, durable hostname, availability guarantee, or production privacy boundary.
Anyone with the URL can submit requests that trigger live Nominatim and road-provider calls.
Provider adapters have bounded in-process caches, serialization/rate gates where documented, and
request limits, but ParkFree has no per-client ingress rate limiter. Traffic traverses
Cloudflare-managed infrastructure. Do not use this setup for sensitive locations, sustained
traffic, multi-worker deployment, or production.

## Verification

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest -q -m 'not integration'
TEST_DATABASE_URL='<dedicated-postgis-url>' .venv/bin/python -m pytest -q
```

Automated browser-facing unit tests statically verify the two buttons and request-mode wiring,
destination-selection gate, enrichment labels, conditional V0 copy, unknown legal/free copy,
security headers, local assets, accessibility markup, and that the strict search API is not wired
to the public form. They do not execute a real browser and therefore do not prove abort/race
behavior, visual layout, focus interaction, or console cleanliness; those remain manual browser
acceptance checks. Provider tests use injected transports and never call live Nominatim, Overpass,
or TIGERweb.

## Scope boundary

This document describes the Phase 7 UI plus its post-Phase-8 two-mode on-demand extension. Phase
8/V1A evidence review does not run automatically in the request path. ParkFree does not calculate
turn-by-turn routes; it hands the selected destination to Google Maps. General web search,
automatic AI evidence approval, verified legal/free parking, outcome
collection, accounts, analytics, and learned/calibrated availability models remain outside this
demo.
