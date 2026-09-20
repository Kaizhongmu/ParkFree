from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal

from parking_ai.domain import (
    Destination,
    FreeState,
    GeoPoint,
    LegalState,
    ParkingSegment,
    RouteCandidateSnapshot,
    RouteMatrix,
    RouteOptimizationStrategy,
    RouteOptimizerSnapshot,
    SearchRoute,
    SearchRouteStep,
)
from parking_ai.gis import DEFAULT_WALKING_SPEED_M_PER_MIN, point_geometry_distance_m
from parking_ai.routing.matrix import (
    FALLBACK_NODE_ID,
    ORIGIN_NODE_ID,
    canonical_destination,
    route_matrix_content_id,
)

GREEDY_OPTIMIZER_VERSION = "route-optimizer-greedy-v0.1.0"
BEAM_OPTIMIZER_VERSION = "route-optimizer-beam-v1.0.0"
COST_MODEL_VERSION = "expected-time-independent-v1"


class RoutePlanningError(ValueError):
    """Base error for deterministic route-planning contract violations."""


class IneligibleCandidateError(RoutePlanningError):
    """Raised when a candidate was not safely filtered before route planning."""


class UnreachableRouteError(RoutePlanningError):
    """Raised when a requested route or guaranteed fallback lacks a directed matrix edge."""


@dataclass(frozen=True)
class RoutePlanningContext:
    session_id: str
    rule_engine_version: str
    availability_model_version: str
    availability_target_window_seconds: int = 90
    require_free: bool = True
    candidate_snapshots: tuple[RouteCandidateSnapshot, ...] = ()

    def __post_init__(self) -> None:
        normalized_snapshots: list[RouteCandidateSnapshot] = []
        for snapshot in self.candidate_snapshots:
            if not isinstance(snapshot, RouteCandidateSnapshot):
                raise ValueError("candidate_snapshots must contain RouteCandidateSnapshot values")
            normalized_snapshots.append(
                snapshot.model_copy(
                    update={"evidence_refs": sorted(snapshot.evidence_refs)}, deep=True
                )
            )
        object.__setattr__(self, "candidate_snapshots", tuple(normalized_snapshots))
        _require_text(self.session_id, "session_id", maximum_length=64)
        _require_text(self.rule_engine_version, "rule_engine_version", maximum_length=128)
        _require_text(
            self.availability_model_version,
            "availability_model_version",
            maximum_length=128,
        )
        if not isinstance(self.availability_target_window_seconds, int) or isinstance(
            self.availability_target_window_seconds, bool
        ):
            raise ValueError("availability_target_window_seconds must be an integer")
        if self.availability_target_window_seconds <= 0:
            raise ValueError("availability_target_window_seconds must be positive")
        if not isinstance(self.require_free, bool):
            raise ValueError("require_free must be a boolean")
        snapshot_ids = [snapshot.segment_id for snapshot in self.candidate_snapshots]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("candidate snapshot segment IDs must be unique")
        for snapshot in self.candidate_snapshots:
            if snapshot.rule_engine_version != self.rule_engine_version:
                raise ValueError("candidate snapshots must share the declared rule-engine version")
            if snapshot.availability_model_version != self.availability_model_version:
                raise ValueError(
                    "candidate snapshots must share the declared availability-model version"
                )
            if (
                snapshot.availability_target_window_seconds
                != self.availability_target_window_seconds
            ):
                raise ValueError(
                    "candidate snapshots must share the declared availability target window"
                )


