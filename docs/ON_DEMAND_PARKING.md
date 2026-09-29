# Zero-cost on-demand parking coverage

## Purpose and current behavior

This post-Phase-8 slice removes the requirement to pre-seed every destination before showing any
local result. After a user explicitly searches for and selects a US place, the application:

1. re-runs the original Nominatim query and requires the exact selected `match_id`;
2. derives stable destination and access-point IDs from that canonical match;
3. follows the explicitly requested road-acquisition mode:
   - `INSTANT` queries the official U.S. Census TIGERweb Transportation REST API directly;
   - `RESEARCH` tries one bounded Overpass/OpenStreetMap query first when configured, then falls
     back to TIGERweb if Overpass fails or returns no roads;
4. clips ways to the requested walking-radius boundary, then deterministically splits them at
   represented shared intersections;
5. reuses the Phase 2 generator to produce side-specific, content-derived curb-segment IDs;
6. resolves the destination's IANA timezone from bundled offline polygons;
7. runs the same versioned Phase 4 V0 baseline for each lead at the requested arrival time; and
8. expands a bounded candidate pool, removes curb fragments shorter than 6 m, preserves proximity
   order, and returns at most 20 provisional leads with conditional availability priors, model
   provenance, and provider attribution.

No persisted destination, prebuilt local parking database, SMU segment, or SMU fallback is needed
for this endpoint. The existing strict `POST /v1/parking/search` remains unchanged and separate
from the public two-mode demo.

## Safety boundary

A road centerline or OSM parking tag is not proof that curb parking is legal or free. Every
on-demand segment therefore has `legal_state=UNKNOWN`, `free_state=UNKNOWN`, and
`legal_confidence=0`. The parking response `status` is one of:

- `PROVISIONAL_LEADS` — road-derived curb leads are available;
- `NO_CANDIDATES` — the bounded snapshot produced no eligible roads; or
- `PROVIDER_UNAVAILABLE` — road coverage could not be acquired.

The UI renders provisional geometry and warnings but no verified route. Missing evidence never
becomes `LEGAL` or `FREE`, and a provider failure never substitutes the SMU dataset.

The response separately echoes `research_mode` and reports `enrichment_status`:

- `NOT_REQUESTED` — `INSTANT` intentionally skipped the enhanced Overpass source;
- `APPLIED` — `RESEARCH` used a successful Overpass snapshot;
- `DEGRADED` — `RESEARCH` attempted Overpass, then TIGERweb supplied the usable snapshot;
- `NOT_CONFIGURED` — `RESEARCH` was requested without a distinct configured Overpass provider, so
  TIGERweb was used directly; or
- `FAILED` — `RESEARCH` produced no usable road snapshot from the distinct provider chain. This
  includes all-source failures (`PROVIDER_UNAVAILABLE`) and completed empty/failed attempt
  combinations that return `NO_CANDIDATES`.

These enrichment states describe provider execution only. They do not express confidence in
parking legality, price, availability, or model quality.

Availability estimates answer a narrower question: “if this curb is legal and usable, what is the
baseline chance of finding an opportunity in the model's 90-second search window?” They are not
legality probabilities, free-parking probabilities, or observations of current occupancy. The
current V0 model uses arrival-time bucket, road class, curb length/capacity fallback, physical
state, and a heuristic range; it has no local historical observations for new destinations and is
explicitly marked `UNCALIBRATED_HEURISTIC`.

`INSTANT` and `RESEARCH` run this same V0 model and feature contract. Enhanced research may
contribute OSM road class or parking tags to the road snapshot, but it does not select a better
model and is not guaranteed to produce a more accurate estimate. The UI preserves proximity order
instead of presenting the prior as a verified ranking. All predictions in one response share one
captured prediction timestamp.

The scheduled-arrival field currently interprets `datetime-local` in the user's device timezone;
the UI says so explicitly. Destination-local wall-time resolution, including DST gap/fold choices,
requires a future API contract and is not inferred silently.

This is a bounded request-time road lookup, not general parking-law research. It does not perform
general web search, jurisdiction-specific municipal dataset discovery, sign-image analysis, or
automatic LLM extraction. The repository's Phase 8 extractor and V1A review queue remain the
required trust boundary before external text can become authoritative parking rules.

## Provider, provenance, and storage

Every on-demand request first revalidates the destination through Nominatim. Road acquisition then
depends on the explicit mode:

| Mode | Primary road call | Fallback | Typical enrichment status |
|---|---|---|---|
| `INSTANT` | TIGERweb | none | `NOT_REQUESTED` |
| `RESEARCH`, Overpass configured | Overpass | TIGERweb after empty/failure | `APPLIED` or `DEGRADED` |
| `RESEARCH`, Overpass not configured | TIGERweb | none | `NOT_CONFIGURED` |

Configure identifying application/contact values:

```text
NOMINATIM_USER_AGENT=ParkFree/0.1 (contact: operator@example.com)
OVERPASS_USER_AGENT=ParkFree/0.1 (contact: operator@example.com)
```

Nominatim place search requires its User-Agent. Overpass is optional; a Nominatim-only setup still
supports both modes through TIGERweb, with `RESEARCH` explicitly marked `NOT_CONFIGURED`. No paid
API, API key, or billing account is required.

Overpass requests are POSTed, serialized per process, rate-gated, bounded by timeout, response
size, element/road/node/tag counts, and cached in memory by rounded coordinate, radius, and policy
version. Raw JSON is ephemeral. Normalized roads retain OSM element ID, version, timestamp, tags,
ODbL license, source URI, retrieval time, and contributor attribution.

