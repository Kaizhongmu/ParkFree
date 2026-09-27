# Zero-cost US destination discovery

This post-Phase-8 slice adds provider-independent, US-only destination discovery without adding a
paid service or changing the deterministic parking-search contract.

## Boundary

`POST /v1/destinations/search` resolves an explicitly submitted place or address into zero, one,
or multiple normalized matches. It never silently chooses the first result. A successful match
means only that the place was resolved; it does **not** mean candidate-road, regulation, verified
fallback, or parking-availability coverage exists there.

`POST /v1/parking/search` remains cached-data-only. It never invokes geocoding, OSM retrieval, an
AI extractor, or another live provider. Only persisted destinations and approved evidence may
affect its deterministic response. The separate `POST /v1/parking/on-demand` endpoint can recheck
an exact selected match and prepare provisional live road coverage without changing that contract.

## Zero-cost provider policy

The initial adapter targets the public OpenStreetMap Nominatim service and is disabled until an
identifying `NOMINATIM_USER_AGENT` is configured. No API key or billing account is required.

The adapter:

- fixes the country filter to the United States;
- sends a recognizable application `User-Agent`;
- allows only explicit submitted searches, never client-side autocomplete;
- serializes cache misses and starts at most one provider request per second per application
  process;
- applies bounded timeouts, response size, result count, and cache size;
- caches normalized positive and negative results in memory for a bounded TTL;
- makes the provider endpoint operator-configurable so self-hosting does not require code changes;
- persists no raw response and exposes OSM attribution and ODbL policy metadata;
- rejects malformed or non-US matches as an all-or-nothing response.

The public Nominatim service is suitable only for low-volume, single-process development. A
multi-worker deployment must first implement a shared rate gate/cache or use a self-hosted
instance. This repository does not claim a production SLA for the public service.

## Request

Configure a non-generic application identity in `.env`:

```text
NOMINATIM_USER_AGENT=ParkFree/0.1 (contact: operator@example.com)
```

Start the API, then submit a destination search:

```bash
curl -X POST http://localhost:8000/v1/destinations/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"Seattle Center"}'
```

The response status is `NO_MATCH`, `UNIQUE`, or `AMBIGUOUS`. Every match has a content-derived
`match_id`, normalized label/address, WGS84 coordinate, source reference, and country code. The
query is sent in the request body so it is not placed in browser history or the usual access-log
URL.

## Testing policy

All automated tests inject provider-shaped local JSON values and fake clocks/transports. Unit,
integration, and CI tests must never call live Nominatim. A manual live smoke test is optional,
must use an identifying `User-Agent`, and must respect the public usage policy.

## On-demand continuation

Destination selection and bounded OSM road-coverage preparation are implemented in the separate
on-demand path described in `ON_DEMAND_PARKING.md`. Regional fallback verification and automatic
reviewed regulation acquisition remain separate work. A nationwide discovery result is therefore
never passed directly to the strict parking optimizer or presented as verified free parking.
