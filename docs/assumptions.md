# Phase 0 Through Phase 8 and V1A Assumptions

1. PostgreSQL 16 and PostGIS 3.4 are the development baseline supplied by Docker Compose.
2. Persisted geographic coordinates use WGS84 (`SRID 4326`). Domain coordinates use GeoJSON
   longitude/latitude order while named point fields use explicit latitude and longitude.
3. Stable IDs are caller-visible strings with UUID defaults. Later ingestion may supply
   deterministic IDs without changing persistence contracts.
4. `parking_sources` is the persistence table for the domain `Evidence` entity, matching the
   implementation specification's database naming.
5. Evidence-to-segment provenance is many-to-many because one source may cover several segments
   and a segment may cite several sources.
6. Availability predictions are validated domain outputs but are not persisted through Phase 4.
   Route steps retain the prediction values and model version actually used by a future search
   session; the prediction persistence lifecycle remains deferred.
7. The initial migration enables PostGIS but does not remove the extension on downgrade because
   an extension may be shared by other schemas in the same database.
8. Integration tests require a dedicated PostgreSQL/PostGIS database supplied through
   `TEST_DATABASE_URL`; unit tests remain database-independent.
9. Regulation evaluation, availability prediction, candidate generation, and route planning are
   typed Protocol contracts. Phase 2 implements candidate generation, Phase 3 implements
   regulation evaluation, Phase 4 implements availability prediction, and Phase 5 implements
   deterministic route planning.
10. Phase 2 approximates walking reach as straight-line distance from the nearest configured
    destination access point at 80 meters per minute. A pedestrian-network adapter is deferred.
11. Candidate road classes are `living_street`, `residential`, `secondary`, `tertiary`, and
    `unclassified`. A fixture may explicitly include or exclude a road; primary and ordinary
    service roads are excluded by default.
12. Segment geometry is rounded to seven decimal places, de-duplicated, and oriented by the
    lexicographically smaller endpoint sequence. Left/right therefore refers to that canonical
    direction rather than source-way direction.
13. Stable segment IDs are SHA-256 digests of normalized geometry, normalized street name, road
    type, side, and ID-scheme version. Physical observations and evidence freshness may change
    without changing the physical segment identity.
14. A generated candidate defaults to `UNKNOWN` physical state unless explicitly curated in the
    fixture. Legal state and free state always remain `UNKNOWN` with confidence `0.0` in Phase 2.
15. `SearchConstraints.free_only` is intentionally not applied during candidate generation;
    filtering on free status belongs to the future deterministic regulation stage.
16. The package-local SMU road fixture is a reduced OSM-derived snapshot retained with
    OpenStreetMap attribution under ODbL 1.0. Source OSM node/way identifiers and tags remain
    traceable in the fixture; clipped way fragments use `<osm-way-id>#<fragment-number>` IDs.
    It is reproducible offline test data, not a live or authoritative statement of road or parking
    conditions.
17. The Phase 2 slice retains eligible road ways touching 550 m from Fondren Library and clips
    them at existing OSM nodes within 700 m. This focused slice does not claim full coverage of the
    eventual 1–1.5 km MVP geography.
18. Fondren's destination centroid and access points are derived from OSM building way
    `443633904` and tagged entrance nodes `6080676255` and `11289496087`. SMU's official site
    corroborates the destination identity and address; west/east are descriptive labels rather
    than official entrance names.
19. OSM road class supports candidate generation but does not establish physical curb
    feasibility. All roads in the Phase 2 fixture therefore default to `physical_state=UNKNOWN`.
20. Phase 3 evaluates local regulation schedules in `America/Chicago`. Arrival values may use any
    aware timezone and are converted to that configured local timezone. Requested durations are
    elapsed minutes on the UTC timeline so DST changes do not add or remove real parking time.
21. Requested stays and rule windows are half-open intervals: the start is included and the end
    is excluded. An overnight rule belongs to its start day, including effective-date checks.
    Empty weekday lists mean every day.
22. If requested duration is omitted, the engine can still establish an active prohibition or an
    active payment requirement at arrival. It cannot establish positive `LEGAL` or `FREE` status
    for an unknown-length stay.
