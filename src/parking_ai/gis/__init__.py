"""GIS package; candidate generation begins in Phase 2."""

from parking_ai.gis.fixtures import SMUGISFixture, load_smu_gis_fixture
from parking_ai.gis.generator import DeterministicCandidateSegmentService
from parking_ai.gis.persistence import upsert_gis_slice

__all__ = [
    "DeterministicCandidateSegmentService",
    "SMUGISFixture",
    "load_smu_gis_fixture",
    "upsert_gis_slice",
]