@dataclass(frozen=True)
class RouteOptimizerConfig:
    strategy: RouteOptimizationStrategy = RouteOptimizationStrategy.GREEDY
    local_search_seconds: float = 90.0
    fallback_service_seconds: float = 300.0
    walking_speed_m_per_min: float = DEFAULT_WALKING_SPEED_M_PER_MIN
    beam_width: int = 8
    max_route_steps: int | None = None
    candidate_limit: int = 20
    optimizer_version: str | None = None
    cost_model_version: str = COST_MODEL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, RouteOptimizationStrategy):
            raise ValueError("strategy must be a RouteOptimizationStrategy")
        _require_finite_positive(self.local_search_seconds, "local_search_seconds")
        _require_finite_nonnegative(self.fallback_service_seconds, "fallback_service_seconds")
        _require_finite_positive(self.walking_speed_m_per_min, "walking_speed_m_per_min")
        if not isinstance(self.beam_width, int) or isinstance(self.beam_width, bool):
            raise ValueError("beam_width must be an integer")
        if self.beam_width <= 0:
            raise ValueError("beam_width must be positive")
        if self.max_route_steps is not None:
            if not isinstance(self.max_route_steps, int) or isinstance(self.max_route_steps, bool):
                raise ValueError("max_route_steps must be an integer when provided")
            if self.max_route_steps <= 0:
                raise ValueError("max_route_steps must be positive when provided")
        if not isinstance(self.candidate_limit, int) or isinstance(self.candidate_limit, bool):
            raise ValueError("candidate_limit must be an integer")
        if self.candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        if self.optimizer_version is not None:
            _require_text(self.optimizer_version, "optimizer_version", maximum_length=128)
        _require_text(self.cost_model_version, "cost_model_version", maximum_length=128)

    @property
    def resolved_optimizer_version(self) -> str:
        if self.optimizer_version is not None:
            return self.optimizer_version
        if self.strategy is RouteOptimizationStrategy.GREEDY:
            return GREEDY_OPTIMIZER_VERSION
        return BEAM_OPTIMIZER_VERSION


@dataclass(frozen=True)
class ExpectedRouteCost:
    expected_time_seconds: float
    success_probability: float
    failure_probability: float
    leg_drive_seconds: tuple[float, ...]
    cumulative_drive_seconds: tuple[float, ...]
    walking_time_seconds: tuple[float, ...]
    first_success_probabilities: tuple[float, ...]
    success_time_seconds: tuple[float, ...]
    success_expected_contributions_seconds: tuple[float, ...]
    fallback_time_if_all_fail_seconds: float
    fallback_expected_contribution_seconds: float


@dataclass(frozen=True)
class _DecimalRouteCost:
    expected_time_seconds: Decimal
    success_probability: Decimal
    failure_probability: Decimal
    leg_drive_seconds: tuple[Decimal, ...]
    cumulative_drive_seconds: tuple[Decimal, ...]
    walking_time_seconds: tuple[Decimal, ...]
    first_success_probabilities: tuple[Decimal, ...]
    success_time_seconds: tuple[Decimal, ...]
    success_expected_contributions_seconds: tuple[Decimal, ...]
    fallback_time_if_all_fail_seconds: Decimal
    fallback_expected_contribution_seconds: Decimal

    def public(self) -> ExpectedRouteCost:
        return ExpectedRouteCost(
            expected_time_seconds=float(self.expected_time_seconds),
            success_probability=float(self.success_probability),
            failure_probability=float(self.failure_probability),
            leg_drive_seconds=tuple(map(float, self.leg_drive_seconds)),
            cumulative_drive_seconds=tuple(map(float, self.cumulative_drive_seconds)),
            walking_time_seconds=tuple(map(float, self.walking_time_seconds)),
            first_success_probabilities=tuple(map(float, self.first_success_probabilities)),
            success_time_seconds=tuple(map(float, self.success_time_seconds)),
            success_expected_contributions_seconds=tuple(
                map(float, self.success_expected_contributions_seconds)
            ),
            fallback_time_if_all_fail_seconds=float(self.fallback_time_if_all_fail_seconds),
            fallback_expected_contribution_seconds=float(
                self.fallback_expected_contribution_seconds
            ),
        )


def calculate_expected_route_cost(
    route: Sequence[ParkingSegment],
    route_matrix: RouteMatrix,
    walking_time_seconds: Mapping[str, float],
    *,
    local_search_seconds: float = 90.0,
    fallback_service_seconds: float = 300.0,
) -> ExpectedRouteCost:
    """Calculate the exact Phase 5 independent-success expected-time objective."""

    _require_finite_positive(local_search_seconds, "local_search_seconds")
    _require_finite_nonnegative(fallback_service_seconds, "fallback_service_seconds")
    decimal_walks = _validated_walking_times(route, walking_time_seconds)
    return _calculate_decimal_route_cost(
        route,
        route_matrix,
        decimal_walks,
        local_search_seconds=_decimal(local_search_seconds),
        fallback_service_seconds=_decimal(fallback_service_seconds),
    ).public()


