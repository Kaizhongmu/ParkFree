# ADR 0004: Expected-time contingent routing semantics

## Status

Accepted for Phase 5.

## Context

Parking search is sequential: travel and search time are incurred only while earlier segments have
failed. Routing providers should supply directed costs without becoming the parking decision
engine. The existing planner method must remain stable, but its arguments do not carry the
session and model versions required by the existing output schemas.

## Decision

Keep `plan_search_route(candidates, origin, destination, route_matrix)` unchanged and inject an
immutable request-scoped planning context into the planner instance. It contains honest session,
regulation, availability, target-window, and free-only metadata plus per-candidate decision
snapshots. Reject ineligible, unversioned, or snapshot-inconsistent candidate state rather than
reinterpret it.

Represent driving costs as a provider-independent directed sparse graph in seconds. Reserve
`origin` and `fallback` node IDs; use stable segment IDs for candidates. Missing edges mean
unreachable. Bind each matrix to the request origin, destination/access points, candidate set,
routing profile, and guaranteed-parking fallback. A content-derived matrix ID distinguishes the
exact cost snapshot from its provider version. The fallback cost from the current node plus
configured service time is the STOP action.

Use the Phase 2 point-to-LineString distance and 80 metres/minute for deterministic V0 walking
time. Compute a versioned expected-time objective using independent segment success probabilities,
the complete 90-second availability target window for every attempted segment, cumulative directed
drive time, terminal walking after success, and guaranteed fallback after all failures.

Provide two deterministic strategies over the same objective: a transparent greedy extension
baseline and bounded beam search. Compare exact `Decimal(str(value))` inputs, break ties by expected
time, success probability, route length, then stable IDs, and derive route/step IDs from normalized
content. Keep all computation read-only.

## Consequences

- Route order is owned by project code, never by a routing provider.
- `drive_eta_min` means the incremental leg; detailed cumulative/first-success components are
  available from the standalone expected-cost evaluator.
- STOP is available at the origin and after every completed route terminal with a fallback edge;
  beam search may cross an intermediate candidate without one.
- Success probabilities assume independence; correlation and conditional rerouting remain future
  model versions.
- V0 walking ignores pedestrian networks, crossing constraints, curb approach direction, and
  one-way access effects.
- The planner context carries immutable legality and availability decision snapshots and validates
  the flattened `ParkingSegment` decision values against them.
- Additional route diagnostics are output-only in Phase 5, so no database migration is needed.
- Phase 6 must persist/log matrix binding, optimizer configuration, decision snapshots, and route
  summary to provide full search-session replayability.
- Live providers, API orchestration, persistence workflow, and dynamic rerouting remain out of
  scope.
