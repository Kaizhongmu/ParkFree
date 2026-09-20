"""Provider-independent deterministic route-matrix and optimization tools."""

from parking_ai.routing.local_matrix import (
    DEFAULT_LOCAL_DRIVING_SPEED_M_PER_MIN,
    LOCAL_MATRIX_PROVIDER_VERSION,
    LocalDeterministicRouteMatrixProvider,
)
from parking_ai.routing.matrix import (
    FALLBACK_NODE_ID,
    MATRIX_SCHEMA_VERSION,
    ORIGIN_NODE_ID,
    SYNTHETIC_MATRIX_VERSION,
    build_synthetic_route_matrix,
    canonical_destination,
    route_matrix_content_id,
)
from parking_ai.routing.optimizer import (
    BEAM_OPTIMIZER_VERSION,
    COST_MODEL_VERSION,
    GREEDY_OPTIMIZER_VERSION,
    DeterministicSearchRoutePlanner,
    ExpectedRouteCost,
    IneligibleCandidateError,
    RouteOptimizerConfig,
    RoutePlanningContext,
    RoutePlanningError,
    UnreachableRouteError,
    calculate_expected_route_cost,
)

__all__ = [
    "BEAM_OPTIMIZER_VERSION",
    "COST_MODEL_VERSION",
    "DEFAULT_LOCAL_DRIVING_SPEED_M_PER_MIN",
    "FALLBACK_NODE_ID",
    "GREEDY_OPTIMIZER_VERSION",
    "LOCAL_MATRIX_PROVIDER_VERSION",
    "MATRIX_SCHEMA_VERSION",
    "ORIGIN_NODE_ID",
    "SYNTHETIC_MATRIX_VERSION",
    "DeterministicSearchRoutePlanner",
    "ExpectedRouteCost",
    "IneligibleCandidateError",
    "LocalDeterministicRouteMatrixProvider",
    "RouteOptimizerConfig",
    "RoutePlanningContext",
    "RoutePlanningError",
    "UnreachableRouteError",
    "build_synthetic_route_matrix",
    "calculate_expected_route_cost",
    "canonical_destination",
    "route_matrix_content_id",
]
