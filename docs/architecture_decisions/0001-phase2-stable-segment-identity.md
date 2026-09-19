# ADR 0001: Canonical geometry and content-derived segment identity

## Status

Accepted for Phase 2.

## Context

Parking segments need persistent IDs that survive input ordering, database row ordering, Python
hash randomization, and OSM way direction changes. The initial fixture also needs deterministic
left/right handling before authoritative curb geometry exists.

## Decision

Normalize each split centerline by rounding WGS84 coordinates to seven decimal places, removing
adjacent duplicates, and selecting the lexicographically smaller of forward and reversed order.
Interpret `LEFT` and `RIGHT` against that canonical direction. Hash the ID-scheme version,
normalized geometry, normalized street name, road type, and side with SHA-256; expose the first 32
hexadecimal characters with a `seg_` prefix.

Do not include source row order, provider feature ID, data freshness, evidence ID, physical state,
or legal/free state in segment identity.

## Consequences

- Reordering fixture ways or reversing source geometry does not change IDs.
- Geometry, street identity, road class, or side changes produce a new ID.
- Updated evidence and physical observations can upsert the same segment.
- Left/right is stable but refers to canonical geometry direction until true curb geometries are
  introduced.
- Changing rounding precision or hash inputs requires an explicit ID-scheme version migration.