23. A `PAID` rule is treated as an affirmative designated-parking rule when it covers the entire
    requested stay. It therefore may establish `LEGAL` while independently establishing `PAID`,
    `FREE`, or `UNKNOWN` payment state from its explicit payment field.
24. Explicit `payment_required=False` evidence is required for `FREE`. The end of a paid window,
    the absence of a paid rule, or the absence of any rule never implies free parking.
25. Evidence precedence is applied independently to legality and payment within each atomic time
    slice of the requested stay. Same-tier direct contradictions return `UNKNOWN`; active
    prohibitions compose with permissions and exclude a stay. Extraction confidence is reported
    deterministically and is not a legality probability.
26. A valid exception to `NO_PARKING` removes that prohibition but does not alone prove general
    legality. Permit and vehicle exceptions must use supported structured exception types and
    exact case-insensitive values; unknown exception formats are ignored.
27. Regulation evaluation is a pure, read-only operation over injected validated rule and
    evidence snapshots. Phase 3 does not persist contextual evaluations or overwrite the
    GIS-owned or future availability-owned fields on `street_segments`.
28. Phase 3 uses source-specific default evidence maximum ages: official code has no automatic
    age expiry because rule effective dates govern it; official GIS and university evidence use
    365 days; verified signs and OSM use 180 days; web and imagery evidence use 90 days; community
    evidence uses 30 days. `observed_at` is preferred over `retrieved_at`, and freshness must hold
    through the requested departure. Caller overrides merge into these defaults; an explicit
    positive limit or `None` changes one source for another jurisdiction. Evidence timestamped
    after the requested arrival is unavailable for that evaluation and fails closed.
29. Phase 4 availability is conditional on the caller separately establishing that a parking
    opportunity is legal for the requested stay. The predictor does not inspect or change legal or
    payment state and cannot turn an unknown/illegal segment into a recommendation.
30. V0 predicts at least one physical opportunity in a 90-second base window. Per-space priors are
    0.08 weekday peak, 0.14 weekday shoulder, 0.22 weekday night, 0.16 weekend day, and 0.24 weekend
    night. Road multipliers are 1.00 local, 0.85 unclassified, 0.70 tertiary, 0.60 secondary, and
    0.80 other/missing. These are transparent assumptions, not fitted SMU estimates.
31. Explicit capacity is preferred. Missing capacity uses segment length divided by 28 metres,
    clamped to one through four effective opportunities. `NOT_PARKABLE` and explicit zero capacity
    return zero; unknown physical state remains nonzero but explicit and weakens the prior.
32. Optional historical input is a typed 90-second success/trial aggregate bound to the same
    segment, local time bucket, and versioned `SEGMENT_TIME_BUCKET` scope. V0 uses Beta shrinkage
    with prior strength 6 when capacity and parkability are known, 3 when one is known, and 2 when
    neither is known. Population mismatches, future-dated aggregates, and open-ended feature
    dictionaries are rejected.
33. The `beta-normal-95-v1` band is deterministic heuristic uncertainty, not a validated confidence
    interval. Non-90-second windows use `1 - (1 - p90) ** (seconds / 90)`, which assumes independent
    constant opportunities over time.
34. Phase 4 predictions contain a normalized feature snapshot, reason codes, feature/model/
    uncertainty versions, and a content-derived ID that includes the uncertainty-method version.
    The wall-clock prediction timestamp is excluded from the ID. Evaluation batches must have one
    model version, feature schema, and target window and unique outcome IDs. Phase 4 adds no
    database migration and does not update `street_segments`.
35. Phase 5 matrices are directed sparse driving-time graphs in finite nonnegative seconds.
    `origin` and `fallback` are reserved node IDs; stable segment IDs name all candidate nodes.
    Missing edges mean unreachable and do not default to zero.
36. The fallback node represents guaranteed parking. STOP from a node costs its directed fallback
    edge plus a default five-minute terminal service allowance. Every completed route must end at
    a candidate with a fallback edge; beam search may traverse an intermediate candidate without
    one when a later reachable candidate restores the fallback path.
37. Phase 5 assumes independent candidate success events. Each successful or failed segment
    attempt consumes the full common availability target window, 90 seconds by default, because
    no conditional time-to-success distribution exists yet.
