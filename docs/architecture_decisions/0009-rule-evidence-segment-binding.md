# ADR 0009: Enforce exact rule-to-evidence segment provenance

## Status

Accepted as a post-Phase 8 safety hardening.

## Context

`ParkingRule` identifies both a segment and source evidence. The database previously enforced each
identifier independently, while the deterministic engine checked only that the evidence existed.
It was therefore possible for evidence associated exclusively with one segment to support a rule
on another segment. High-confidence mismatched evidence could produce a false legal or free
conclusion even though the many-to-many evidence association did not support that segment.

## Decision

Require every rule's `(source_evidence_id, segment_id)` pair to exist as the same
`(evidence_id, segment_id)` pair in `parking_source_segments`.

Enforce this twice:

- engine construction rejects an unbound rule before any evaluation; and
- migration `0004_rule_provenance_binding` adds a composite PostgreSQL foreign key matching
  the SQLAlchemy model metadata.

Keep the existing individual foreign keys. Make the composite key deferrable and initially
deferred so valid evidence associations and rules may be staged in either ORM flush order within
one transaction. Before adding it, count mismatched legacy rows and abort with a clear remediation
message if any exist. Do not automatically add associations, delete rules, or lower confidence,
because each of those would invent or rewrite legal provenance without an audit decision.

## Consequences

- Evidence may still cover any number of explicitly associated segments.
- A rule cannot borrow evidence from another segment in memory or in persistent storage.
- Search fails closed with the existing generic unavailable response if corrupt provenance reaches
  runtime snapshot construction; internal identifiers are not exposed to clients.
- Valid databases upgrade without data changes. A database with legacy mismatches requires a
  deliberate operator audit before migration can complete.
- The stable regulation and search interfaces do not change, and no new provider or regulation
  interpretation is introduced.
