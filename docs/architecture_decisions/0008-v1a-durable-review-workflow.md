# ADR 0008: Durable least-privilege evidence-review workflow

## Status

Accepted for V1A after Phase 8.

## Context

Phase 8 deliberately stopped at an in-memory explicit-review boundary because the repository had
no authorization, retention, or reviewer-identity policy. Operations now need a restart-safe way
to claim, decide, audit, and atomically publish those proposals. The repository still has no
identity provider suitable for authenticating public reviewer requests.

## Decision

Add a provider-independent review state machine and a SQLAlchemy event-store adapter. Trusted
backend callers inject immutable actor IDs and fixed roles. Do not add a public review HTTP API.
Require claim leases, four-eyes separation, terminal decisions, bounded reason codes, hashed
idempotency keys, content-derived identities, and an append-only per-item hash chain.

Persist normalized `REVIEW_REQUIRED` snapshots only. Revalidate their content-derived IDs before
publication. Stage evidence/rule publication, queue projection, and the audit event in one
caller-owned transaction. Add PostgreSQL constraints and triggers for immutable proposals,
terminal rows, append-only events, role separation, revision continuity, and prior-hash binding.

Do not persist raw source content, prompts, provider responses, credentials, exception messages,
or free-text notes. Retain workflow/audit rows indefinitely until an explicit operator retention
policy is approved.

## Consequences

- Review work survives process restarts and can be replayed and verified deterministically.
- AI output still cannot approve itself or directly decide parking legality.
- The database schema advances through migration `0003_v1a_evidence_review_queue`.
- A future authenticated management surface must verify identities outside this service and may
  not accept actor roles from request bodies.
- Automated retention, deletion, reopening terminal work, and administrator bypass remain absent.