class DeterministicSearchRoutePlanner:
    """Greedy or bounded-beam planner implementing the stable SearchRoutePlanner protocol."""

    def __init__(
        self,
        context: RoutePlanningContext,
        *,
        config: RouteOptimizerConfig | None = None,
    ) -> None:
        self._context = context
        self._config = config or RouteOptimizerConfig()
        self._snapshots_by_id = {
            snapshot.segment_id: snapshot.model_copy(deep=True)
            for snapshot in context.candidate_snapshots
        }
        if self._config.local_search_seconds != context.availability_target_window_seconds:
            raise ValueError(
                "local_search_seconds must match the availability target window for Phase 5"
            )

    def plan_search_route(
        self,
        candidates: list[ParkingSegment],
        origin: GeoPoint,
        destination: Destination,
        route_matrix: RouteMatrix,
    ) -> SearchRoute:
        ordered_candidates = self._validate_candidates(candidates)
        if len(ordered_candidates) > self._config.candidate_limit:
            raise RoutePlanningError(
                f"candidate count exceeds configured limit {self._config.candidate_limit}"
            )
        self._validate_matrix_binding(
            route_matrix,
            origin,
            destination,
            ordered_candidates,
        )
        if ORIGIN_NODE_ID not in route_matrix.travel_time_seconds:
            raise UnreachableRouteError("route matrix is missing the reserved origin row")
        _edge_seconds(route_matrix, ORIGIN_NODE_ID, FALLBACK_NODE_ID)

        walks = self._walking_times(ordered_candidates, destination)
        usable_candidates = tuple(
            candidate for candidate in ordered_candidates if candidate.availability_probability != 0
        )
        maximum_steps = min(
            len(usable_candidates),
            self._config.max_route_steps or len(usable_candidates),
        )
        if self._config.strategy is RouteOptimizationStrategy.GREEDY:
            selected = self._greedy_route(
                usable_candidates,
                route_matrix,
                walks,
                maximum_steps=maximum_steps,
            )
        else:
            selected = self._beam_route(
                usable_candidates,
                route_matrix,
                walks,
                maximum_steps=maximum_steps,
            )

        cost = self._cost(selected, route_matrix, walks)
        return self._build_route(
            selected,
            cost,
            origin,
            destination,
            ordered_candidates,
            route_matrix,
            walks,
        )

    def _validate_candidates(
        self, candidates: Sequence[ParkingSegment]
    ) -> tuple[ParkingSegment, ...]:
        by_id: dict[str, ParkingSegment] = {}
        for candidate in candidates:
            if (
                not candidate.segment_id.strip()
                or candidate.segment_id != candidate.segment_id.strip()
            ):
                raise IneligibleCandidateError("candidate segment IDs must be nonblank and trimmed")
            if candidate.segment_id in {ORIGIN_NODE_ID, FALLBACK_NODE_ID}:
                raise IneligibleCandidateError(
                    f"segment ID {candidate.segment_id!r} is reserved for route planning"
                )
            if candidate.segment_id in by_id:
                raise IneligibleCandidateError(
                    f"duplicate candidate segment ID: {candidate.segment_id}"
                )
            if candidate.legal_state is not LegalState.LEGAL:
                raise IneligibleCandidateError(
                    f"candidate {candidate.segment_id} must be LEGAL before route planning"
                )
            if self._context.require_free:
                if candidate.free_state is not FreeState.FREE:
                    raise IneligibleCandidateError(
                        f"candidate {candidate.segment_id} must be FREE for this planning context"
                    )
            elif candidate.free_state is FreeState.UNKNOWN:
                raise IneligibleCandidateError(
                    f"candidate {candidate.segment_id} has unknown payment state"
                )
            if candidate.availability_probability is None:
                raise IneligibleCandidateError(
                    f"candidate {candidate.segment_id} is missing availability probability"
                )
            by_id[candidate.segment_id] = candidate
        if set(by_id) != set(self._snapshots_by_id):
            raise IneligibleCandidateError(
                "planning context snapshots must exactly match candidate segment IDs"
            )
        for segment_id, candidate in by_id.items():
            snapshot = self._snapshots_by_id[segment_id]
            if (
                candidate.legal_state is not snapshot.legal_state
                or candidate.free_state is not snapshot.free_state
                or candidate.legal_confidence != snapshot.legal_confidence
            ):
                raise IneligibleCandidateError(
                    f"candidate {segment_id} does not match its legality evaluation snapshot"
                )
            if candidate.availability_probability != snapshot.availability_probability:
                raise IneligibleCandidateError(
                    f"candidate {segment_id} does not match its availability prediction snapshot"
                )
        return tuple(by_id[segment_id] for segment_id in sorted(by_id))

    def _validate_matrix_binding(
        self,
        route_matrix: RouteMatrix,
        origin: GeoPoint,
        destination: Destination,
        candidates: Sequence[ParkingSegment],
    ) -> None:
        binding = route_matrix.binding
        if binding is None or route_matrix.matrix_id is None:
            raise RoutePlanningError("route matrix must include a content ID and request binding")
        if route_matrix_content_id(route_matrix) != route_matrix.matrix_id:
            raise RoutePlanningError("route matrix content ID does not match its bound costs")
        if binding.origin != origin:
            raise RoutePlanningError("route matrix origin binding does not match the request")
        if binding.destination != canonical_destination(destination):
            raise RoutePlanningError("route matrix destination binding does not match the request")
        candidate_ids = [candidate.segment_id for candidate in candidates]
        if binding.candidate_segment_ids != candidate_ids:
            raise RoutePlanningError("route matrix candidate binding does not match the request")

    def _walking_times(
        self,
        candidates: Sequence[ParkingSegment],
        destination: Destination,
    ) -> dict[str, Decimal]:
        access_points = [point.location for point in destination.access_points]
        if not access_points:
            access_points = [destination.location]
        return {
            candidate.segment_id: _decimal(
                min(
                    point_geometry_distance_m(point, candidate.geometry.coordinates)
                    for point in access_points
                )
                / self._config.walking_speed_m_per_min
                * 60.0
            )
            for candidate in candidates
        }

    def _cost(
        self,
        route: Sequence[ParkingSegment],
        route_matrix: RouteMatrix,
        walks: Mapping[str, Decimal],
    ) -> _DecimalRouteCost:
        return _calculate_decimal_route_cost(
            route,
            route_matrix,
            walks,
            local_search_seconds=_decimal(self._config.local_search_seconds),
            fallback_service_seconds=_decimal(self._config.fallback_service_seconds),
        )

    def _greedy_route(
        self,
        candidates: Sequence[ParkingSegment],
        route_matrix: RouteMatrix,
        walks: Mapping[str, Decimal],
        *,
        maximum_steps: int,
    ) -> tuple[ParkingSegment, ...]:
        route: tuple[ParkingSegment, ...] = ()
        remaining = {candidate.segment_id: candidate for candidate in candidates}
        while len(route) < maximum_steps:
            current_node = route[-1].segment_id if route else ORIGIN_NODE_ID
            extensions = [
                (*route, candidate)
                for candidate in remaining.values()
                if candidate.segment_id in route_matrix.travel_time_seconds.get(current_node, {})
                and FALLBACK_NODE_ID
                in route_matrix.travel_time_seconds.get(candidate.segment_id, {})
            ]
            if not extensions:
                break
            options = [route, *extensions]
            selected = min(options, key=lambda item: self._route_key(item, route_matrix, walks))
            if selected == route:
                break
            route = selected
            remaining.pop(route[-1].segment_id)
            if route[-1].availability_probability == 1:
                break
        return route

    def _beam_route(
        self,
        candidates: Sequence[ParkingSegment],
        route_matrix: RouteMatrix,
        walks: Mapping[str, Decimal],
        *,
        maximum_steps: int,
    ) -> tuple[ParkingSegment, ...]:
        frontier: list[tuple[ParkingSegment, ...]] = [()]
        completed: list[tuple[ParkingSegment, ...]] = [()]
        candidates_by_id = {candidate.segment_id: candidate for candidate in candidates}
        for _ in range(maximum_steps):
            extensions: list[tuple[ParkingSegment, ...]] = []
            for route in frontier:
                if route and route[-1].availability_probability == 1:
                    continue
                used_ids = {candidate.segment_id for candidate in route}
                current_node = route[-1].segment_id if route else ORIGIN_NODE_ID
                for segment_id in sorted(candidates_by_id.keys() - used_ids):
                    if segment_id not in route_matrix.travel_time_seconds.get(current_node, {}):
                        continue
                    extensions.append((*route, candidates_by_id[segment_id]))
            if not extensions:
                break
            completed.extend(
                route
                for route in extensions
                if FALLBACK_NODE_ID
                in route_matrix.travel_time_seconds.get(route[-1].segment_id, {})
            )
            extensions.sort(key=lambda item: self._partial_route_key(item, route_matrix, walks))
            frontier = extensions[: self._config.beam_width]
        return min(completed, key=lambda item: self._route_key(item, route_matrix, walks))

    def _partial_route_key(
        self,
        route: Sequence[ParkingSegment],
        route_matrix: RouteMatrix,
        walks: Mapping[str, Decimal],
    ) -> tuple[Decimal, Decimal, int, tuple[str, ...]]:
        failure_probability = Decimal(1)
        cumulative_drive = Decimal(0)
        expected_success_time = Decimal(0)
        current_node = ORIGIN_NODE_ID
        local_search_seconds = _decimal(self._config.local_search_seconds)
        for index, candidate in enumerate(route):
            probability = _decimal(candidate.availability_probability or 0.0)
            cumulative_drive += _edge_seconds(route_matrix, current_node, candidate.segment_id)
            first_success = failure_probability * probability
            success_time = (
                cumulative_drive + (index + 1) * local_search_seconds + walks[candidate.segment_id]
            )
            expected_success_time += first_success * success_time
            failure_probability *= Decimal(1) - probability
            current_node = candidate.segment_id
        return (
            expected_success_time,
            -(Decimal(1) - failure_probability),
            len(route),
            tuple(candidate.segment_id for candidate in route),
        )

    def _route_key(
        self,
        route: Sequence[ParkingSegment],
        route_matrix: RouteMatrix,
        walks: Mapping[str, Decimal],
    ) -> tuple[Decimal, Decimal, int, tuple[str, ...]]:
        cost = self._cost(route, route_matrix, walks)
        return (
            cost.expected_time_seconds,
            -cost.success_probability,
            len(route),
            tuple(candidate.segment_id for candidate in route),
        )

    def _build_route(
        self,
        selected: Sequence[ParkingSegment],
        cost: _DecimalRouteCost,
        origin: GeoPoint,
        destination: Destination,
        all_candidates: Sequence[ParkingSegment],
        route_matrix: RouteMatrix,
        walks: Mapping[str, Decimal],
    ) -> SearchRoute:
        route_id = _route_id(
            selected,
            cost,
            origin,
            destination,
            all_candidates,
            route_matrix,
            walks,
            self._context,
            self._config,
            self._snapshots_by_id,
        )
        steps = [
            SearchRouteStep(
                route_step_id=_step_id(route_id, index, candidate.segment_id),
                session_id=self._context.session_id,
                segment_id=candidate.segment_id,
                step_order=index,
                legal_state=self._snapshots_by_id[candidate.segment_id].legal_state,
                free_state=self._snapshots_by_id[candidate.segment_id].free_state,
                legal_confidence=self._snapshots_by_id[candidate.segment_id].legal_confidence,
                availability_probability=self._snapshots_by_id[
                    candidate.segment_id
                ].availability_probability,
                drive_eta_min=_rounded_minutes(cost.leg_drive_seconds[index]),
                walk_min=_rounded_minutes(cost.walking_time_seconds[index]),
                evidence_refs=sorted(self._snapshots_by_id[candidate.segment_id].evidence_refs),
                rule_engine_version=self._snapshots_by_id[candidate.segment_id].rule_engine_version,
                availability_model_version=self._snapshots_by_id[
                    candidate.segment_id
                ].availability_model_version,
            )
            for index, candidate in enumerate(selected)
        ]
        return SearchRoute(
            route_id=route_id,
            session_id=self._context.session_id,
            steps=steps,
            expected_time_to_park_min=_rounded_minutes(cost.expected_time_seconds),
            success_probability=_rounded_probability(cost.success_probability),
            fallback_description=(
                route_matrix.binding.fallback.description
                if route_matrix.binding is not None
                else None
            ),
            optimizer_version=self._config.resolved_optimizer_version,
            failure_probability=_rounded_probability(cost.failure_probability),
            fallback_time_min=_rounded_minutes(cost.fallback_time_if_all_fail_seconds),
            fallback_expected_contribution_min=_rounded_minutes(
                cost.fallback_expected_contribution_seconds
            ),
            route_matrix_version=route_matrix.matrix_id,
            route_matrix_provider_version=route_matrix.provider_version,
            cost_model_version=self._config.cost_model_version,
            optimization_strategy=self._config.strategy,
            optimizer_snapshot=RouteOptimizerSnapshot(
                optimization_strategy=self._config.strategy,
                optimizer_version=self._config.resolved_optimizer_version,
                cost_model_version=self._config.cost_model_version,
                rule_engine_version=self._context.rule_engine_version,
                availability_model_version=self._context.availability_model_version,
                availability_target_window_seconds=(
                    self._context.availability_target_window_seconds
                ),
                require_free=self._context.require_free,
                local_search_seconds=self._config.local_search_seconds,
                fallback_service_seconds=self._config.fallback_service_seconds,
                walking_speed_m_per_min=self._config.walking_speed_m_per_min,
                beam_width=self._config.beam_width,
                max_route_steps=self._config.max_route_steps,
                candidate_limit=self._config.candidate_limit,
            ),
            selected_candidate_snapshots=[
                self._snapshots_by_id[candidate.segment_id] for candidate in selected
            ],
        )


