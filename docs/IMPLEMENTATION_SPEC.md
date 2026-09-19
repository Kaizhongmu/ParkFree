# Parking Intelligence System
## Engineering & AI Coding Implementation Specification — V1

**Initial geography:** SMU campus and approximately 1–1.5 km surrounding area  
**Primary objective:** Minimize expected time-to-park while respecting free/legal parking constraints  
**Architecture:** Modular deterministic core + bounded AI intelligence services  
**Primary implementation audience:** Codex / AI coding agents / engineers

---

# 1. Product definition

The product is a **Parking Intelligence Engine**, not a single AI agent that searches the web and gives an answer.

The system must separate four different problems:

1. **GIS / spatial reasoning** — where candidate curb segments exist.
2. **Parking regulation evaluation** — whether parking is legal and whether payment is required at a particular time.
3. **Parking availability prediction** — the probability that the driver will find at least one legal space on a candidate segment.
4. **Search-route optimization** — which segment should be checked first, and where the driver should go next if it is full.

The key design rule is:

> **Legality/free status and parking availability are different quantities.**
>
> Legality should be determined primarily by structured rules and authoritative evidence. Statistical/ML models should estimate the probability of finding an available legal space.

The final product should answer:

> “Where can I legally park for free near my destination right now, how likely am I to find a spot there, and what sequence of streets should I search to minimize expected time-to-park?”

---

# 2. Example user query

```text
Destination: SMU Fondren Library
Origin: current GPS location
Arrival time: now / specified time
Vehicle: normal passenger car
Preference: free parking only
Maximum walking time: 8 minutes
```

Example system output:

```text
Search Route A

1. Segment A — University Blvd
   Legal now: YES
   Free now: YES
   Legal confidence: 0.97
   P(find ≥1 spot): 0.76
   Drive ETA: 3.2 min
   Walk to destination: 4.1 min

2. Segment B — Daniel Ave
   Legal now: YES
   Free now: YES
   Legal confidence: 0.92
   P(find ≥1 spot): 0.61
   Additional driving if A is full: 1.3 min
   Walk to destination: 5.0 min
```

The route is contingent:

```text
A → if full → B → if full → C → fallback
```

Map states:

```text
🟢 legal + free + high predicted availability
🟡 legal + free + lower predicted availability
🔴 illegal / unavailable to this user
⚪ regulation uncertain — verify signage
```

---

# 3. Non-negotiable architecture principles

1. `LEGAL / ILLEGAL / UNKNOWN` is produced by the regulation engine, not by the availability model.
2. `FREE / PAID / UNKNOWN` is stored separately from legality.
3. Absence of a known prohibition is **not** sufficient evidence of legality.
4. Unresolved or materially conflicting regulation evidence must produce `UNKNOWN`.
5. Official law, official GIS/curb records, and verified current signs outrank community reports and visual inference.
6. Every legal/free conclusion must be traceable to evidence and timestamps.
7. AI components must emit schema-validated structured data.
8. Downstream deterministic code must not depend on parsing natural-language AI prose.
9. Geometry, time arithmetic, rule execution, database logic, routing costs, and optimization should be deterministic code.
10. Routing providers provide travel-time/distance data; the Parking Intelligence System owns the parking-search sequence.
11. The availability model predicts a precisely defined event.
12. Provider-specific data structures must remain behind adapters.
13. Core domain models must remain provider-independent.
14. Model/rule/optimizer outputs must be versioned and reproducible where practical.

---

# 4. Top-level architecture

```text
USER QUERY
origin + destination + arrival time + preferences
        │
        ▼
Destination Resolver
        │
        ▼
Destination Access Points
        │
        ▼
Search Area Builder
        │
        ▼
Candidate Curb Generator
OSM / GIS / curb geometry
        │
        ├────────────────┬──────────────────┐
        ▼                ▼                  ▼
 Official data        OSM / campus       AI intelligence
 adapters             information        services
                                           ├─ regulation extraction
                                           ├─ community evidence
                                           └─ vision/sign parsing
        └────────────────┴──────────────────┘
                         ▼
                   Evidence Store
                         ▼
                  Regulation Engine
                         ▼
           LEGAL/FREE candidates + UNKNOWN
                         ▼
                    Context Engine
                         ▼
                Availability Predictor
                         ▼
                    Route Matrix
                         ▼
            Stochastic Search Optimizer
                         ▼
          Explanation / Verification Layer
                         ▼
                       MAP / API
```

