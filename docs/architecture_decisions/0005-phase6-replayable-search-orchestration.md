# ADR 0005: Replayable end-to-end search orchestration

## Status

Accepted for Phase 6.

## Context

Phases 2 through 5 provide deterministic candidate generation, regulation evaluation,
availability estimation, and route planning behind stable provider-independent interfaces. Phase
6 must compose them without collapsing `UNKNOWN` into a positive conclusion, while retaining
enough request-scoped state to reproduce what the system returned. Route costs and a genuinely
guaranteed fallback also need an offline development implementation without turning an external
routing provider into the parking decision engine.

## Decision

Expose `POST /v1/parking/search` with a strict typed request. Require one destination selector, an
aware timestamp or `now`, a positive parking duration, bounded walking/candidate limits, and a
vehicle profile. Resolve `now` once. Evaluate every deterministic candidate, but call availability
and the optimizer only for candidates whose legality is `LEGAL` and whose payment state is known
and compatible with `free_only`. Return all candidate decisions, including explicit unknowns.

Compose dependencies in a request-scoped database transaction. Keep the existing four conceptual
service contracts unchanged. Read destinations, segments, normalized rules, and evidence through
PostGIS adapters. Ignore legacy flattened contextual fields on `street_segments` and never write
search results back to those rows.

Use a versioned local straight-line matrix for the offline Phase 6 runtime. Bind and content-hash
its origin, destination, sorted eligible candidate IDs, exact costs, routing profile, and explicit
operator-configured fallback. Fail closed when the fallback is not configured. Label the
approximation in every response.

Persist a replayable search session containing the resolved request, all candidate decisions,
matrix, optimizer configuration, route, response, version metadata, and normalized route steps.
Store only a SHA-256 idempotency-key digest. Reuse with the same logical request returns the stored
response; reuse with a different request returns a conflict. On replay, validate every schema and
recompute request, matrix, and aggregate artifact hashes before returning data.

## Consequences

- Missing or conflicting regulation/payment evidence remains visible and is never routed.
- The initial OSM fixture alone produces a fallback-only search, not a legal-parking claim.
- Results are transactional, idempotent, auditable, and protected against internally inconsistent
  replay snapshots.
- The local matrix is deterministic and network-free but does not model the road network,
  crossings, turns, traffic, or one-way approach constraints.
- A deployment must supply and verify a real guaranteed-parking fallback and ingest authoritative
  regulation evidence before the API can provide useful parking candidates.
- Phase 7 frontend, Phase 8 AI extraction, live routing, availability training, and outcome APIs
  remain out of scope.
