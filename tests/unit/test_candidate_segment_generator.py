from datetime import UTC, datetime

import pytest

from parking_ai.domain import (
    Destination,
    DestinationAccessPoint,
    FreeState,
    GeoPoint,
    LegalState,
    LineStringGeometry,
    ParkingSegment,
    PhysicalState,
    SearchConstraints,
    SegmentSide,
)
from parking_ai.gis.fixtures import load_smu_gis_fixture
from parking_ai.gis.generator import (
    CandidateSegmentConflictError,
    DeterministicCandidateSegmentService,
    geodesic_length_m,
    normalize_geometry,
    stable_segment_id,
)
from parking_ai.gis.models import RoadFeature, SideReferenceDirection

NOW = datetime(2026, 9, 19, tzinfo=UTC)


def _destination(latitude: float = 32.8425, longitude: float = -96.7855) -> Destination:
    return Destination(
        destination_id="test-destination",
        name="Test destination",
        location=GeoPoint(latitude=latitude, longitude=longitude),
    )


def _road(
    feature_id: str,
    coordinates: list[tuple[float, float]],
    *,
    road_type: str = "residential",
    parking_candidate: bool | None = None,
    break_indexes: frozenset[int] = frozenset(),
) -> RoadFeature:
    return RoadFeature(
        feature_id=feature_id,
        geometry=LineStringGeometry(coordinates=coordinates),
        street_name=f"Street {feature_id}",
        road_type=road_type,
        parking_candidate=parking_candidate,
        break_indexes=break_indexes,
    )


def _service(roads: list[RoadFeature]) -> DeterministicCandidateSegmentService:
    return DeterministicCandidateSegmentService(
        roads,
        evidence_id="fixture-evidence",
        data_freshness=NOW,
    )


def _all_nearby(
    service: DeterministicCandidateSegmentService, limit: int = 100
) -> list[ParkingSegment]:
    return service.get_candidate_segments(
        _destination(), SearchConstraints(max_walk_minutes=30, max_candidates=limit)
    )


def test_stable_ids_repeat_and_do_not_depend_on_input_order() -> None:
    first = _road("first", [(-96.786, 32.842), (-96.785, 32.842)])
    second = _road("second", [(-96.785, 32.843), (-96.784, 32.843)])

    forward_ids = [segment.segment_id for segment in _all_nearby(_service([first, second]))]
    reverse_ids = [segment.segment_id for segment in _all_nearby(_service([second, first]))]

    assert forward_ids == reverse_ids
    assert len(forward_ids) == len(set(forward_ids)) == 4


def test_identical_stable_id_records_deduplicate_with_canonical_output() -> None:
    geometry = LineStringGeometry(coordinates=[(-96.786, 32.842), (-96.785, 32.842)])
    padded = RoadFeature(
        feature_id="padded",
        geometry=geometry,
        street_name="Main  Street ",
        road_type=" RESIDENTIAL ",
    )
    canonical = RoadFeature(
        feature_id="canonical",
        geometry=geometry,
        street_name="Main Street",
        road_type="residential",
    )

    forward = _all_nearby(_service([padded, canonical]))
    reverse = _all_nearby(_service([canonical, padded]))

    assert forward == reverse
    assert len(forward) == 2
    assert {segment.street_name for segment in forward} == {"Main Street"}
    assert {segment.road_type for segment in forward} == {"residential"}


def test_conflicting_stable_id_records_fail_independently_of_input_order() -> None:
    geometry = LineStringGeometry(coordinates=[(-96.786, 32.842), (-96.785, 32.842)])
    parkable = RoadFeature(
        feature_id="parkable",
        geometry=geometry,
        street_name="Conflict Street",
        road_type="residential",
        physical_state=PhysicalState.PARKABLE,
    )
    not_parkable = parkable.model_copy(
        update={
            "feature_id": "not-parkable",
            "physical_state": PhysicalState.NOT_PARKABLE,
        }
    )

    messages: list[str] = []
    for roads in ([parkable, not_parkable], [not_parkable, parkable]):
        with pytest.raises(CandidateSegmentConflictError) as error:
            _all_nearby(_service(roads))
        messages.append(str(error.value))

    assert messages[0] == messages[1]
    assert "not-parkable" in messages[0]
    assert "parkable" in messages[0]


