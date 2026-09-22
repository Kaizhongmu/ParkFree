# Phase 8 AI evidence services

Phase 8 adds bounded, provider-independent extraction services for regulation text, community
claims, and imagery/sign observations. It does not add a live model provider, make a legal
decision, or call AI during parking search.

## Safety boundary

The workflow has two explicit stages:

```text
untrusted provider output
→ strict Phase 8 response schema
→ validated proposal or quarantine
→ explicit human review
→ approved evidence bundle
→ idempotent evidence/rule persistence
→ deterministic regulation engine
```

All three services return `REVIEW_REQUIRED` for valid output. Schema failures, adapter failures,
and extractor-version mismatches return `QUARANTINED` without evidence or rules. Validation error
codes do not include raw provider output or exception messages.

Only a reviewed regulation extraction may publish `ParkingRule` records. Reviewed community and
imagery results remain evidence-only. A vision inference cannot label itself `VERIFIED_SIGN`;
that upgrade requires a separately sourced trusted verification record.

The existing legality engine and its public interface are unchanged. AI output never mutates a
legality evaluation, candidate segment, availability result, route, or search response.

## Provider adapters

Adapters implement one small synchronous protocol and return an untrusted object. Provider
payload formats, prompting, authentication, retry behavior, and timeouts remain adapter-owned.
The core service accepts only the strict provider-independent response schemas.

No OpenAI, vision, community, or web provider is configured in this phase. Tests use deterministic
in-process adapters, so test and request execution are network-free and incur no paid-service
cost.

## Source authority and storage policy

Trusted caller metadata determines source type, reliability tier, source identifier, publisher,
timestamps, segment binding, and raw-storage policy. Extractor output cannot choose or elevate
these values.

| Source type | Tier | Phase 8 behavior |
|---|---:|---|
| `OFFICIAL_CODE`, `OFFICIAL_GIS` | A | Regulation proposal; human review required |
| `UNIVERSITY`, `OSM` | B | Regulation proposal; human review required |
| `WEB` | C | Regulation proposal; human review required; raw `PERSIST` forbidden |
| `COMMUNITY` | C | Evidence claim only; raw `PERSIST` forbidden |
| `IMAGERY_INFERENCE` | D | Evidence claim and non-publishable rule proposal; raw `PERSIST` forbidden |
| `VERIFIED_SIGN` | A | Not assignable by the vision extractor |

`PERSIST` raw-content policy is accepted only for official code, official GIS, university, and
OSM material. This repository stores the normalized reviewed `Evidence` record and policy marker,
not the supplied raw text/image content itself. `EPHEMERAL` and `REFERENCE_ONLY` remain available
for every supported adapter.

## Determinism and provenance

Evidence, extraction-result, approval, and rule IDs are SHA-256 content-derived identifiers.
Canonical identity includes material source metadata, timestamps, storage policy, sorted segment
IDs, source-content hash, extractor version, and sorted normalized claims. Input order and Python
hash randomization do not affect IDs; material content, claim, metadata, reviewer, or review-time
changes do.

Approval creates a new reviewed evidence identity and appends a structured
`HUMAN_REVIEW_APPROVAL` claim containing the original evidence ID, extraction result ID, reviewer,
review timestamp, approval scope, and approval ID. Approved rules reference that reviewed evidence.

## Persistence

`persist_approved_evidence` accepts only `ApprovedEvidenceBundle`; raw extraction results and
quarantined output do not satisfy its type contract. It inserts reviewed evidence, segment
associations, and approved rules with conflict-safe stable IDs, never commits the caller's
transaction, and never updates `street_segments`.

The Phase 1 schema already contains every required normalized evidence/rule field, so Phase 8
itself added no Alembic migration. The later V1A hardening slice adds a durable least-privilege
review queue without changing the Phase 8 extractor contracts; see
[`V1A_DURABLE_REVIEW_QUEUE.md`](V1A_DURABLE_REVIEW_QUEUE.md). Raw-source archives remain absent.

## Verification

```bash
ruff check .
ruff format --check .
mypy src
pytest -m "not integration"
TEST_DATABASE_URL=<dedicated-postgis-url> pytest
```

The integration test starts from a freshly migrated database, proves that an unreviewed extraction
does not affect the rule engine, publishes an explicitly reviewed regulation bundle twice, checks
idempotent evidence/rule/association counts, confirms segment decision fields remain untouched,
and verifies that the deterministic engine can then read the approved rule.
