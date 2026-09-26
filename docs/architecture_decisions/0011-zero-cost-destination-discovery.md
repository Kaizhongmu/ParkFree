# ADR 0011: Zero-cost US destination discovery boundary

## Status

Accepted for the first nationwide-readiness slice after V1A.

## Context

The persisted destination resolver supports only canonical local records. Calling a public
geocoder from the replayable parking-search transaction would introduce unbounded latency,
ambiguous same-name selection, external failure, and a non-replayable input. Treating a resolved
place as covered would also allow an unrelated globally configured fallback to be returned for a
destination with no local GIS or regulation data.

The requested rollout must use no paid service. Public Nominatim permits modest end-user-triggered
searches under identification, attribution, caching, and application-wide rate limits, and
forbids using the public endpoint for client-side autocomplete.

## Decision

Add a separate provider-independent destination-discovery contract and an explicit same-origin
`POST /v1/destinations/search` endpoint. Use an injected Nominatim adapter with strict US-only
normalization, explicit `NO_MATCH`/`UNIQUE`/`AMBIGUOUS` results, content-derived match IDs, bounded
transport behavior, a one-request-per-second process gate, and a bounded TTL cache.

Keep `/v1/parking/search` unchanged and prohibit it from invoking the geocoder. Discovery output
is not a canonical destination, coverage declaration, legal/free conclusion, or optimizer input.
Provider payloads remain ephemeral; only validated normalized values may be retained by a future
approved persistence slice.

Require an operator-supplied identifying User-Agent before enabling the live adapter. Keep the
provider URL configurable for a future self-hosted endpoint. Public-service operation is limited
to a single process until a shared rate gate/cache is implemented.

## Consequences

- Any US place can be discovered without an API key or usage fee at low development volume.
- Ambiguous names remain explicit and cannot silently bind to the wrong city or state.
- Existing search replay, legality, availability, routing, and fallback semantics remain stable.
- Nationwide parking recommendations are still unavailable until destination selection, local
  road coverage, reviewed regulation evidence, and a geographically valid fallback are ready.
- Public-service latency and availability are degraded-operation concerns and never become legal
  or free-parking evidence.
