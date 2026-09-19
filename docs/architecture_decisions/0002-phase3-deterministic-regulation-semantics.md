# ADR 0002: Deterministic interval regulation semantics

## Status

Accepted for Phase 3.

## Context

Parking legality depends on the whole requested stay, local wall-clock schedules, user permits,
rule authority, and explicit payment evidence. A point-in-time or absence-based implementation
could incorrectly recommend a segment that becomes restricted during the stay. Regulation output
also needs to remain distinct from future availability prediction and from the persisted Phase 2
GIS defaults.

## Decision

Implement regulation evaluation as a pure deterministic service behind the existing
`LegalityService.evaluate_legality(segment, user_profile, datetime)` contract. Requested duration
remains in `UserProfile`; normalized `ParkingRule` and `Evidence` snapshots are injected when the
engine is constructed.

Use `America/Chicago` as the configurable Phase 3 schedule timezone. Convert aware arrivals to
UTC, add requested duration as elapsed time, and compare half-open UTC intervals generated from
local schedules. Anchor overnight schedules to their start day. Treat effective start and end
dates as inclusive for that anchor day.

Partition the requested stay at every active rule boundary. Resolve legality and payment
independently in each atomic time slice, applying evidence tiers A through D per axis, then
aggregate the slices. Direct same-tier contradictions are unknown; explicit restrictions compose
with permissions. A valid `NO_PARKING` exception removes that restriction but does not create
permission. Only explicit payment evidence can produce `FREE` or `PAID`. Missing duration cannot
produce positive `LEGAL` or `FREE` status.

Use a deterministic source-specific evidence freshness policy, preferring `observed_at` over
`retrieved_at`. Do not automatically expire official code because the rule effective-date window
governs it. Default maximum ages for the other source categories are documented and callers may
inject source-specific overrides that merge into the defaults. Freshness must hold through the
requested departure; stale active evidence fails closed to `UNKNOWN`.
Evidence observed or retrieved after the query instant also fails closed to prevent look-ahead in
historical and reproducible evaluations.

Return stable typed reason codes, sorted evidence references, deterministic confidence derived
from the decisive rules, and a content-derived evaluation ID. Do not mutate or persist the
evaluation in Phase 3.

## Consequences

- Missing coverage and unresolved material conflicts remain visible as `UNKNOWN`.
- DST gaps and repeated times use real elapsed duration rather than naive wall-time arithmetic.
- A wall-time window wholly inside a repeated fall-back hour is represented once in each fold,
  without inventing a restriction between the two occurrences.
- Phase 2 segment legal/free defaults and future availability columns remain untouched.
- The existing database schema already stores all required normalized rule/evidence fields, so
  Phase 3 needs no Alembic migration.
- Loading rules from persistence or provider adapters can be added around the injected snapshot
  without changing the stable evaluation contract.