def _calculate_decimal_route_cost(
    route: Sequence[ParkingSegment],
    route_matrix: RouteMatrix,
    walking_time_seconds: Mapping[str, Decimal],
    *,
    local_search_seconds: Decimal,
    fallback_service_seconds: Decimal,
) -> _DecimalRouteCost:
    segment_ids = [candidate.segment_id for candidate in route]
    if len(segment_ids) != len(set(segment_ids)):
        raise RoutePlanningError("expected-cost route must not repeat a segment")

    failure_probability = Decimal(1)
    cumulative_drive = Decimal(0)
    expected_time = Decimal(0)
    current_node = ORIGIN_NODE_ID
    leg_drives: list[Decimal] = []
    cumulative_drives: list[Decimal] = []
    walks: list[Decimal] = []
    first_success_probabilities: list[Decimal] = []
    success_times: list[Decimal] = []
    success_contributions: list[Decimal] = []

    for index, candidate in enumerate(route):
        if candidate.availability_probability is None:
            raise RoutePlanningError(
                f"candidate {candidate.segment_id} is missing availability probability"
            )
        leg_drive = _edge_seconds(route_matrix, current_node, candidate.segment_id)
        walk = walking_time_seconds.get(candidate.segment_id)
        if walk is None:
            raise RoutePlanningError(
                f"walking time is missing for candidate {candidate.segment_id}"
            )
        probability = _decimal(candidate.availability_probability)
        cumulative_drive += leg_drive
        first_success = failure_probability * probability
        success_time = cumulative_drive + (index + 1) * local_search_seconds + walk
        contribution = first_success * success_time
        expected_time += contribution
        failure_probability *= Decimal(1) - probability

        leg_drives.append(leg_drive)
        cumulative_drives.append(cumulative_drive)
        walks.append(walk)
        first_success_probabilities.append(first_success)
        success_times.append(success_time)
        success_contributions.append(contribution)
        current_node = candidate.segment_id

    fallback_drive = _edge_seconds(route_matrix, current_node, FALLBACK_NODE_ID)
    fallback_time = (
        cumulative_drive
        + len(route) * local_search_seconds
        + fallback_drive
        + fallback_service_seconds
    )
    fallback_contribution = failure_probability * fallback_time
    expected_time += fallback_contribution
    return _DecimalRouteCost(
        expected_time_seconds=expected_time,
        success_probability=Decimal(1) - failure_probability,
        failure_probability=failure_probability,
        leg_drive_seconds=tuple(leg_drives),
        cumulative_drive_seconds=tuple(cumulative_drives),
        walking_time_seconds=tuple(walks),
        first_success_probabilities=tuple(first_success_probabilities),
        success_time_seconds=tuple(success_times),
        success_expected_contributions_seconds=tuple(success_contributions),
        fallback_time_if_all_fail_seconds=fallback_time,
        fallback_expected_contribution_seconds=fallback_contribution,
    )


