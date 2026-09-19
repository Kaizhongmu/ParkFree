# Phase 0 Through Phase 3 Assumptions

1. PostgreSQL 16 and PostGIS 3.4 are the development baseline supplied by Docker Compose.
2. Persisted geographic coordinates use WGS84 (`SRID 4326`). Domain coordinates use GeoJSON
   longitude/latitude order while named point fields use explicit latitude and longitude.
3. Stable IDs are caller-visible strings with UUID defaults. Later ingestion may supply
   deterministic IDs without changing persistence contracts.
4. `parking_sources` is the persistence table for the domain `Evidence` entity, matching the
   implementation specification's database naming.
5. Evidence-to-segment provenance is many-to-many because one source may cover several segments
   and a segment may cite several sources.
6. Availability predictions are validated domain outputs but are not persisted in Phase 1.
   There is no predictor or generated prediction lifecycle yet; route steps retain the prediction
   values and model version actually used by a future search session.
7. The initial migration enables PostGIS but does not remove the extension on downgrade because
   an extension may be shared by other schemas in the same database.
8. Integration tests require a dedicated PostgreSQL/PostGIS database supplied through
   `TEST_DATABASE_URL`; unit tests remain database-independent.
9. Regulation evaluation, availability prediction, candidate generation, and route planning are
   typed Protocol contracts. Phase 2 implements candidate generation and Phase 3 implements
   regulation evaluation; availability prediction and route planning remain unimplemented.
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
