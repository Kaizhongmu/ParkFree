from datetime import UTC, datetime
from itertools import permutations

import pytest

from parking_ai.domain import (
    Destination,
    DestinationAccessPoint,
    FreeState,
    GeoPoint,
    LegalState,
    LineStringGeometry,
    ParkingSegment,
    RouteCandidateSnapshot,
    RouteOptimizationStrategy,
)
from parking_ai.routing import (
    FALLBACK_NODE_ID,
    ORIGIN_NODE_ID,
    DeterministicSearchRoutePlanner,
    IneligibleCandidateError,
    RouteOptimizerConfig,
    RoutePlanningContext,
    RoutePlanningError,
    UnreachableRouteError,
    build_synthetic_route_matrix,
    calculate_expected_route_cost,
)

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)
ORIGIN = GeoPoint(latitude=32.84, longitude=-96.79)


def segment(
    segment_id: str,
    probability: float | None,
    *,
    legal_state: LegalState = LegalState.LEGAL,
    free_state: FreeState = FreeState.FREE,
    coordinates: list[tuple[float, float]] | None = None,
) -> ParkingSegment:
    return ParkingSegment(
        segment_id=segment_id,
        geometry=LineStringGeometry(
            coordinates=coordinates or [(-96.784, 32.842), (-96.783, 32.843)]
        ),
        street_name=f"Street {segment_id}",
        length_m=100.0,
        legal_state=legal_state,
        free_state=free_state,
        legal_confidence=0.9,
        availability_probability=probability,
        evidence_refs=[f"evidence-{segment_id}"],
        data_freshness=NOW,
    )


def destination(*, with_access_points: bool = False) -> Destination:
    access_points = []
    if with_access_points:
        access_points = [
            DestinationAccessPoint(
                access_point_id="far",
                destination_id="destination-1",
                name="Far",
                location=GeoPoint(latitude=33.0, longitude=-97.0),
            ),
            DestinationAccessPoint(
                access_point_id="on-segment",
                destination_id="destination-1",
                name="On segment",
                location=GeoPoint(latitude=32.842, longitude=-96.784),
            ),
        ]
    return Destination(
        destination_id="destination-1",
        name="Test destination",
        location=GeoPoint(latitude=32.842, longitude=-96.784),
        access_points=access_points,
    )


def context(
    candidates: list[ParkingSegment],
    *,
    session_id: str = "session-1",
    require_free: bool = True,
) -> RoutePlanningContext:
    return RoutePlanningContext(
        session_id=session_id,
        rule_engine_version="rules-v1",
        availability_model_version="availability-v1",
        require_free=require_free,
        candidate_snapshots=tuple(
            RouteCandidateSnapshot(
                segment_id=candidate.segment_id,
                legality_evaluation_id=f"legality-{candidate.segment_id}",
                legal_state=candidate.legal_state,
                free_state=candidate.free_state,
                legal_confidence=candidate.legal_confidence,
                evidence_refs=[f"rule-evidence-{candidate.segment_id}"],
                rule_engine_version="rules-v1",
                availability_prediction_id=f"prediction-{candidate.segment_id}",
                availability_probability=(
                    candidate.availability_probability
                    if candidate.availability_probability is not None
                    else 0.5
                ),
                availability_model_version="availability-v1",
                availability_target_window_seconds=90,
            )
            for candidate in candidates
        ),
    )


def config(
    *,
    strategy: RouteOptimizationStrategy = RouteOptimizationStrategy.GREEDY,
    beam_width: int = 8,
    max_route_steps: int | None = None,
    fallback_service_seconds: float = 0.0,
    optimizer_version: str | None = None,
) -> RouteOptimizerConfig:
    return RouteOptimizerConfig(
        strategy=strategy,
        beam_width=beam_width,
        max_route_steps=max_route_steps,
        fallback_service_seconds=fallback_service_seconds,
        optimizer_version=optimizer_version,
    )


def matrix(
    segment_ids: list[str],
    edges: dict[tuple[str, str], float],
    *,
    provider_version: str = "synthetic-test-v1",
    bound_destination: Destination | None = None,
):
    return build_synthetic_route_matrix(
        segment_ids,
        edges,
        origin=ORIGIN,
        destination=bound_destination or destination(),
        provider_version=provider_version,
    )


