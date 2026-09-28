# ADR 0012: Separate on-demand provisional road coverage

Status: Accepted after ADR 0011.

## Context

ADR 0011 intentionally stopped destination discovery before parking coverage. Requiring operators
to pre-seed every US destination does not satisfy an on-demand product. At the same time, passing a
geocoder coordinate into the strict Phase 6 optimizer would either reuse unrelated persisted data
or imply legal/free knowledge that the system does not have.

## Decision

Add a separate `POST /v1/parking/on-demand` boundary. It revalidates the original query and exact
provider-derived match ID, acquires a bounded OSM road snapshot through a provider-independent
contract, and reuses the deterministic candidate generator. It does not write to the canonical
parking database and does not invoke the strict Phase 6 optimizer.

The endpoint returns only provisional leads. Legality, payment state, and confidence remain
`UNKNOWN`, `UNKNOWN`, and zero until trusted regulation evidence is evaluated. Provider failure or
empty coverage never triggers a cross-location fallback.

The initial Overpass adapter is zero-cost and suitable for low-volume development. It uses bounded
POST requests, an identifying user agent, an in-memory TTL cache, per-process rate gating, strict
normalization, and OSM attribution/provenance. Automated tests inject transports and never call the
live service.

The official U.S. Census TIGERweb Transportation REST service is the zero-key road-geometry
fallback. It is called after an Overpass failure or empty snapshot, or directly when Overpass is
not configured. It supplies nationwide road centerlines but no curb rules, so all derived
legal/free states remain UNKNOWN. Responses expose only sanitized provider attempt names, roles,
and outcomes. HTTPS transports use the packaged `certifi` trust store rather than assuming a
machine-specific Framework Python CA file exists.

## Consequences

- Any selected US destination can produce local road-derived curb geometry without a prebuilt row.
- The existing replayable `/v1/parking/search` contract and its database trust boundary remain
  stable.
- The UI can show useful local geometry immediately but cannot call it verified free parking.
- General web discovery, jurisdiction-specific source adapters, local-model extraction, reviewed
  rule publication, nationwide time-zone resolution, and verified routing remain later slices.
- Production scale requires shared throttling/cache and self-hosted or bulk OSM data rather than a
  public Overpass dependency.
