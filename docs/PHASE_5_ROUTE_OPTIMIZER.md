# Phase 5 route matrix and search optimizer

## Scope

Phase 5 implements a provider-independent directed travel-time matrix, an explicit synthetic
matrix builder, the deterministic expected-time objective, a transparent greedy baseline, and a
bounded beam-search optimizer. The stable public method remains:

```python
plan_search_route(candidates, origin, destination, route_matrix) -> SearchRoute
```

The planner performs no network or database I/O and does not mutate its inputs. This phase does
not implement OSRM or another live provider, end-to-end API orchestration, search-session writes,
live rerouting, UI, training, or AI behavior.

## Route-matrix contract

`RouteMatrix.travel_time_seconds` is a directed, sparse graph of finite nonnegative driving
seconds. It does not assume symmetry or the triangle inequality. Missing edges mean unreachable;
they never mean zero and `NaN`/infinity are rejected.

Phase 5 reserves two node IDs:

- `origin` — the request origin;
- `fallback` — a modeled destination that guarantees parking.

All other planning nodes use stable `ParkingSegment.segment_id` values. The matrix must contain an
`origin -> fallback` edge. Every completed route must end at a node with an edge to `fallback`.
Beam search may traverse an intermediate candidate without a direct fallback edge when a later
reachable candidate restores the fallback path; greedy considers only immediately completable
extensions. Matrix construction, not the optimizer, owns driving costs.

Each matrix is bound to the exact request origin, canonical destination and access points, sorted
candidate-ID set, routing profile, and guaranteed-parking fallback identity/location. Its
content-derived matrix ID covers that binding, the directed costs, and the provider version. The
provider version identifies adapter/data/profile behavior; the matrix ID identifies the exact
cost snapshot consumed by one optimization.

`build_synthetic_route_matrix` creates request-bound canonical matrices from explicit directed edge
costs, adds zero diagonals, validates node IDs, and retains missing edges as unreachable. It exists
for local, analytically verifiable tests and makes no provider calls. A new
`RouteMatrixProvider` Protocol defines the future adapter boundary without selecting parking order.

## Request-scoped context

The original four-argument planning method does not contain the session ID or rule/model versions
required by `SearchRoute`. `DeterministicSearchRoutePlanner` therefore receives an immutable
`RoutePlanningContext` at construction with:

- session ID;
- regulation-engine version;
- availability-model version;
- availability target window;
- whether free parking is required.
- an immutable per-segment decision snapshot containing the regulation-evaluation ID and state,
  evidence references, rule-engine version, availability-prediction ID and probability, model
  version, and target window.

This preserves the stable public method and avoids hidden mutable globals or invented provenance.
The local search time must match the injected availability target window. Phase 5 defaults both to
90 seconds. Candidate values are checked against their decision snapshots before planning, so a
route cannot silently mix a different legal/free evaluation, probability, rule version, model
version, or target window.

## Eligibility and ownership

The optimizer never decides parking legality, payment status, or availability. It fails closed
unless every supplied candidate has:

- unique, non-reserved stable ID;
- `LEGAL` state;
- `FREE` state when the context requires free parking, otherwise a known `FREE` or `PAID` state;
- a validated availability probability.

Upstream stages remain responsible for regulation evaluation, availability prediction, and the
maximum-walk candidate filter. Zero-probability segments are not used as routing waypoints.

## Walking-time approximation

V0 converts deterministic straight-line distance from the candidate LineString to the nearest
configured destination access point at 80 metres per minute, using the same local projection as
Phase 2 candidate filtering. If no access point exists, it uses the destination centroid. This is
an explicit offline approximation; a future provider may supply pedestrian-network costs behind a
versioned contract.

## Expected-time objective

For route `π = (i1, ..., im)`, let:

```text
q0 = 1
qk = q(k-1) * (1 - p_ik)
P(first success at k) = q(k-1) * p_ik
```

Phase 5 assumes independent candidate outcomes. The availability probability describes success
within the common 90-second target window. Because no conditional time-to-success distribution
exists, both a successful and failed attempt conservatively consume the full window.

For step `k`:

```text
T_success(k)
  = cumulative driving through k
  + k * local_search_seconds
  + walk_seconds(k)
```

If all candidates fail:

```text
T_fallback
  = all driving legs
  + m * local_search_seconds
  + drive(last_or_origin, fallback)
  + fallback_service_seconds
```

The default fallback service time is five minutes and represents entry, parking, and other
non-driving terminal work at the guaranteed-parking fallback.

```text
ExpectedTime(π)
  = Σ P(first success at k) * T_success(k)
  + q_m * T_fallback

SuccessProbability(π) = 1 - q_m
```

All optimization comparisons use `Decimal` values created from validated inputs and do not round
before selection. Outputs convert seconds to minutes only at the boundary.

## Greedy and beam strategies

Greedy V0 compares STOP with every reachable unused one-candidate extension using the complete
expected-time objective. It appends the best extension only when the deterministic route key
prefers it, then repeats.

Beam V1 expands non-repeating reachable paths up to the configured candidate and step limits. It
retains the configured number of partial routes by a deterministic lower-bound key and chooses the
best fallback-completable prefix observed at any depth. The default width is eight.

Both strategies use the same tie order:

1. lower expected time;
2. higher success probability before fallback;
3. fewer steps;
4. lexicographically smaller ordered stable segment IDs.

This makes candidate input order, mapping insertion order, and Python hash randomization
irrelevant. `SearchRouteStep.drive_eta_min` is the incremental leg time from origin or the previous
candidate. The standalone expected-cost evaluator exposes cumulative driving and first-success
components for verification. Step order is zero-based.

## Reproducibility and persistence

The planner returns content-derived route and step IDs. The route ID includes the session,
optimizer/cost versions, configuration, normalized origin/destination, immutable candidate decision
snapshots, exact matrix ID/provider/content, walking times, selected order, and output metrics. No
wall-clock or input list position participates.

Outputs include the optimizer configuration snapshot, exact matrix ID and provider version,
availability, regulation, and cost-model versions, selected decision snapshots, pre-fallback
success/failure probability, the all-fail fallback time, and the fallback expected-cost
contribution. First-success probabilities and component costs are available from the standalone
expected-cost evaluator used by tests and diagnostics.

Phase 5 adds output-only optional route diagnostics and immutable snapshot schemas. Existing
persisted route and route-step columns remain compatible, and matrix/route computation is
read-only, so no Alembic migration is required. Phase 6 must explicitly persist or log the matrix
binding and ID, optimizer configuration, decision snapshots, and route summary before claiming
full search-session replayability.

## Verification commands

```text
.venv/bin/pytest -q tests/unit/test_route_matrix.py
.venv/bin/pytest -q tests/unit/test_route_optimizer.py
.venv/bin/pytest -m "not integration"
TEST_DATABASE_URL=<dedicated-postgis-url> .venv/bin/pytest -m integration
TEST_DATABASE_URL=<dedicated-postgis-url> .venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
```

The synthetic tests cover hand-calculated expected cost, zero/one candidates, STOP, all-zero and
guaranteed-success cases, product-of-failures probability, directed/asymmetric costs, greedy versus
beam behavior, exhaustive small-fixture comparison, exact ties, input-order independence,
free/legal preconditions, sparse unreachable edges, nearest access-point walking, version/ID
sensitivity, request-binding rejection, intermediate sparse beam paths, decision-snapshot
consistency, schema compatibility, and non-mutation.