38. Walking time uses deterministic straight-line point-to-LineString distance to the nearest
    destination access point at 80 metres per minute, or the destination centroid when access
    points are absent. Pedestrian-network routing remains deferred.
39. The planner validates but does not create legality, payment, or availability state. It requires
    `LEGAL`, a known payment state compatible with the request-scoped free-only setting, and a
    non-null availability probability. Zero-probability segments are not routing waypoints.
40. The stable four-argument planner method is preserved. Session ID, rule/model versions,
    availability target window, free-only behavior, and per-candidate regulation/availability
    decision snapshots are injected through an immutable request-scoped context; strategy and
    cost behavior use immutable versioned configuration.
41. Greedy and bounded beam strategies minimize the same expected-time objective. Exact Decimal
    comparisons and stable-ID tie-breaking make results independent of list/map order and Python
    hash randomization. `drive_eta_min` is an incremental leg, not cumulative time.
42. Matrices are request-bound and content-addressed separately from their provider version. Route
    and step IDs are also content-derived. Phase 5 route diagnostics and decision snapshots are
    contextual output and are not persisted, so no Alembic migration is required. Phase 6 must
    persist/log the matrix binding, optimizer configuration, decision snapshots, and route summary
    before claiming full search-session replayability.
43. Phase 6 requires an explicit positive parking duration. The HTTP boundary accepts either an
    aware timestamp or the literal `now`; `now` is resolved once per request and the resolved time
    is persisted. For idempotency comparison, the logical `now` token is hashed rather than the
    wall-clock resolution so a retry can replay the original result.
44. A candidate reaches availability prediction and route optimization only when legality is
    `LEGAL` and payment is known. `free_only=true` additionally requires `FREE`; `free_only=false`
    permits `FREE` or `PAID`. `UNKNOWN` is returned in candidate decisions and never promoted into
    a recommendation.
45. The default Phase 6 matrix provider is an offline deterministic approximation: great-circle
    distance between representative points divided by a configured fixed speed. It is explicitly
    versioned and warned in every response; live network routing remains a later adapter concern.
46. A guaranteed fallback is mandatory for search. Its ID, description, and coordinates are
    operator configuration, not inferred from the SMU fixture. Search fails closed with HTTP 503
    when it is absent; the placeholders remain commented out in `.env.example`.
47. The Phase 2 OSM fixture describes roads, not authoritative parking regulations. A database
    containing only that fixture produces explicit `UNKNOWN` decisions and a fallback-only route.
    Tests add synthetic, clearly labeled regulation evidence rather than asserting real SMU curb
    legality.
48. Phase 6 stores only a SHA-256 digest of an idempotency key. A matching key and normalized
    request replays the original response; reuse with a different request is a conflict. Search
    request, candidate decisions, route matrix binding/costs, optimizer configuration, route, and
    response are persisted as versioned JSONB snapshots plus normalized route-step rows.
49. Replay validation rehydrates every stored schema and recomputes the request hash, route-matrix
    content ID, and complete artifact hash. It also cross-checks database metadata and route-step
    rows. Corrupt or incompatible snapshots fail closed rather than silently replaying.
50. Contextual legality and availability values are request-scoped. PostGIS candidate hydration
    deliberately ignores legacy flattened legal/free/confidence/availability columns, and Phase 6
    persistence never updates those GIS-owned segment rows.
51. Phase 7 is served from the existing FastAPI process as package-local HTML, CSS, JavaScript,
    SVG, and favicon assets. It introduces no Node runtime, frontend build pipeline, CDN, external
    font, live tile service, analytics, cookie, or local-storage dependency.
52. The map is a deterministic schematic geographic plot of response LineStrings, destination,
    and route order. It is not a road basemap or turn-by-turn navigation. A possibly remote
    fallback is excluded from automatic map bounds and remains visible in the semantic fallback
    card with coordinates when supplied.
53. UI state comes from `candidate_decisions[].legality` and `.availability`; the intentionally
    stale/unknown flattened contextual fields on `candidate_decisions[].segment` are never used to
    color or recommend a curb.
54. Phase 7 provenance means the safe traceability already present in the Phase 6 replay response:
    evidence reference IDs, regulation reason codes, evaluation time, confidence, and version
    metadata. Publisher names, source URLs, reliability tiers, and evidence observation timestamps
    are not exposed by the stable Phase 6 response and are not invented or fetched by the UI.
