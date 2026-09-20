# ADR 0006: Dependency-free local SVG map UI

## Status

Accepted for Phase 7.

## Context

Phase 7 must make Phase 6 search decisions inspectable on a map without introducing live provider
calls, leaking precise origins to a tile vendor, requiring paid services, or creating a second
build/deployment system. The response already includes normalized LineString geometry, candidate
decisions, route steps, fallback, warnings, evidence references, and version metadata.

## Decision

Serve a fixed package-local HTML document, CSS, native JavaScript, SVG favicon, and responsive SVG
map from the existing FastAPI application. Use same-origin `POST /v1/parking/search`; add no new
backend contract or database migration. Project validated longitude/latitude coordinates into an
SVG viewport and render candidate geometry, route order, destination, and a fallback only when it
falls inside local bounds. Label route connectors as schematic rather than navigation.

Read legality and payment only from each request-scoped `ParkingCandidateDecision.legality`, and
availability only from its optional prediction. Display unknown and excluded decisions alongside
eligible candidates. Treat every API string as untrusted text and construct result nodes with
`textContent`, `createElement`, and `createElementNS`; do not interpret returned HTML or URLs.

Provide a semantic route list and candidate-card list equivalent to the SVG, keyboard selection,
visible focus, text plus line-style state encoding, responsive single-column layout, reduced-motion
support, status/alert live regions, and explicit warning/fallback presentation. Set a restrictive
Content Security Policy and browser security headers on the document. Keep UI state in memory.

## Consequences

- The interface works offline after the application and local data are available, with no frontend
  compilation or third-party runtime dependency.
- The map has no street/building basemap and must remain labeled as a schematic plot with OSM/ODbL
  fixture attribution.
- Phase 7 can show evidence IDs, reason codes, evaluation time, confidence, and versions, but it
  cannot invent publisher/source metadata absent from the Phase 6 replay response.
- A deployment without database, regulation data, or verified fallback shows a safe unavailable
  state instead of fabricated results.
- Live maps, detailed evidence-source UI, analytics, user accounts, and Phase 8 AI services remain
  out of scope.