---

# 5. Orchestration model

Do not create a system where autonomous agents freely chat with one another and mutate shared state.

Use a central orchestrator with explicit inputs and outputs.

```text
                 ParkingQueryOrchestrator
                   /        |        \
                  /         |         \
       Regulation AI   Community AI   Vision AI
                  \         |         /
                   \        |        /
                    Evidence Store
                         │
                         ▼
                Deterministic Core
```

Agents may read required fields, append evidence, and return normalized claims. Agents must not overwrite deterministic legality results, rewrite other agents' results, decide the final route, or silently alter domain schemas.

---

# 6. Canonical domain model

## 6.1 ParkingSegment

`ParkingSegment` is the central persistent spatial unit.

```python
ParkingSegment {
    segment_id: str
    geometry: LineString
    street_name: str | None
    side: LEFT | RIGHT | UNKNOWN
    length_m: float
    estimated_capacity: float | None
    road_type: str | None
    physical_state: PARKABLE | NOT_PARKABLE | UNKNOWN
    regulations: list[RuleRef]
    legal_state: LEGAL | ILLEGAL | UNKNOWN
    free_state: FREE | PAID | UNKNOWN
    legal_confidence: float
    availability_probability: float | None
    availability_interval: tuple[float, float] | None
    evidence_refs: list[EvidenceRef]
    data_freshness: datetime
}
```

Stable IDs are required. Do not generate permanent IDs from row numbers or transient list positions.

## 6.2 ParkingRule DSL

```python
ParkingRule {
    rule_id: str
    segment_id: str
    rule_type:
        NO_PARKING | TIME_LIMIT | PAID | PERMIT_ONLY |
        LOADING | STREET_CLEANING | EVENT_RESTRICTION | OTHER
    days: list[DayOfWeek]
    start_time: time | None
    end_time: time | None
    effective_start_date: date | None
    effective_end_date: date | None
    max_duration_min: int | None
    payment_required: bool | None
    permit_required: bool | None
    permit_type: str | None
    exceptions: list[object]
    source_evidence_id: str
    extraction_confidence: float
}
```

The rule engine executes this representation. AI extraction does not.

## 6.3 Evidence

```python
Evidence {
    evidence_id: str
    source_type:
        OFFICIAL_CODE | OFFICIAL_GIS | VERIFIED_SIGN | UNIVERSITY |
        OSM | COMMUNITY | WEB | IMAGERY_INFERENCE
    source_uri_or_identifier: str
    publisher: str | None
    published_at: datetime | None
    observed_at: datetime | None
    retrieved_at: datetime
    raw_storage_policy: PERSIST | EPHEMERAL | REFERENCE_ONLY
    segment_ids: list[str]
    normalized_claims: list[object]
    reliability_tier: A | B | C | D
    extractor_version: str | None
    content_hash: str | None
}
```

Recommended evidence precedence:

| Tier | Source | Role |
|---|---|---|
| A | official ordinance, official GIS/curb data, verified current signage | authoritative legal evaluation |
| B | university/parking authority, well-maintained OSM | strong structured support |
| C | Reddit, forums, reviews, blogs | weak contextual evidence |
| D | imagery inference / AI guess | candidate generation and gap filling only |

Lower tiers must not override a conflicting higher-tier restriction.

---

# 7. Stable core interfaces

## Interface A — candidate generation

```python
get_candidate_segments(
    destination,
    search_constraints
) -> list[ParkingSegment]
```

## Interface B — legal/free evaluation

```python
evaluate_legality(
    segment,
    user_profile,
    datetime
) -> LegalityEvaluation
```

Example result:

```json
{
  "legal_state": "LEGAL",
  "free_state": "FREE",
  "max_duration_min": null,
  "confidence": 0.97,
  "evidence_refs": ["E123"],
  "reason_codes": ["NO_ACTIVE_RESTRICTION", "NO_PAYMENT_REQUIRED"]
}
```

## Interface C — availability prediction

```python
predict_availability(
    segment,
    context
) -> AvailabilityPrediction
```

