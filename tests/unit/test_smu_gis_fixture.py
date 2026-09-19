import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from parking_ai.domain import EvidenceSourceType, EvidenceStoragePolicy, PhysicalState
from parking_ai.gis.adapters import load_osm_fixture
from parking_ai.gis.fixtures import DATA_DIRECTORY, load_smu_destination, load_smu_gis_fixture
from parking_ai.gis.models import SideReferenceDirection


def _copy_fixture_with_change(
    tmp_path: Path,
    fixture_name: str,
    change: Callable[[dict[str, Any]], None],
) -> Path:
    data = json.loads((DATA_DIRECTORY / fixture_name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    change(data)
    path = tmp_path / fixture_name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_smu_destination_fixture_has_valid_access_points() -> None:
    fixture = load_smu_gis_fixture()
    destination = fixture.destination

    assert destination.destination_id == "smu-fondren-library"
    assert destination.name == "Fondren Library Center"
    assert len(destination.access_points) == 2
    assert destination.location.latitude == 32.8444462
    assert destination.location.longitude == -96.7834949
    assert {point.access_point_id for point in destination.access_points} == {
        "smu-fondren-osm-entrance-6080676255",
        "smu-fondren-osm-entrance-11289496087",
    }
    assert {point.destination_id for point in destination.access_points} == {
        destination.destination_id
    }
    for point in [destination.location, *(item.location for item in destination.access_points)]:
        assert 32.83 < point.latitude < 32.85
        assert -96.80 < point.longitude < -96.77


def test_osm_fixture_coordinates_use_longitude_latitude_order_and_bounds() -> None:
    fixture = load_smu_gis_fixture()

    assert len(fixture.osm.roads) == 102
    for road in fixture.osm.roads:
        assert road.feature_id.split("#", maxsplit=1)[0].isdigit()
        for longitude, latitude in road.geometry.coordinates:
            assert -96.791 < longitude < -96.776
            assert 32.838 < latitude < 32.851


def test_osm_fixture_retains_storage_and_attribution_policy() -> None:
    fixture = load_smu_gis_fixture()
    evidence = fixture.osm.evidence

    assert evidence.source_type is EvidenceSourceType.OSM
    assert evidence.raw_storage_policy is EvidenceStoragePolicy.PERSIST
    assert evidence.content_hash is not None
    assert len(evidence.content_hash) == 64
    assert fixture.osm.source_uri == (
        "https://api.openstreetmap.org/api/0.6/map?bbox=-96.795,32.834,-96.773,32.852"
    )
    assert fixture.osm.license == ("Open Data Commons Open Database License 1.0 (ODbL-1.0)")
    assert "OpenStreetMap contributors" in fixture.osm.attribution
    assert {road.physical_state for road in fixture.osm.roads} == {PhysicalState.UNKNOWN}


def test_adapter_marks_real_shared_intersection_nodes_and_clip_boundaries() -> None:
    fixture = load_smu_gis_fixture()
    raw = json.loads((DATA_DIRECTORY / "smu_osm_fixture_v1.json").read_text(encoding="utf-8"))
    reference_counts = Counter(node_ref for way in raw["ways"] for node_ref in way["node_refs"])

    durham = next(road for road in fixture.osm.roads if road.feature_id == "9952277#0")
    daniel = next(road for road in fixture.osm.roads if road.feature_id == "10010265#0")
    shared_node = "81695256"
    durham_raw = next(way for way in raw["ways"] if way["way_id"] == durham.feature_id)
    daniel_raw = next(way for way in raw["ways"] if way["way_id"] == daniel.feature_id)

    assert reference_counts[shared_node] == 2
    assert durham_raw["node_refs"].index(shared_node) in durham.break_indexes
    assert daniel_raw["node_refs"].index(shared_node) in daniel.break_indexes
    assert durham.side_reference_direction is SideReferenceDirection.SOURCE_GEOMETRY
    boundary_node_ids = {
        node["node_id"] for node in raw["nodes"] if node.get("fixture_boundary") is True
    }
    assert boundary_node_ids
    adapted_roads = {road.feature_id: road for road in fixture.osm.roads}
    boundary_occurrences = 0
    for way in raw["ways"]:
        for node_id in boundary_node_ids.intersection(way["node_refs"]):
            index = way["node_refs"].index(node_id)
            assert index in adapted_roads[way["way_id"]].break_indexes
            boundary_occurrences += 1
    assert boundary_occurrences >= 1


def test_destination_fixture_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    path = _copy_fixture_with_change(
        tmp_path,
        "smu_fondren_destination_v1.json",
        lambda data: data.__setitem__("schema_version", 2),
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_smu_destination(path)


def test_osm_fixture_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    path = _copy_fixture_with_change(
        tmp_path,
        "smu_osm_fixture_v1.json",
        lambda data: data.__setitem__("schema_version", 2),
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_osm_fixture(path)


def test_destination_fixture_rejects_duplicate_access_point_ids(tmp_path: Path) -> None:
    def duplicate_access_point(data: dict[str, Any]) -> None:
        access_points = data["access_points"]
        assert isinstance(access_points, list)
        access_points.append(access_points[0])

    path = _copy_fixture_with_change(
        tmp_path,
        "smu_fondren_destination_v1.json",
        duplicate_access_point,
    )

    with pytest.raises(ValueError, match="access-point IDs must be unique"):
        load_smu_destination(path)


def test_osm_fixture_rejects_duplicate_way_ids(tmp_path: Path) -> None:
    def duplicate_way(data: dict[str, Any]) -> None:
        ways = data["ways"]
        assert isinstance(ways, list)
        ways.append(ways[0])

    path = _copy_fixture_with_change(
        tmp_path,
        "smu_osm_fixture_v1.json",
        duplicate_way,
    )

    with pytest.raises(ValueError, match="way IDs must be unique"):
        load_osm_fixture(path)
