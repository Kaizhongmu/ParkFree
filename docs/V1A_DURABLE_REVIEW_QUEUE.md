# V1A durable human-review queue

V1A is a post-Phase 8 operational hardening slice. It makes the existing human-review boundary
durable without starting a new product phase or changing parking search. There is still no live
AI provider, request-time AI, public review API, or identity provider in this repository.

## Trust and authorization boundary

`ReviewQueueService` accepts a trusted `ReviewActor` supplied by backend code after authentication
and authorization outside this repository. The application does not accept reviewer identity or
roles from an HTTP request. Until a deployment supplies a verified identity provider and token
policy, no review-management route is exposed.

The fixed roles are:

- `SUBMITTER`: enqueue a validated Phase 8 `REVIEW_REQUIRED` result.
- `EVIDENCE_REVIEWER`: claim/release work, approve evidence-only publication, or reject.
- `REGULATION_PUBLISHER`: claim/release work and publish reviewed regulation evidence plus rules.

There is no administrator bypass. A submitter cannot review its own extraction, even when the
same actor also has a reviewer role.

## Workflow

```text
SUBMIT:   no item    -> PENDING
CLAIM:    PENDING    -> IN_REVIEW
RECLAIM:  IN_REVIEW  -> IN_REVIEW, only after the prior lease expires
RELEASE:  IN_REVIEW  -> PENDING
APPROVE:  IN_REVIEW  -> APPROVED
REJECT:   IN_REVIEW  -> REJECTED
```

`APPROVED` and `REJECTED` are terminal. Decisions require the current unexpired claim. Lease
timestamps come from the injected server clock and are normalized to UTC. Reasons are a bounded
enum; free-text review notes are intentionally absent.

Every command has a caller-supplied idempotency key, but only its SHA-256 digest is stored. Reuse
with a different normalized action fails closed. Queue revisions are one-based and optimistic;
submission is revision 1.

## Integrity and persistence

The queue stores only the strict normalized extraction result, source metadata already present in
that result, review/error codes, digests, and workflow metadata. It does not store raw source text
or images, prompts, model responses, secrets, access tokens, exception messages, or free-text
review notes.

Before submission and approval, content-derived extraction, evidence, and proposed-rule IDs are
recomputed from the normalized snapshot. On reload, the adapter revalidates the strict schemas,
snapshot digest, mirrored columns, every deterministic event ID, the full SHA-256 event hash
chain, the materialized queue projection, and the exact published evidence, segment bindings, and
rules. Database triggers make an approved publication immutable, so the regulation engine cannot
consume rows that have drifted from the approval record.

Publication and every direct write to the same evidence/rule/segment-binding identity share a
PostgreSQL transaction-level advisory lock. This closes the interval between validating a
pre-existing idempotent publication and making its queue decision terminal.

`SQLAlchemyReviewQueueRepository` never commits. The caller owns the transaction. For approval,
the reviewed evidence/rules, queue transition, and audit event are staged in one savepoint and
must be committed together. Stable-ID collisions are compared to stored content and fail closed
when the content differs.

Database protections add another layer:

- proposal fields cannot change after submission;
- resolved queue rows cannot change or be deleted;
- audit events cannot be updated or deleted;
- revisions and transitions are constrained;
- an insert trigger binds each new event to the current queue projection and prior event hash;
- the insert trigger enforces the fixed roles and submitter/reviewer separation.
- migration `0005_review_queue_event_guard` adds the reverse invariant: every inserted or updated
  queue projection must have its matching audit event by transaction commit. The constraint is
  deferred so the repository may stage the projection before the event in one transaction.

The migration aborts if an existing projection lacks its current-revision event or disagrees with
that event's status/time. It never fabricates missing audit history; an operator must investigate
and repair such a database from a trustworthy source before retrying.

Audit and queue records currently have no automatic expiry and are retained indefinitely. Any
future deletion, archival, or legal-retention policy is an operator product/security decision and
requires a new migration because database triggers deliberately prevent deletion.

## Provider and search isolation

Provider-specific payloads remain behind Phase 8 adapters. V1A adds no provider, network call, or
paid service. Review processing is offline from the parking-search request path and does not
mutate GIS-owned segment state, contextual legality/payment state, availability output, routes,
or search snapshots.

## Verification

Run static and database-independent checks:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pytest -m "not integration"
```

Run migrations and all tests against a dedicated PostgreSQL/PostGIS database:

```bash
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:PORT/DEDICATED_TEST_DB' \
  .venv/bin/pytest
```

The database suite covers a fresh full migration chain, schema constraints/indexes/foreign keys,
database immutability and bidirectional projection/event guards, durable submit/claim/approval
replay, idempotency, and atomic reviewed evidence/rule publication.