## Interface D — contingent search routing

```python
plan_search_route(
    candidates,
    origin,
    destination,
    route_matrix
) -> SearchRoute
```

These interfaces should remain stable while internal implementations improve.

---

# 8. Destination Resolver

Normalize user-entered names/addresses into a canonical destination with access points.

```json
{
  "destination_id": "...",
  "name": "Fondren Library",
  "lat": 0.0,
  "lon": 0.0,
  "destination_type": "library",
  "access_points": []
}
```

Large destinations must not rely only on a centroid because parking cost depends on walking to usable entrances.

### V0

- one canonical SMU destination fixture;
- manually configured access points.

### V1

- provider-independent geocoding/POI adapter;
- multiple access points;
- entrance ranking.

---

# 9. Search Area Builder

Constrain the initial search space by walking tolerance and valid destination access points.

Initial MVP geography:

```text
SMU campus + approximately 1–1.5 km surrounding area
```

---

# 10. Candidate Curb Generator

The first spatial task is not to detect an individual parking spot. It is to identify curb segments that may plausibly support street parking.

Candidate inputs may include residential roads, tertiary roads, appropriate service roads, explicit OSM street-parking metadata, curb geometry, and parking-lane tags.

Split roads at intersections, known parking-rule changes, driveways where relevant, regulatory boundaries, and physical curb discontinuities. Represent left/right sides separately where regulations differ.

Recommended tools:

- OSMnx
- GeoPandas
- Shapely
- PostgreSQL + PostGIS

V0 target: manually inspect and persist approximately 100–300 segments around SMU.

---

# 11. Regulation Intelligence

This subsystem discovers parking restrictions from official city sources, municipal code, official GIS/curb datasets, university rules, signs, OSM, and public documents.

Its responsibility is evidence extraction, not final legal judgment.

Example structured extraction:

```json
{
  "segment_id": "DAL10291",
  "rules": [
    {
      "type": "NO_PARKING",
      "days": ["MON", "TUE", "WED", "THU", "FRI"],
      "start": "08:00",
      "end": "18:00"
    }
  ],
  "source": {"type": "OFFICIAL_CODE", "identifier": "..."},
  "confidence": 0.87
}
```

---

# 12. Regulation Engine

The regulation engine is deterministic.

Input:

```text
segment
local datetime
requested parking duration
vehicle profile
permit profile
normalized rules
evidence metadata
```

Output:

```text
LEGAL | ILLEGAL | UNKNOWN
FREE  | PAID    | UNKNOWN
duration limit
confidence
reason codes
evidence refs
```

Confidence is evidence confidence, not a probability produced by the availability model.

`UNKNOWN` is a first-class state.

---

# 13. Community Intelligence Agent

Community sources may include Reddit, local forums, parking discussions, blogs, and reviews.

The agent should extract structured claims rather than return summaries.

Example:

```json
{
  "location": "candidate street or area",
  "evidence_type": "community_claim",
  "claims": [
    {"type": "FREE_AFTER", "time": "18:00"},
    {"type": "LOW_AVAILABILITY", "condition": "football_event"}
  ],
  "published_date": "...",
  "source_reliability": 0.35
}
```

Community evidence can help candidate discovery and availability modeling but must not override authoritative regulation.

---

# 14. Vision Agent

Vision is a later-stage enhancement.

Primary tasks:

1. Parking sign parsing.
2. Physical curb feasibility.
3. Capacity estimation.

Vision is not required for V0.

---

# 15. Context Engine

Every search request should create a timestamped context snapshot.

```json
{
  "datetime": "2026-09-18T22:15:00-05:00",
  "weekday": "Friday",
  "hour": 22,
  "weather": "clear",
  "rain": false,
  "nearby_event": true,
  "event_type": "football",
  "event_start_delta_min": -35,
  "school_in_session": true
}
```

These variables primarily affect availability, not legal status.

---

# 16. Availability model

The response event must be precisely defined.

Initial definition:

> `Y(i,t) = 1` if the user reaches segment `i` at time `t` and finds at least one legal parking space within a configured search window, initially 90 seconds; otherwise `0`.

Model:

```text
P(Y(i,t) = 1 | X(i,t))
```

Potential features:

```text
segment identity
neighborhood
hour
weekday
weekend
capacity
distance to destination
land use
event state
weather
historical occupancy
recent observations
community demand signals
```

---

# 17. Availability-model progression

```text
V0: heuristic prior
V1: logistic regression / GAM
V2: XGBoost / LightGBM + calibration
V3: hierarchical spatiotemporal model
V4: graph / spatial-temporal model
```

Primary metrics:

- Brier score
- log loss
- calibration curve/error
- AUC as a secondary metric
- top-k parking success rate
- expected time-to-park under deployed policy

Calibration matters because the optimizer consumes probabilities.

---

# 18. Sparse-data strategy

Do not use raw empirical success rates without shrinkage.

Possible baseline:

```text
p_i ~ Beta(alpha, beta)
Y_i ~ Binomial(n_i, p_i)
```

Later:

```text
logit(p_it)
  = alpha_i
  + f(hour)
  + weekday
  + event
  + weather
  + ...

alpha_i ~ Normal(mu_neighborhood, sigma²)
```

This allows sparse/new segments to borrow strength from nearby segments.

---

# 19. Training data acquisition

Collect real parking outcomes:

```text
Did you find parking here?
YES / NO
```

Preferred fields:

```text
segment_id
timestamp
success
search duration
estimated occupancy
weather
event context
route position
```

---

# 20. Route optimization

Single-segment ranking is insufficient because parking search is sequential.

```text
try A
if A is full → B
if B is full → C
stop after successful parking
```

A simple score may be used for pruning:

```text
Score_i =
availability_probability
- λ1 * walking_distance
- λ2 * driving_time
- λ3 * uncertainty_or_risk
```

---

# 21. Expected-cost objective

For route `π = (i1, i2, ..., im)`:

```text
P(K = k) = p_ik × Π[j < k](1 - p_ij)
```

Define:

```text
ExpectedCost(π)
  = Σ_k [P(first success at k) × C_k]
    + P(all fail) × C_fallback
```

`C_k` includes cumulative driving time, expected local search time, walking time, and optional uncertainty/user-preference penalties.

The system should report:

```text
expected_time_to_park
probability_of_success_before_fallback
```

---

# 22. Optimizer progression

### V0
Transparent greedy/utility ranking.

### V1
Beam search over the top candidate set.

### V2+
Potential stochastic shortest-path formulation.

### V3+
Potential MDP after enough real-time observations exist.

---

# 23. Routing provider responsibility

Routing providers may calculate travel time/distance between origin, candidates, and destination entrances.

Possible providers:

- OSRM
- Valhalla
- GraphHopper
- Google Routes
- Mapbox

The internal optimizer decides parking order.

---

# 24. Provider adapter pattern

Use adapters such as:

```text
OSMAdapter
DallasCityAdapter
UniversityAdapter
RedditAdapter
WebSearchAdapter
GooglePlacesAdapter
StreetViewAdapter
WeatherAdapter
EventsAdapter
RoutingAdapter
```

Each adapter should define retrieval, caching, retention, attribution, persistent-storage policy, freshness, timeout, and failure behavior.

---

# 25. Database architecture

Use PostgreSQL + PostGIS as the source of truth.

Core tables:

```text
destinations
destination_access_points
street_segments
parking_rules
parking_sources
observations
occupancy_predictions
events
search_sessions
search_route_steps
parking_outcomes
```

Redis may be used later for cache, not as the authoritative store.

---

# 26. Main search API

Recommended endpoint:

```text
POST /v1/parking/search
```

Example request:

```json
{
  "origin": {"lat": 0.0, "lon": 0.0},
  "destination": {"query": "Fondren Library"},
  "arrival_time": "now",
  "free_only": true,
  "max_walk_minutes": 8,
  "vehicle_profile": {"type": "passenger"},
  "max_candidates": 20
}
```

Example response:

```json
{
  "destination": {},
  "route": {
    "steps": [
      {
        "segment_id": "...",
        "legal_state": "LEGAL",
        "free_state": "FREE",
        "legal_confidence": 0.97,
        "p_success": 0.76,
        "drive_eta_min": 3.2,
        "walk_min": 4.1,
        "evidence_refs": []
      }
    ],
    "expected_time_to_park_min": 8.4,
    "success_probability": 0.91,
    "fallback": {}
  },
  "unknown_segments": [],
  "versions": {
    "rule_engine": "v0",
    "availability_model": "v0",
    "optimizer": "v0"
  }
}
```