def _validated_walking_times(
    route: Sequence[ParkingSegment], walking_time_seconds: Mapping[str, float]
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for candidate in route:
        value = walking_time_seconds.get(candidate.segment_id)
        if value is None:
            raise RoutePlanningError(
                f"walking time is missing for candidate {candidate.segment_id}"
            )
        _require_finite_nonnegative(value, f"walking time for {candidate.segment_id}")
        result[candidate.segment_id] = _decimal(value)
    return result


def _edge_seconds(route_matrix: RouteMatrix, source_id: str, target_id: str) -> Decimal:
    seconds = route_matrix.travel_time_seconds.get(source_id, {}).get(target_id)
    if seconds is None:
        raise UnreachableRouteError(
            f"route matrix has no directed edge {source_id!r} -> {target_id!r}"
        )
    return _decimal(seconds)


def _route_id(
    selected: Sequence[ParkingSegment],
    cost: _DecimalRouteCost,
    origin: GeoPoint,
    destination: Destination,
    all_candidates: Sequence[ParkingSegment],
    route_matrix: RouteMatrix,
    walks: Mapping[str, Decimal],
    context: RoutePlanningContext,
    config: RouteOptimizerConfig,
    snapshots_by_id: Mapping[str, RouteCandidateSnapshot],
) -> str:
    planning_nodes = {
        ORIGIN_NODE_ID,
        FALLBACK_NODE_ID,
        *(candidate.segment_id for candidate in all_candidates),
    }
    matrix_payload = {
        source_id: {
            target_id: seconds
            for target_id, seconds in sorted(targets.items())
            if target_id in planning_nodes
        }
        for source_id, targets in sorted(route_matrix.travel_time_seconds.items())
        if source_id in planning_nodes
    }
    candidate_payload = [
        {
            "decision_snapshot": snapshots_by_id[candidate.segment_id].model_dump(mode="json"),
            "walking_time_seconds": str(walks[candidate.segment_id]),
        }
        for candidate in all_candidates
    ]
    destination_payload = destination.model_dump(mode="json", exclude={"access_points"})
    destination_payload["access_points"] = [
        access.model_dump(mode="json")
        for access in sorted(destination.access_points, key=lambda item: item.access_point_id)
    ]
    payload = {
        "candidates": candidate_payload,
        "config": {
            **asdict(config),
            "strategy": config.strategy.value,
            "optimizer_version": config.resolved_optimizer_version,
        },
        "context": {
            "availability_model_version": context.availability_model_version,
            "availability_target_window_seconds": (context.availability_target_window_seconds),
            "require_free": context.require_free,
            "rule_engine_version": context.rule_engine_version,
            "session_id": context.session_id,
        },
        "cost": {
            "expected_time_seconds": str(cost.expected_time_seconds),
            "failure_probability": str(cost.failure_probability),
            "fallback_time_if_all_fail_seconds": str(cost.fallback_time_if_all_fail_seconds),
            "success_probability": str(cost.success_probability),
        },
        "destination": destination_payload,
        "id_scheme_version": 1,
        "matrix": matrix_payload,
        "matrix_id": route_matrix.matrix_id,
        "matrix_provider_version": route_matrix.provider_version,
        "origin": origin.model_dump(mode="json"),
        "selected_segment_ids": [candidate.segment_id for candidate in selected],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"route_{digest[:32]}"


def _step_id(route_id: str, step_order: int, segment_id: str) -> str:
    payload = {
        "id_scheme_version": 1,
        "route_id": route_id,
        "segment_id": segment_id,
        "step_order": step_order,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"step_{digest[:32]}"


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(value))


def _rounded_minutes(seconds: Decimal) -> float:
    return round(float(seconds / Decimal(60)), 12)


def _rounded_probability(value: Decimal) -> float:
    return round(float(value), 12)


def _require_text(value: str, label: str, *, maximum_length: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value) > maximum_length:
        raise ValueError(f"{label} must not exceed {maximum_length} characters")


def _require_finite_positive(value: float, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{label} must be finite and positive")


def _require_finite_nonnegative(value: float, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{label} must be finite and nonnegative")
