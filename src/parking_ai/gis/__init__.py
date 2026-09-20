"""GIS package; candidate generation begins in Phase 2."""

from parking_ai.gis.adapters.postgis import (
    PostGISCandidateSegmentService,
    PostGISDestinationResolver,
)
from parking_ai.gis.fixtures import SMUGISFixture, load_smu_gis_fixture
from parking_ai.gis.generator import (
    DEFAULT_WALKING_SPEED_M_PER_MIN,
    DeterministicCandidateSegmentService,
    point_geometry_distance_m,
)
from parking_ai.gis.persistence import upsert_gis_slice

__all__ = [
    "DEFAULT_WALKING_SPEED_M_PER_MIN",
    "DeterministicCandidateSegmentService",
    "PostGISCandidateSegmentService",
    "PostGISDestinationResolver",
    "SMUGISFixture",
    "load_smu_gis_fixture",
    "point_geometry_distance_m",
    "upsert_gis_slice",
]