---

# 27. Reliability and degraded operation

The orchestrator should use async I/O for independent provider calls, enforce timeouts, validate AI outputs, record failures, and degrade gracefully.

Examples:

- community source failure should not stop official-rule evaluation;
- availability-model failure may fall back to a heuristic prior;
- insufficient regulation evidence must return `UNKNOWN`.

---

# 28. Recommended backend stack

```text
Python
FastAPI
Pydantic
SQLAlchemy
Alembic
PostgreSQL + PostGIS
GeoPandas
Shapely
OSMnx
pytest
Docker
Docker Compose
```

ML later:

```text
scikit-learn
XGBoost / LightGBM
PyMC
```

Frontend later:

```text
React / Next.js
MapLibre or Leaflet
```

---

# 29. Recommended repository structure

```text
parking-intelligence/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── docs/
│   ├── IMPLEMENTATION_SPEC.md
│   ├── assumptions.md
│   └── architecture_decisions/
├── src/
│   └── parking_ai/
│       ├── api/
│       ├── orchestrator/
│       ├── geocoding/
│       ├── gis/
│       ├── regulations/
│       ├── agents/
│       ├── availability/
│       ├── routing/
│       ├── evidence/
│       └── database/
├── alembic/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── frontend/
```

---

# 30. MVP roadmap

## V0 — prove the decision loop

```text
ParkingSegment
      ↓
legal/free filter
      ↓
availability prior
      ↓
expected-cost route
      ↓
API / map
```

Tasks:

1. Bootstrap repository.
2. Add FastAPI.
3. Add PostgreSQL/PostGIS.
4. Define domain schemas.
5. Define database schema.
6. Create SMU destination fixture.
7. Generate or manually curate 100–300 curb segments.
8. Manually encode enough parking rules for a trustworthy test area.
9. Build deterministic rule engine.
10. Build heuristic availability prior.
11. Build provider-independent route-matrix interface.
12. Build transparent greedy or beam-search optimizer.
13. Expose `POST /v1/parking/search`.
14. Record search sessions.
15. Record user outcome feedback.
16. Build minimal map UI only after backend works.

**V0 exit criterion:** The system can return a reproducible free/legal contingent route around SMU without requiring an LLM at request time.

## V1 — automate evidence acquisition

Add official-source adapters, regulation extraction, human-review queue, community evidence, weather/events context, logistic/GAM availability, calibration reporting, beam search, and provenance UI.

## V2 — vision and learned occupancy

Add sign vision, user-submitted sign verification, physical curb feasibility, capacity estimation, stronger availability models, monitoring, and active learning.

## V3 — dynamic intelligence

Add crowdsourced observations, dynamic rerouting, neighborhood-demand models, conditional updates, and advanced sequential decision methods.

---

# 31. Phase-based coding execution

## Phase 0 — repository and infrastructure

Deliver:

- Python project
- FastAPI health endpoint
- Docker Compose
- PostgreSQL + PostGIS
- SQLAlchemy
- Alembic
- pytest
- README

Gate:

```text
application starts
database starts
migrations run
tests pass
```

## Phase 1 — domain layer

Deliver typed schemas and persistence for:

```text
Destination
DestinationAccessPoint
ParkingSegment
ParkingRule
Evidence
LegalityEvaluation
AvailabilityPrediction
SearchRoute
SearchRouteStep
SearchSession
ParkingOutcome
```

No external provider and no LLM yet.

## Phase 2 — SMU candidate-segment GIS slice

Deliver SMU fixture, access points, OSM retrieval or fixtures, segment generator, stable IDs, PostGIS persistence, and geometry tests.

## Phase 3 — regulation engine

Deliver:

```text
LEGAL / ILLEGAL / UNKNOWN
FREE / PAID / UNKNOWN
```

## Phase 4 — availability baseline

Deliver V0 heuristic, model-version contract, feature snapshot, uncertainty output, and evaluation harness.

## Phase 5 — route matrix and optimizer

Deliver provider-independent matrix, synthetic matrix for tests, optional OSRM implementation, expected-cost calculation, greedy baseline, beam search.