def test_stable_id_is_direction_independent_but_changes_with_meaningful_content() -> None:
    geometry = [(-96.786, 32.842), (-96.785, 32.842)]
    original = stable_segment_id(geometry, "Test Street", "residential", SegmentSide.LEFT)
    reversed_id = stable_segment_id(
        list(reversed(geometry)), "Test Street", "residential", SegmentSide.LEFT
    )
    moved = stable_segment_id(
        [geometry[0], (-96.7849, 32.842)],
        "Test Street",
        "residential",
        SegmentSide.LEFT,
    )
    renamed = stable_segment_id(geometry, "Other Street", "residential", SegmentSide.LEFT)
    reclassified = stable_segment_id(geometry, "Test Street", "tertiary", SegmentSide.LEFT)
    opposite_side = stable_segment_id(geometry, "Test Street", "residential", SegmentSide.RIGHT)

    assert original == reversed_id
    assert len({original, moved, renamed, reclassified, opposite_side}) == 5


def test_geometry_normalization_rounds_and_removes_adjacent_duplicates() -> None:
    normalized = normalize_geometry(
        [
            (-96.785000001, 32.842000001),
            (-96.785000002, 32.842000002),
            (-96.784, 32.842),
        ]
    )

    assert normalized == ((-96.785, 32.842), (-96.784, 32.842))


def test_filtering_uses_road_class_and_explicit_fixture_override() -> None:
    roads = [
        _road("eligible", [(-96.786, 32.842), (-96.785, 32.842)]),
        _road(
            "primary",
            [(-96.786, 32.8421), (-96.785, 32.8421)],
            road_type="primary",
        ),
        _road(
            "forced-service",
            [(-96.786, 32.8422), (-96.785, 32.8422)],
            road_type="service",
            parking_candidate=True,
        ),
        _road(
            "blocked",
            [(-96.786, 32.8423), (-96.785, 32.8423)],
            parking_candidate=False,
        ),
    ]

    segments = _all_nearby(_service(roads))

    assert {segment.street_name for segment in segments} == {
        "Street eligible",
        "Street forced-service",
    }
    assert len(segments) == 4


def test_intersection_splitting_and_left_right_side_separation() -> None:
    road = _road(
        "split",
        [(-96.786, 32.842), (-96.785, 32.842), (-96.784, 32.842)],
        break_indexes=frozenset({0, 1, 2}),
    )

    segments = _all_nearby(_service([road]))

    assert len(segments) == 4
    assert {segment.side for segment in segments} == {SegmentSide.LEFT, SegmentSide.RIGHT}
    geometry_groups: dict[tuple[tuple[float, float], ...], set[SegmentSide]] = {}
    for segment in segments:
        geometry = tuple(segment.geometry.coordinates)
        geometry_groups.setdefault(geometry, set()).add(segment.side)
        assert geometry[0] < geometry[-1]
    assert list(geometry_groups.values()) == [
        {SegmentSide.LEFT, SegmentSide.RIGHT},
        {SegmentSide.LEFT, SegmentSide.RIGHT},
    ]


def test_source_relative_one_sided_road_maps_to_canonical_geometry_side() -> None:
    west_to_east = RoadFeature(
        feature_id="forward",
        geometry=LineStringGeometry(coordinates=[(-96.786, 32.842), (-96.785, 32.842)]),
        street_name="One-sided Street",
        road_type="residential",
        candidate_sides=(SegmentSide.LEFT,),
        side_reference_direction=SideReferenceDirection.SOURCE_GEOMETRY,
    )
    east_to_west_same_curb = west_to_east.model_copy(
        update={
            "feature_id": "reverse",
            "geometry": LineStringGeometry(
                coordinates=list(reversed(west_to_east.geometry.coordinates))
            ),
            "candidate_sides": (SegmentSide.RIGHT,),
        }
    )

    forward = _all_nearby(_service([west_to_east]))
    reverse = _all_nearby(_service([east_to_west_same_curb]))

    assert forward == reverse
    assert len(forward) == 1
    assert forward[0].side is SegmentSide.LEFT