55. Color never carries curb meaning by itself. Text state labels, line style, availability
    percentages, route numbers, an SVG description, and a complete keyboard-readable candidate
    and route list provide equivalent semantics without the map.
56. Browser geolocation is requested only after the user activates “Use my location.” Coordinates
    remain in page memory and the same-origin request; the UI does not place them in URLs,
    localStorage, analytics, or console logs. Operator transport security remains a deployment
    responsibility.
57. One idempotency key is reused only while retrying an uncompleted identical request. A completed
    request clears that retry identity so a later `now` search creates a fresh session. Starting a
    new request clears the previous result and failures remain fail-closed with no stale plan shown.
58. Phase 8 is a provider-independent extraction and review boundary, not a live provider rollout.
    No model, community, web, or image service is called by default or during parking search.
59. Source type, publisher, timestamps, segment binding, storage policy, and resulting reliability
    tier are trusted caller metadata. An extractor response cannot select or elevate them.
60. Every valid AI extraction requires explicit human review. Only regulation extraction can be
    approved for rule publication; community and imagery results remain evidence-only, and vision
    cannot self-promote an inference to `VERIFIED_SIGN`.
61. Phase 8 source text/image content exists only in the in-memory adapter request. The repository
    persists its SHA-256 hash, normalized reviewed evidence, provenance, and policy marker—not the
    raw content. Raw `PERSIST` policy is forbidden for web, community, and imagery sources.
62. Evidence, proposal, approval, and approved-rule IDs are content-derived from canonical sorted
    inputs. Material source metadata, timestamp, claim, extractor, reviewer, or review-time changes
    create a different identity; input ordering does not.
63. The existing Phase 1 evidence and rule tables are sufficient for approved normalized output.
    Phase 8 adds no migration and no durable quarantine/review queue. Such an operational queue
    requires a later access-control, retention, and reviewer-identity design.
64. Approved evidence ingestion is append-only and idempotent by stable identity. It never updates
    GIS-owned segment data or contextual legality, payment, availability, and routing results.
65. V1A is an operational hardening slice after Phase 8, not a new numbered product phase. It adds
    durable review workflow only and does not add a provider, public review API, or search-path AI.
66. Reviewer identity and roles are trusted backend inputs established outside this repository.
    Because no identity provider is configured, accepting reviewer identity from HTTP is forbidden.
67. Review roles are fixed to submitter, evidence reviewer, and regulation publisher. There is no
    administrator bypass, and submitter/reviewer separation is mandatory.
68. Claims use a 15-minute server-clock lease by default. Expired work may be reclaimed with an
    explicit audited reason; approval and rejection require the current unexpired claim.
69. Normalized queue snapshots and append-only audit events are retained indefinitely. Raw source
    content, prompts, provider responses, secrets, exception text, and free-text notes are not
    stored. A future retention/deletion policy requires explicit operator approval and migration.
70. The caller owns the SQLAlchemy transaction. Approval publication, queue state, and its audit
    event are committed or rolled back together; the repository adapter never commits implicitly.
71. A parking rule may use evidence only when the exact `(evidence_id, segment_id)` pair exists in
    `parking_source_segments`. Evidence may legitimately cover multiple segments, but a binding to
    one segment never authorizes another. Engine construction and the deferred database constraint
    both fail closed; migration `0004` aborts on legacy mismatches instead of manufacturing
    provenance or silently deleting rules.
72. Every durable review-queue projection revision must have one matching audit event. Migration
    `0005` enforces this reverse edge with a deferred constraint trigger; legacy inconsistencies
    abort migration and are never repaired by synthesizing audit history.
73. A regulation evaluation consumed by search must represent the same instant as the requested
    arrival. A planner may select only candidates that passed the request's legality/payment filter,
    and it must return their exact request-scoped legality and availability snapshots.
74. Replayable normalized route-step rows are integrity mirrors, including legality evaluation ID,
    availability prediction ID, and availability target window. A mismatch in any mirror fails
    replay even when the JSON snapshots remain internally valid.
75. The Phase 7 HTML entrypoint is served only by `/` with its security headers. `/assets` is an
    allowlist of the package CSS, JavaScript, and favicon and must not expose `index.html`.