## Phase 6 — end-to-end search API

```text
destination
→ candidates
→ legality
→ availability
→ route matrix
→ optimizer
→ response
```

## Phase 7 — minimal map UI

Show candidate segments, states, predicted availability, route sequence, provenance.

## Phase 8 — AI evidence services

Add regulation extraction, community evidence, and vision only after deterministic core is stable.

---

# 32. Coding-agent guardrails

1. Inspect existing code and tests before editing.
2. Do not implement future phases unless explicitly asked.
3. Preserve the four core interfaces.
4. Do not silently alter public APIs.
5. Keep provider-specific code behind adapters.
6. Keep LLM code out of deterministic core.
7. Never use an LLM response as the sole authoritative legal basis.
8. Add tests with every functional change.
9. Run tests before declaring completion.
10. Add migrations for schema changes.
11. Record architecture assumptions in `docs/assumptions.md`.
12. Use ADRs for meaningful architecture decisions.
13. Prefer small composable services/functions over god classes.
14. Keep model training separate from online inference.
15. Version availability models, rule engine, and optimizer behavior.
16. Validate time zones explicitly.
17. Avoid hidden global mutable state.
18. Do not store third-party content without an explicit storage policy.
19. Handle external failures/timeouts.
20. Report changed files, tests, assumptions, and unresolved decisions after each phase.

---

# 33. Testing requirements

## Rule engine

Test:

- weekday/weekend boundaries;
- exact start/end times;
- overlapping rules;
- permits;
- paid periods;
- expired/stale evidence;
- contradictory evidence;
- unknown coverage;
- duration crossing a restriction boundary;
- daylight-saving transitions.

## Database

Test geometry, enums, relationships, timezone-aware timestamps, search sessions, and outcomes.

## Availability

Test valid probability range, uncertainty interval, deterministic V0 behavior, and model version.

## Optimizer

Test synthetic analytically obvious cases, all-fail, one candidate, zero candidates, distance/probability tradeoffs, and fallback cost.

## Integration

At minimum:

```text
SMU fixture
→ candidate segments
→ legal/free evaluation
→ availability prediction
→ route optimization
→ API response
```

---

# 34. Product-level evaluation

Primary product metric:

```text
time-to-park
```

Also track walking time, failure rate, fallback rate, legality-error reports, top-k success, and probability calibration.

Compare against simple baselines such as nearest candidate first, highest-probability first, and unguided/manual search.

---

# 35. Uncertainty and user-facing safety

Parking regulation data may be incomplete or stale.

Never equate:

```text
no prohibition found
```

with:

```text
definitely legal
```

User-facing explanations should include source, last verification/freshness, confidence, and a prompt to verify signage when state is uncertain.

The product must not present model output as a legal guarantee.

---

# 36. Source compliance

Each adapter should encode a storage policy:

```text
PERSIST
EPHEMERAL
REFERENCE_ONLY
```

Prefer long-term training/storage from open data, official city datasets with appropriate rights, OSM under applicable terms, internally collected observations, and user feedback with appropriate consent.

---

# 37. Definition of done for the first usable MVP

The MVP is complete when:

- user can submit SMU-area origin, destination, time, free-only preference, and max walk;
- system generates curb candidates;
- deterministic rule engine evaluates legal/free status;
- `UNKNOWN` remains visible;
- availability layer assigns versioned probabilities;
- optimizer returns an ordered contingent route;
- route output includes expected time-to-park;
- recommendations expose provenance;
- search sessions/outcomes are persisted;
- core tests pass;
- request runs end-to-end without manual code changes.

---

# 38. Long-term moat

The primary defensible assets are:

1. structured curb-level regulation database;
2. historical occupancy data;
3. user parking outcomes;
4. calibrated local availability models;
5. search-route decision models;
6. evidence freshness/provenance;
7. city-by-city adapters and validation infrastructure.

---

# 39. Engineering priority

```text
correctness
>
clear contracts
>
testability
>
observability
>
modularity
>
performance
>
feature breadth
```

Do not add AI simply because a task can be phrased in natural language.

---

# 40. Project motto

> **AI reads the world. GIS describes space. Rules determine legality. Statistics estimates availability. Optimization decides where to search next.**
