# Zero-cost on-demand parking coverage

## Purpose and current behavior

This post-Phase-8 slice removes the requirement to pre-seed every destination before showing any
local result. After a user explicitly searches for and selects a US place, the application:

1. re-runs the original Nominatim query and requires the exact selected `match_id`;
2. derives stable destination and access-point IDs from that canonical match;
3. submits one bounded Overpass query for eligible nearby road ways;
4. clips ways to the requested walking-radius boundary, then deterministically splits them at
   represented shared intersections;
5. reuses the Phase 2 generator to produce side-specific, content-derived curb-segment IDs;
6. resolves the destination's IANA timezone from bundled offline polygons;
7. runs the versioned Phase 4 baseline for each lead at the requested arrival time; and
8. expands a bounded candidate pool, removes curb fragments shorter than 6 m, preserves proximity
   order, and returns at most 20 provisional leads with conditional availability priors, model
   provenance, and provider attribution.

No persisted destination, prebuilt local parking database, SMU segment, or SMU fallback is needed
for this endpoint. The existing strict `POST /v1/parking/search` remains unchanged.

## Safety boundary

An OSM road is not proof that curb parking is legal or free. Every on-demand segment therefore has
`legal_state=UNKNOWN`, `free_state=UNKNOWN`, and `legal_confidence=0`. The response status is one of:

- `PROVISIONAL_LEADS` — road-derived curb leads are available;
- `NO_CANDIDATES` — the bounded snapshot produced no eligible roads; or
- `PROVIDER_UNAVAILABLE` — road coverage could not be acquired.

The UI renders provisional geometry and warnings but no verified route. Missing evidence never
becomes `LEGAL` or `FREE`, and a provider failure never substitutes the SMU dataset.

Availability estimates answer a narrower question: “if this curb is legal and usable, what is the
baseline chance of finding an opportunity in the model's 90-second search window?” They are not
legality probabilities, free-parking probabilities, or observations of current occupancy. The UI
ranks provisional leads by that conditional value while continuing to show UNKNOWN legality and
price. The current V0 model uses arrival-time bucket, road class, curb length/capacity fallback,
physical state, and a heuristic range; it has no local historical observations for new
destinations and is explicitly marked `UNCALIBRATED_HEURISTIC`. The UI preserves proximity order
instead of presenting this prior as a verified ranking. All predictions in one response share one
captured prediction timestamp.

The scheduled-arrival field currently interprets `datetime-local` in the user's device timezone;
the UI says so explicitly. Destination-local wall-time resolution, including DST gap/fold choices,
requires a future API contract and is not inferred silently.

This is the first operational step toward request-time research. It does not yet perform general
web search, jurisdiction-specific municipal dataset discovery, sign-image analysis, or automatic
LLM extraction. The repository's Phase 8 extractor and V1A review queue remain the required trust
boundary before external text can become authoritative parking rules.

## Provider, provenance, and storage

The initial provider is the public OpenStreetMap Overpass API. Configure identifying
application/contact values:

```text
NOMINATIM_USER_AGENT=ParkFree/0.1 (contact: operator@example.com)
OVERPASS_USER_AGENT=ParkFree/0.1 (contact: operator@example.com)
```

Both providers are disabled until their user agents are set. No API key or billing account is
required. Overpass requests are POSTed, serialized per process, rate-gated, bounded by timeout,
response size, element/road/node/tag counts, and cached in memory by rounded coordinate, radius,
and policy version. Raw JSON is ephemeral. Normalized roads retain OSM element ID, version,
timestamp, tags, ODbL license, source URI, retrieval time, and contributor attribution.

Destination timezones are resolved locally with `timezonefinder` (MIT-licensed code) and its
bundled Timezone Boundary Builder-derived data (ODbL). This avoids another request-time network or
paid API dependency and prevents all US destinations from being evaluated in the former fixed
`America/Chicago` timezone.

Public Nominatim and Overpass are suitable only for a low-volume local demo. A multi-worker or
public production deployment must add shared rate control/cache and use self-hosted services or
regional OSM extracts. See the provider policies before deployment:

- <https://operations.osmfoundation.org/policies/nominatim/>
- <https://dev.overpass-api.de/overpass-doc/en/preface/commons.html>
- <https://osmfoundation.org/wiki/Licence/Attribution_Guidelines>

## Exact local smoke test

Start the API after configuring both user agents. Resolve the place first:

```bash
curl -X POST http://127.0.0.1:8000/v1/destinations/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"The Village Chase, 5657 Amesbury Drive, Dallas, TX 75206"}'
```

Copy the returned `match_id` and submit the original query with it:

```bash
curl -X POST http://127.0.0.1:8000/v1/parking/on-demand \
  -H 'Content-Type: application/json' \
  -d '{
    "origin":{"lat":32.842,"lon":-96.784},
    "destination":{
      "query":"The Village Chase, 5657 Amesbury Drive, Dallas, TX 75206",
      "match_id":"geo_replace_me"
    },
    "arrival_time":"now",
    "parking_duration_minutes":60,
    "free_only":true,
    "max_walk_minutes":8.0,
    "vehicle_profile":{"type":"passenger","permit_types":[]},
    "max_candidates":20
  }'
```

Expected: HTTP 200 and a typed status. `PROVISIONAL_LEADS` contains only candidates around the
selected place; every candidate remains unknown for legal/payment state. When timezone resolution
succeeds, `availability_predictions`, `availability_assumption`, `calibration_status`, and
`destination_timezone` make the conditional model output explicit.

## Verification

Automated tests use injected transports and local provider-shaped fixtures. They never call live
Nominatim or Overpass.

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest -q -m 'not integration'
```

A manual live smoke test is optional and must use identifying user agents. The live result is not
part of deterministic CI because public OSM data and service availability change independently of
this repository.