def test_expected_cost_matches_single_candidate_hand_calculation() -> None:
    candidate = segment("a", 0.5)
    route_matrix = matrix(
        ["a"],
        {
            (ORIGIN_NODE_ID, "a"): 60.0,
            ("a", FALLBACK_NODE_ID): 120.0,
        },
    )

    cost = calculate_expected_route_cost(
        [candidate],
        route_matrix,
        {"a": 30.0},
        local_search_seconds=90.0,
        fallback_service_seconds=300.0,
    )

    assert cost.first_success_probabilities == pytest.approx((0.5,))
    assert cost.success_time_seconds == pytest.approx((180.0,))
    assert cost.fallback_time_if_all_fail_seconds == pytest.approx(570.0)
    assert cost.fallback_expected_contribution_seconds == pytest.approx(285.0)
    assert cost.expected_time_seconds == pytest.approx(375.0)
    assert cost.success_probability == pytest.approx(0.5)
    assert cost.failure_probability == pytest.approx(0.5)


def test_planner_rejects_blank_candidate_id_before_matrix_use() -> None:
    candidate = segment("   ", 0.5)
    planner = DeterministicSearchRoutePlanner(context([candidate]), config=config())
    route_matrix = matrix(
        [],
        {(ORIGIN_NODE_ID, FALLBACK_NODE_ID): 30.0},
    )

    with pytest.raises(IneligibleCandidateError, match="nonblank and trimmed"):
        planner.plan_search_route([candidate], ORIGIN, destination(), route_matrix)


def test_empty_route_goes_directly_to_fallback() -> None:
    route_matrix = matrix(
        [],
        {(ORIGIN_NODE_ID, FALLBACK_NODE_ID): 120.0},
    )

    cost = calculate_expected_route_cost(
        [],
        route_matrix,
        {},
        fallback_service_seconds=300.0,
    )

    assert cost.expected_time_seconds == 420.0
    assert cost.fallback_time_if_all_fail_seconds == 420.0
    assert cost.success_probability == 0.0
    assert cost.failure_probability == 1.0


def test_greedy_one_candidate_output_is_fully_traceable() -> None:
    candidate = segment("a", 0.5)
    route_matrix = matrix(
        ["a"],
        {
            (ORIGIN_NODE_ID, "a"): 60.0,
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 600.0,
            ("a", FALLBACK_NODE_ID): 120.0,
        },
    )
    planner = DeterministicSearchRoutePlanner(context([candidate]), config=config())

    route = planner.plan_search_route([candidate], ORIGIN, destination(), route_matrix)

    assert [step.segment_id for step in route.steps] == ["a"]
    assert route.expected_time_to_park_min == pytest.approx(3.5)
    assert route.success_probability == pytest.approx(0.5)
    assert route.failure_probability == pytest.approx(0.5)
    assert route.fallback_time_min == pytest.approx(4.5)
    assert route.fallback_expected_contribution_min == pytest.approx(2.25)
    assert route.route_matrix_version == route_matrix.matrix_id
    assert route.route_matrix_provider_version == "synthetic-test-v1"
    assert route.cost_model_version == "expected-time-independent-v1"
    assert route.steps[0].drive_eta_min == pytest.approx(1.0)
    assert route.steps[0].walk_min == 0.0
    assert route.steps[0].session_id == route.session_id == "session-1"
    assert route.optimizer_snapshot is not None
    assert route.optimizer_snapshot.local_search_seconds == 90.0
    assert route.selected_candidate_snapshots[0].availability_prediction_id == "prediction-a"


def test_stop_at_origin_beats_low_value_candidate() -> None:
    candidate = segment("a", 0.1)
    route_matrix = matrix(
        ["a"],
        {
            (ORIGIN_NODE_ID, "a"): 60.0,
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 100.0,
            ("a", FALLBACK_NODE_ID): 120.0,
        },
    )

    route = DeterministicSearchRoutePlanner(
        context([candidate]), config=config()
    ).plan_search_route([candidate], ORIGIN, destination(), route_matrix)

    assert route.steps == []
    assert route.expected_time_to_park_min == pytest.approx(100.0 / 60.0)
    assert route.success_probability == 0.0


def test_all_zero_probability_candidates_are_not_routing_waypoints() -> None:
    candidates = [segment("a", 0.0), segment("b", 0.0)]
    route_matrix = matrix(
        ["a", "b"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 500.0,
            (ORIGIN_NODE_ID, "a"): 1.0,
            ("a", "b"): 1.0,
            ("a", FALLBACK_NODE_ID): 1.0,
            ("b", FALLBACK_NODE_ID): 1.0,
        },
    )

    route = DeterministicSearchRoutePlanner(context(candidates), config=config()).plan_search_route(
        candidates, ORIGIN, destination(), route_matrix
    )

    assert route.steps == []
    assert route.failure_probability == 1.0