TIGERweb requests query primary, secondary, and local road layers through bounded GeoJSON
envelopes, clip results to the requested radius, and retain Census layer/OID/MTFCC provenance.
Census geometry contains no curb regulation proof, so TIGERweb success never changes `UNKNOWN`
legality or price. Sanitized `provider_attempts` let the UI show which provider succeeded, returned
empty, or failed without exposing exception text or raw query URLs. All HTTPS transports use the
packaged `certifi` trust store.

Destination timezones are resolved locally with `timezonefinder` and its bundled Timezone Boundary
Builder-derived data. This avoids another request-time network or paid API dependency.

Public Nominatim and Overpass are suitable only for a low-volume, single-process demo. A
multi-worker or public production deployment must add shared rate control/cache and use self-hosted
services or regional OSM extracts. See the provider policies before deployment:

- <https://operations.osmfoundation.org/policies/nominatim/>
- <https://dev.overpass-api.de/overpass-doc/en/preface/commons.html>
- <https://osmfoundation.org/wiki/Licence/Attribution_Guidelines>

## Privacy boundary

The browser sends the destination query, selected `match_id`, origin coordinate, and search
preferences to the same-origin ParkFree API in POST bodies. The API sends the destination query to
Nominatim for both selection and canonical revalidation. It sends the selected destination area
and radius—not the user's origin coordinate—to TIGERweb in `INSTANT` mode, or to Overpass and
possibly TIGERweb in `RESEARCH` mode. Offline timezone resolution makes no additional network call.

The application does not persist raw geocoder or road-provider payloads. Provider caches are
bounded and in process, and application logs must not include raw destination queries, precise
result coordinates, provider query URLs, or provider exception text. These controls reduce data
retention but do not make live third-party calls private. Operators should not invite users to
submit sensitive homes, medical destinations, or other private locations through this demo.

All `/v1/` responses set `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and
`X-Content-Type-Options: nosniff`. With `ENVIRONMENT=production`, FastAPI's `/docs`, `/redoc`, and
`/openapi.json` endpoints are disabled. These response controls do not add authentication.

## Exact local run and smoke test

After creating `.env` and configuring at least `NOMINATIM_USER_AGENT`, run the API from the project
virtual environment:

```bash
set -a
source .env
set +a
.venv/bin/python -m uvicorn parking_ai.main:app --host 127.0.0.1 --port 8000 --reload
```

Resolve the place first:

```bash
curl -X POST http://127.0.0.1:8000/v1/destinations/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"The Village Chase, 5657 Amesbury Drive, Dallas, TX 75206"}'
```

Copy the returned `match_id` and submit the original query with it. This is the direct TIGERweb
`INSTANT` path:

```bash
curl -X POST http://127.0.0.1:8000/v1/parking/on-demand \
  -H 'Content-Type: application/json' \
  -d '{
    "origin":{"lat":32.842,"lon":-96.784},
    "destination":{
      "query":"The Village Chase, 5657 Amesbury Drive, Dallas, TX 75206",
      "match_id":"geo_replace_me"
    },
    "research_mode":"INSTANT",
    "arrival_time":"now",
    "parking_duration_minutes":60,
    "free_only":true,
    "max_walk_minutes":8.0,
    "vehicle_profile":{"type":"passenger","permit_types":[]},
    "max_candidates":20
  }'
```

To exercise enhanced research, submit the same payload with `"research_mode":"RESEARCH"`.
With `OVERPASS_USER_AGENT` configured, this tries Overpass before TIGERweb fallback. Without it,
TIGERweb supplies the road snapshot and `enrichment_status` is `NOT_CONFIGURED`.

Expected: HTTP 200 and a typed status. `PROVISIONAL_LEADS` contains only candidates around the
selected place; every candidate remains unknown for legal/payment state. When timezone resolution
succeeds, `availability_predictions`, `availability_assumption`, `calibration_status`, and
`destination_timezone` make the conditional model output explicit. Compare `research_mode`,
`enrichment_status`, and `provider_attempts` to see which provider path actually ran.

## Temporary Quick Tunnel

For a short-lived public demonstration, start the API without development reload in one terminal:

```bash
set -a
source .env
set +a
ENVIRONMENT=production .venv/bin/python -m uvicorn parking_ai.main:app \
  --host 127.0.0.1 --port 8000
```

Then start this in a second terminal:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Open the generated `https://*.trycloudflare.com` URL. It is an ephemeral hostname that changes on
restart and stops working when either local process exits. This Quick Tunnel adds no ParkFree
authentication, durable hostname, availability guarantee, or production privacy boundary. Anyone
with the URL can trigger Nominatim and road-provider calls. Provider adapters have bounded
in-process caches, serialization/rate gates where documented, and request limits, but ParkFree has
no per-client ingress rate limiter. Traffic traverses Cloudflare-managed infrastructure, and all
public-provider rate and acceptable-use limits still apply. Do not use a Quick Tunnel for
sensitive locations, sustained traffic, multi-worker operation, or production.

## Verification

Automated tests use injected transports and local provider-shaped fixtures. They never call live
Nominatim, Overpass, or TIGERweb.

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest -q -m 'not integration'
```

A manual live smoke test is optional and must use identifying user agents. The live result is not
part of deterministic CI because public road data and service availability change independently of
this repository.
