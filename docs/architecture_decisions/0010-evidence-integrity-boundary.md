# ADR 0010: Enforce evidence chronology and source authority ceilings

## Status

Accepted as post-Phase 8 safety hardening.

## Context

The canonical `Evidence` contract previously allowed publication or observation timestamps after
retrieval. It also stored source type and reliability tier independently, so a weak source could
claim an authoritative tier and change deterministic regulation precedence. Phase 8 extraction
already generated canonical tiers, but direct domain, ORM, SQL, legacy-data, and future-adapter
paths were not protected by that private mapping.

## Decision

Define one provider-independent source-authority ceiling: official code/GIS and verified signs
may use A; university and OSM may use B; community and web may use C; imagery inference may use D.
A source may be deliberately downgraded, but it may never claim a stronger tier than its ceiling.

Require `published_at <= retrieved_at` and `observed_at <= retrieved_at`. Enforce chronology and
authority in the domain schema, again when constructing the deterministic engine, and with
PostgreSQL checks installed by migration `0006_evidence_integrity`. The engine also
checks publication time when deciding whether evidence existed at the query instant.

Migration `0006` locks `parking_sources`, audits existing rows before schema changes, and aborts
with an operator-facing example when it finds a violation. It does not alter timestamps, lower a
tier, delete evidence, or invent provenance. The engine version advances to
`regulation-engine-v2` because its accepted-input safety semantics changed.

## Consequences

- Weak sources cannot self-promote and override stronger evidence.
- Future publication/observation metadata cannot authorize a historical parking decision.
- Conservative source downgrades remain possible.
- Invalid legacy evidence requires human investigation before migration can proceed.
- Existing replay snapshots remain immutable and are not rewritten.
- Phase 8 continues to require the canonical ceiling tier for automatically normalized output,
  which is stricter than the general domain rule that permits downgrades.