def test_probability_one_stops_after_guaranteed_candidate() -> None:
    candidates = [segment("a", 1.0), segment("b", 0.9)]
    route_matrix = matrix(
        ["a", "b"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
            (ORIGIN_NODE_ID, "a"): 30.0,
            (ORIGIN_NODE_ID, "b"): 200.0,
            ("a", "b"): 1.0,
            ("a", FALLBACK_NODE_ID): 100.0,
            ("b", FALLBACK_NODE_ID): 100.0,
        },
    )

    route = DeterministicSearchRoutePlanner(context(candidates), config=config()).plan_search_route(
        candidates, ORIGIN, destination(), route_matrix
    )

    assert [step.segment_id for step in route.steps] == ["a"]
    assert route.success_probability == 1.0
    assert route.failure_probability == 0.0


def test_beam_finds_better_directed_sequence_than_greedy() -> None:
    candidates = [segment("a", 0.6), segment("b", 0.5)]
    route_matrix = matrix(
        ["a", "b"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
            (ORIGIN_NODE_ID, "a"): 60.0,
            (ORIGIN_NODE_ID, "b"): 120.0,
            ("a", "b"): 6000.0,
            ("b", "a"): 60.0,
            ("a", FALLBACK_NODE_ID): 1000.0,
            ("b", FALLBACK_NODE_ID): 1000.0,
        },
    )

    greedy = DeterministicSearchRoutePlanner(
        context(candidates), config=config()
    ).plan_search_route(candidates, ORIGIN, destination(), route_matrix)
    beam = DeterministicSearchRoutePlanner(
        context(candidates),
        config=config(strategy=RouteOptimizationStrategy.BEAM, beam_width=2),
    ).plan_search_route(candidates, ORIGIN, destination(), route_matrix)

    assert [step.segment_id for step in greedy.steps] == ["a"]
    assert [step.segment_id for step in beam.steps] == ["b", "a"]
    assert beam.expected_time_to_park_min < greedy.expected_time_to_park_min