def test_canonical_side_reference_is_not_swapped_for_reversed_source_geometry() -> None:
    road = RoadFeature(
        feature_id="canonical-side",
        geometry=LineStringGeometry(coordinates=[(-96.785, 32.842), (-96.786, 32.842)]),
        street_name="Canonical Street",
        road_type="residential",
        candidate_sides=(SegmentSide.LEFT,),
        side_reference_direction=SideReferenceDirection.CANONICAL_GEOMETRY,
    )

    segments = _all_nearby(_service([road]))

    assert len(segments) == 1
    assert segments[0].side is SegmentSide.LEFT


def test_geometry_length_and_phase_two_defaults_are_deterministic() -> None:
    road = RoadFeature(
        feature_id="length",
        geometry=LineStringGeometry(coordinates=[(-96.785, 32.842), (-96.785, 32.843)]),
        street_name="Length Street",
        road_type="residential",
        physical_state=PhysicalState.UNKNOWN,
    )

    segments = _all_nearby(_service([road]))

    assert geodesic_length_m(road.geometry.coordinates) == pytest.approx(111.2, abs=0.3)
    assert [segment.length_m for segment in segments] == pytest.approx([111.2, 111.2], abs=0.3)
    for segment in segments:
        assert segment.physical_state is PhysicalState.UNKNOWN
        assert segment.legal_state is LegalState.UNKNOWN
        assert segment.free_state is FreeState.UNKNOWN
        assert segment.legal_confidence == 0.0
        assert segment.evidence_refs == ["fixture-evidence"]


def test_search_constraints_apply_access_radius_and_deterministic_limit() -> None:
    nearby = _road("nearby", [(-96.7856, 32.8425), (-96.7854, 32.8425)])
    far = _road("far", [(-96.7930, 32.8465), (-96.7928, 32.8465)])
    service = _service([far, nearby])

    narrow = service.get_candidate_segments(
        _destination(), SearchConstraints(max_walk_minutes=1, max_candidates=10)
    )
    limited = service.get_candidate_segments(
        _destination(), SearchConstraints(max_walk_minutes=30, max_candidates=1)
    )

    assert len(narrow) == 2
    assert {segment.street_name for segment in narrow} == {"Street nearby"}
    assert len(limited) == 1
    assert limited[0].street_name == "Street nearby"


def test_search_radius_uses_access_points_and_free_only_is_a_gis_no_op() -> None:
    road = _road("entrance", [(-96.7856, 32.8425), (-96.7854, 32.8425)])
    destination = Destination(
        destination_id="access-point-destination",
        name="Access point destination",
        location=GeoPoint(latitude=32.8500, longitude=-96.7950),
        access_points=[
            DestinationAccessPoint(
                access_point_id="nearby-entrance",
                destination_id="access-point-destination",
                name="Nearby entrance",
                location=GeoPoint(latitude=32.8425, longitude=-96.7855),
            )
        ],
    )
    service = _service([road])

    free_only = service.get_candidate_segments(
        destination,
        SearchConstraints(free_only=True, max_walk_minutes=1, max_candidates=10),
    )
    any_payment_state = service.get_candidate_segments(
        destination,
        SearchConstraints(free_only=False, max_walk_minutes=1, max_candidates=10),
    )

    assert free_only == any_payment_state
    assert len(free_only) == 2


def test_smu_fixture_produces_target_scale_from_real_osm_roads() -> None:
    fixture = load_smu_gis_fixture()
    service = DeterministicCandidateSegmentService(
        fixture.osm.roads,
        evidence_id=fixture.osm.evidence.evidence_id,
        data_freshness=fixture.osm.observed_at,
    )

    segments = service.get_candidate_segments(
        fixture.destination,
        SearchConstraints(max_walk_minutes=30, max_candidates=300),
    )

    assert len(segments) == 284
    assert 100 <= len(segments) <= 300
    assert {segment.physical_state for segment in segments} == {PhysicalState.UNKNOWN}
    assert {segment.road_type for segment in segments} <= {
        "living_street",
        "residential",
        "secondary",
        "tertiary",
        "unclassified",
    }
