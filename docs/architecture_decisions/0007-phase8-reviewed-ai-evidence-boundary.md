# ADR 0007: Reviewed boundary for AI-derived evidence

## Status

Accepted for Phase 8.

## Context

The regulation engine loads every persisted `parking_rules` row for a candidate segment and does
not know whether a rule was AI-generated or reviewed. Community and imagery evidence can also be
decisive when no higher tier is present. Persisting model proposals directly would therefore let
an AI response become the sole legal basis, contrary to the architecture specification.

Provider formats, model choices, and content-retention rights are not yet selected. Phase 8 still
needs reproducible contracts that can reject malformed output and preserve provenance without
adding network or paid-service dependencies.

## Decision

Add strict provider-independent extraction schemas and injected adapters for regulation,
community, and vision services. Treat adapters and their output as untrusted. Trusted caller
metadata fixes source type, reliability tier, storage policy, timestamps, and segment binding;
extractors cannot elevate authority.

Every valid AI result requires explicit human review. Quarantined results expose no evidence or
rules. Human approval creates a new content-derived evidence identity containing structured
review provenance. Only regulation extraction can receive `EVIDENCE_AND_RULES` approval.
Community and imagery results are evidence-only, and vision cannot self-promote to a verified sign.

Persist only `ApprovedEvidenceBundle` values into the existing `parking_sources`,
`parking_source_segments`, and `parking_rules` tables. Keep extraction and review outside the
search request path. Do not change the stable regulation/search interfaces or persisted segment
decision fields.

## Consequences

- AI assists reading and normalization but cannot approve or execute its own legal conclusions.
- Invalid output fails closed with deterministic, non-sensitive error codes.
- Approved ingestion is repeatable and order-independent.
- Existing Phase 1 tables are sufficient; Phase 8 needs no migration.
- There is no live model/provider, persistent quarantine queue, raw-content archive, automatic
  sign verification, or request-time AI in this phase.
- A future operational review queue requires explicit authorization, access control, retention,
  and reviewer-identity policy before adding durable workflow tables or endpoints.

ADR 0008 records the later V1A decision that satisfies those prerequisites for a trusted backend
workflow. It deliberately adds no public endpoint because no identity provider is configured.