def test_wide_beam_matches_exhaustive_small_fixture() -> None:
    candidates = [segment("a", 0.4), segment("b", 0.6), segment("c", 0.3)]
    edges = {
        (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 900.0,
        (ORIGIN_NODE_ID, "a"): 20.0,
        (ORIGIN_NODE_ID, "b"): 80.0,
        (ORIGIN_NODE_ID, "c"): 50.0,
        ("a", "b"): 40.0,
        ("a", "c"): 20.0,
        ("b", "a"): 70.0,
        ("b", "c"): 15.0,
        ("c", "a"): 25.0,
        ("c", "b"): 30.0,
        ("a", FALLBACK_NODE_ID): 600.0,
        ("b", FALLBACK_NODE_ID): 600.0,
        ("c", FALLBACK_NODE_ID): 600.0,
    }
    route_matrix = matrix(["a", "b", "c"], edges)
    walks = {candidate.segment_id: 0.0 for candidate in candidates}
    possible_routes = [()]
    for length in range(1, len(candidates) + 1):
        possible_routes.extend(permutations(candidates, length))
    expected = min(
        possible_routes,
        key=lambda item: (
            calculate_expected_route_cost(item, route_matrix, walks).expected_time_seconds,
            -calculate_expected_route_cost(item, route_matrix, walks).success_probability,
            len(item),
            tuple(candidate.segment_id for candidate in item),
        ),
    )

    route = DeterministicSearchRoutePlanner(
        context(candidates),
        config=config(strategy=RouteOptimizationStrategy.BEAM, beam_width=20),
    ).plan_search_route(candidates, ORIGIN, destination(), route_matrix)

    assert [step.segment_id for step in route.steps] == [
        candidate.segment_id for candidate in expected
    ]


def test_candidate_and_matrix_input_order_do_not_change_route_or_ids() -> None:
    candidates = [segment("a", 0.5), segment("b", 0.4)]
    edges = {
        (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
        (ORIGIN_NODE_ID, "a"): 30.0,
        (ORIGIN_NODE_ID, "b"): 40.0,
        ("a", "b"): 20.0,
        ("b", "a"): 20.0,
        ("a", FALLBACK_NODE_ID): 500.0,
        ("b", FALLBACK_NODE_ID): 500.0,
    }
    first_matrix = matrix(["a", "b"], edges)
    second_matrix = matrix(["b", "a"], dict(reversed(list(edges.items()))))
    planner = DeterministicSearchRoutePlanner(
        context(candidates), config=config(strategy=RouteOptimizationStrategy.BEAM)
    )

    first = planner.plan_search_route(candidates, ORIGIN, destination(), first_matrix)
    second = planner.plan_search_route(
        list(reversed(candidates)), ORIGIN, destination(), second_matrix
    )

    assert first == second


def test_exact_tie_uses_segment_id() -> None:
    candidates = [segment("b", 0.5), segment("a", 0.5)]
    route_matrix = matrix(
        ["a", "b"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
            (ORIGIN_NODE_ID, "a"): 10.0,
            (ORIGIN_NODE_ID, "b"): 10.0,
            ("a", FALLBACK_NODE_ID): 500.0,
            ("b", FALLBACK_NODE_ID): 500.0,
        },
    )

    route = DeterministicSearchRoutePlanner(
        context(candidates), config=config(max_route_steps=1)
    ).plan_search_route(candidates, ORIGIN, destination(), route_matrix)

    assert [step.segment_id for step in route.steps] == ["a"]


@pytest.mark.parametrize(
    "candidate",
    [
        segment("illegal", 0.5, legal_state=LegalState.ILLEGAL),
        segment("unknown-legal", 0.5, legal_state=LegalState.UNKNOWN),
        segment("paid", 0.5, free_state=FreeState.PAID),
        segment("unknown-free", 0.5, free_state=FreeState.UNKNOWN),
        segment("missing-probability", None),
    ],
)
def test_ineligible_candidates_fail_closed(candidate: ParkingSegment) -> None:
    route_matrix = matrix(
        [candidate.segment_id],
        {(ORIGIN_NODE_ID, FALLBACK_NODE_ID): 100.0},
    )

    with pytest.raises(IneligibleCandidateError):
        DeterministicSearchRoutePlanner(context([candidate]), config=config()).plan_search_route(
            [candidate], ORIGIN, destination(), route_matrix
        )


def test_paid_candidate_is_allowed_only_when_free_is_not_required() -> None:
    candidate = segment("paid", 0.9, free_state=FreeState.PAID)
    route_matrix = matrix(
        ["paid"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
            (ORIGIN_NODE_ID, "paid"): 10.0,
            ("paid", FALLBACK_NODE_ID): 100.0,
        },
    )

    route = DeterministicSearchRoutePlanner(
        context([candidate], require_free=False), config=config()
    ).plan_search_route([candidate], ORIGIN, destination(), route_matrix)

    assert [step.segment_id for step in route.steps] == ["paid"]


def test_duplicate_candidate_ids_are_rejected() -> None:
    candidate = segment("a", 0.5)
    route_matrix = matrix(["a"], {(ORIGIN_NODE_ID, FALLBACK_NODE_ID): 100.0})

    with pytest.raises(IneligibleCandidateError, match="duplicate"):
        DeterministicSearchRoutePlanner(context([candidate]), config=config()).plan_search_route(
            [candidate, candidate], ORIGIN, destination(), route_matrix
        )


def test_sparse_unreachable_candidate_is_skipped_but_origin_fallback_is_required() -> None:
    candidate = segment("a", 0.9)
    unreachable = matrix(
        ["a"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 100.0,
            ("a", FALLBACK_NODE_ID): 100.0,
        },
    )

    route = DeterministicSearchRoutePlanner(
        context([candidate]), config=config()
    ).plan_search_route([candidate], ORIGIN, destination(), unreachable)
    assert route.steps == []

    missing_fallback = matrix(["a"], {(ORIGIN_NODE_ID, "a"): 10.0, ("a", FALLBACK_NODE_ID): 10.0})
    with pytest.raises(UnreachableRouteError, match="fallback"):
        DeterministicSearchRoutePlanner(context([candidate]), config=config()).plan_search_route(
            [candidate], ORIGIN, destination(), missing_fallback
        )


def test_beam_can_use_intermediate_candidate_without_direct_fallback_edge() -> None:
    candidates = [segment("a", 0.9), segment("b", 0.9)]
    route_matrix = matrix(
        ["a", "b"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 10000.0,
            (ORIGIN_NODE_ID, "a"): 1.0,
            ("a", "b"): 1.0,
            ("b", FALLBACK_NODE_ID): 1.0,
        },
    )

    route = DeterministicSearchRoutePlanner(
        context(candidates),
        config=RouteOptimizerConfig(strategy=RouteOptimizationStrategy.BEAM, beam_width=2),
    ).plan_search_route(candidates, ORIGIN, destination(), route_matrix)

    assert [step.segment_id for step in route.steps] == ["a", "b"]
    assert route.expected_time_to_park_min == pytest.approx(103.11 / 60.0)


def test_nearest_destination_access_point_drives_walk_time() -> None:
    candidate = segment("a", 0.9)
    bound_destination = destination(with_access_points=True)
    route_matrix = matrix(
        ["a"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
            (ORIGIN_NODE_ID, "a"): 10.0,
            ("a", FALLBACK_NODE_ID): 100.0,
        },
        bound_destination=bound_destination,
    )

    route = DeterministicSearchRoutePlanner(
        context([candidate]), config=config()
    ).plan_search_route([candidate], ORIGIN, bound_destination, route_matrix)

    assert route.steps[0].walk_min == 0.0


def test_versions_and_session_change_content_id_without_changing_order() -> None:
    candidate = segment("a", 0.9)
    edges = {
        (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
        (ORIGIN_NODE_ID, "a"): 10.0,
        ("a", FALLBACK_NODE_ID): 100.0,
    }
    first = DeterministicSearchRoutePlanner(
        context([candidate], session_id="session-1"),
        config=config(optimizer_version="optimizer-v1"),
    ).plan_search_route([candidate], ORIGIN, destination(), matrix(["a"], edges))
    second = DeterministicSearchRoutePlanner(
        context([candidate], session_id="session-2"),
        config=config(optimizer_version="optimizer-v2"),
    ).plan_search_route(
        [candidate],
        ORIGIN,
        destination(),
        matrix(["a"], edges, provider_version="synthetic-test-v2"),
    )

    assert [step.segment_id for step in first.steps] == [step.segment_id for step in second.steps]
    assert first.expected_time_to_park_min == second.expected_time_to_park_min
    assert first.route_id != second.route_id
    assert first.steps[0].route_step_id != second.steps[0].route_step_id


def test_matrix_binding_and_content_id_are_enforced() -> None:
    candidate = segment("a", 0.9)
    edges = {
        (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
        (ORIGIN_NODE_ID, "a"): 10.0,
        ("a", FALLBACK_NODE_ID): 100.0,
    }
    route_matrix = matrix(["a"], edges)
    planner = DeterministicSearchRoutePlanner(context([candidate]), config=config())

    with pytest.raises(RoutePlanningError, match="origin binding"):
        planner.plan_search_route(
            [candidate],
            GeoPoint(latitude=32.85, longitude=-96.80),
            destination(),
            route_matrix,
        )
    with pytest.raises(RoutePlanningError, match="destination binding"):
        planner.plan_search_route(
            [candidate],
            ORIGIN,
            destination().model_copy(update={"destination_id": "other"}),
            route_matrix,
        )

    tampered_costs = route_matrix.model_copy(deep=True)
    tampered_costs.travel_time_seconds[ORIGIN_NODE_ID]["a"] = 999.0
    with pytest.raises(RoutePlanningError, match="content ID"):
        planner.plan_search_route([candidate], ORIGIN, destination(), tampered_costs)


def test_candidate_values_must_match_decision_snapshots() -> None:
    candidate = segment("a", 0.9)
    planning_context = context([candidate])
    changed = candidate.model_copy(update={"availability_probability": 0.2})
    route_matrix = matrix(
        ["a"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
            (ORIGIN_NODE_ID, "a"): 10.0,
            ("a", FALLBACK_NODE_ID): 100.0,
        },
    )

    with pytest.raises(IneligibleCandidateError, match="availability prediction"):
        DeterministicSearchRoutePlanner(planning_context, config=config()).plan_search_route(
            [changed], ORIGIN, destination(), route_matrix
        )


def test_planner_does_not_mutate_inputs() -> None:
    candidate = segment("a", 0.9)
    route_matrix = matrix(
        ["a"],
        {
            (ORIGIN_NODE_ID, FALLBACK_NODE_ID): 1000.0,
            (ORIGIN_NODE_ID, "a"): 10.0,
            ("a", FALLBACK_NODE_ID): 100.0,
        },
    )
    candidate_before = candidate.model_dump()
    matrix_before = route_matrix.model_dump()

    DeterministicSearchRoutePlanner(context([candidate]), config=config()).plan_search_route(
        [candidate], ORIGIN, destination(), route_matrix
    )

    assert candidate.model_dump() == candidate_before
    assert route_matrix.model_dump() == matrix_before


def test_local_search_window_must_match_availability_contract() -> None:
    with pytest.raises(ValueError, match="match"):
        DeterministicSearchRoutePlanner(
            context([]), config=RouteOptimizerConfig(local_search_seconds=45.0)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("beam_width", 1.5),
        ("beam_width", True),
        ("max_route_steps", 1.5),
        ("max_route_steps", True),
        ("candidate_limit", 1.5),
        ("candidate_limit", True),
    ],
)
def test_count_configuration_requires_real_integers(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        RouteOptimizerConfig(**{field: value})  # type: ignore[arg-type]
