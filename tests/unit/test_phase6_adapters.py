from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from parking_ai.database.models import (
    DestinationAccessPointModel,
    DestinationModel,
    EvidenceModel,
    ParkingRuleModel,
    ParkingSegmentModel,
)
from parking_ai.domain import (
    Destination,
    DestinationAccessPoint,
    EvidenceReliabilityTier,
    EvidenceSourceType,
    EvidenceStoragePolicy,
    FreeState,
    GeoPoint,
    LegalState,
    LineStringGeometry,
    ParkingRuleType,
    ParkingSegment,
    PhysicalState,
    SearchConstraints,
    SegmentSide,
    UserProfile,
)
from parking_ai.gis.adapters.postgis import (
    PostGISCandidateSegmentService,
    PostGISDestinationResolver,
)
from parking_ai.regulations.persistence import build_regulation_engine
from parking_ai.routing import (
    FALLBACK_NODE_ID,
    ORIGIN_NODE_ID,
    LocalDeterministicRouteMatrixProvider,
    route_matrix_content_id,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def one_or_none(self) -> Any | None:
        if len(self._rows) > 1:
            raise AssertionError("fake one_or_none received multiple rows")
        return self._rows[0] if self._rows else None


class _ReadOnlySession:
    def __init__(
        self,
        *,
        execute_results: list[list[Any]] | None = None,
        scalar_results: list[list[Any]] | None = None,
    ) -> None:
        self.execute_results = list(execute_results or [])
        self.scalar_results = list(scalar_results or [])
        self.executed: list[Any] = []
        self.scalar_statements: list[Any] = []

    def execute(self, statement: Any) -> _Rows:
        self.executed.append(statement)
        return _Rows(self.execute_results.pop(0))

    def scalars(self, statement: Any) -> _Rows:
        self.scalar_statements.append(statement)
        return _Rows(self.scalar_results.pop(0))

    def add(self, _: Any) -> None:
        raise AssertionError("read adapter must not add ORM objects")

    def flush(self) -> None:
        raise AssertionError("read adapter must not flush")


def _destination() -> Destination:
    return Destination(
        destination_id="destination-a",
        name="Destination A",
        location=GeoPoint(latitude=32.842, longitude=-96.784),
    )


def _segment_model(segment_id: str) -> ParkingSegmentModel:
    return ParkingSegmentModel(
        segment_id=segment_id,
        street_name="Test Street",
        side=SegmentSide.LEFT,
        length_m=50.0,
        estimated_capacity=2.0,
        road_type="residential",
        physical_state=PhysicalState.PARKABLE,
        legal_state=LegalState.UNKNOWN,
        free_state=FreeState.UNKNOWN,
        legal_confidence=0.0,
        availability_probability=None,
        availability_interval=None,
        data_freshness=NOW,
    )


def _segment(segment_id: str, longitude: float) -> ParkingSegment:
    return ParkingSegment(
        segment_id=segment_id,
        geometry=LineStringGeometry(
            coordinates=[(longitude, 32.842), (longitude + 0.0002, 32.842)]
        ),
        street_name="Test Street",
        side=SegmentSide.LEFT,
        length_m=20.0,
        physical_state=PhysicalState.PARKABLE,
        data_freshness=NOW,
    )


def test_postgis_destination_resolver_hydrates_geojson_without_geometry_library() -> None:
    destination_model = DestinationModel(
        destination_id="destination-a",
        name="Destination A",
        destination_type="library",
    )
    access_model = DestinationAccessPointModel(
        access_point_id="entrance-a",
        destination_id="destination-a",
        name="Main entrance",
    )
    session = _ReadOnlySession(
        execute_results=[
            [(destination_model, '{"type":"Point","coordinates":[-96.784,32.842]}')],
            [(access_model, '{"type":"Point","coordinates":[-96.7839,32.8421]}')],
        ]
    )

    destination = PostGISDestinationResolver(session).resolve_destination(  # type: ignore[arg-type]
        query="destination a",
        destination_id=None,
    )

    assert destination is not None
    assert destination.location == GeoPoint(latitude=32.842, longitude=-96.784)
    assert destination.access_points[0].access_point_id == "entrance-a"
    assert destination.access_points[0].location.latitude == pytest.approx(32.8421)
    assert all("ST_AsGeoJSON" in str(statement) for statement in session.executed)


@pytest.mark.parametrize(
    ("query", "destination_id"),
    [(None, None), ("Destination", "destination-a"), ("  ", None), (None, "  ")],
)
def test_postgis_destination_resolver_requires_one_nonblank_selector(
    query: str | None,
    destination_id: str | None,
) -> None:
    with pytest.raises(ValueError):
        PostGISDestinationResolver(_ReadOnlySession()).resolve_destination(  # type: ignore[arg-type]
            query=query,
            destination_id=destination_id,
        )


def test_postgis_candidate_service_hydrates_refs_and_uses_phase_two_ordering() -> None:
    shared_geometry = '{"type":"LineString","coordinates":[[-96.784,32.842],[-96.7838,32.842]]}'
    persisted_context = _segment_model("segment-a")
    persisted_context.legal_state = LegalState.LEGAL
    persisted_context.free_state = FreeState.PAID
    persisted_context.legal_confidence = 0.99
    persisted_context.availability_probability = 0.75
    session = _ReadOnlySession(
        execute_results=[
            [
                (_segment_model("segment-b"), shared_geometry),
                (persisted_context, shared_geometry),
            ],
            [("segment-a", "rule-a")],
            [("segment-a", "evidence-a"), ("segment-b", "evidence-b")],
        ]
    )

    candidates = PostGISCandidateSegmentService(session).get_candidate_segments(  # type: ignore[arg-type]
        _destination(),
        SearchConstraints(max_walk_minutes=1, max_candidates=2),
    )

    assert [candidate.segment_id for candidate in candidates] == ["segment-a", "segment-b"]
    assert candidates[0].regulation_refs == ["rule-a"]
    assert candidates[0].evidence_refs == ["evidence-a"]
    assert candidates[0].legal_state is LegalState.UNKNOWN
    assert candidates[0].free_state is FreeState.UNKNOWN
    assert candidates[0].legal_confidence == 0.0
    assert candidates[0].availability_probability is None
    assert candidates[1].regulation_refs == []
    assert candidates[1].evidence_refs == ["evidence-b"]
    candidate_statement = session.executed[0].compile(dialect=postgresql.dialect())
    candidate_sql = str(candidate_statement)
    assert "ST_AsGeoJSON" in candidate_sql
    assert candidate_sql.count("ST_DWithin(") == 1
    assert candidate_sql.count("street_segments.geometry && ST_Expand(") == 1
    assert "CAST(street_segments.geometry AS geography" in candidate_sql
    assert candidate_statement.params["ST_MakePoint_1"] == pytest.approx(-96.784)
    assert candidate_statement.params["ST_MakePoint_2"] == pytest.approx(32.842)
    assert candidate_statement.params["ST_DWithin_1"] == pytest.approx(80.8)


def test_postgis_candidate_service_prefilters_all_access_points_before_ref_hydration() -> None:
    destination = Destination(
        destination_id="destination-a",
        name="Destination A",
        location=GeoPoint(latitude=32.842, longitude=-96.79),
        access_points=[
            DestinationAccessPoint(
                access_point_id="entrance-west",
                destination_id="destination-a",
                location=GeoPoint(latitude=32.842, longitude=-96.784),
            ),
            DestinationAccessPoint(
                access_point_id="entrance-east",
                destination_id="destination-a",
                location=GeoPoint(latitude=32.842, longitude=-96.78),
            ),
        ],
    )
    near_second_access_point = (
        '{"type":"LineString","coordinates":[[-96.78,32.842],[-96.7798,32.842]]}'
    )
    # About 80.4 m north of the first entrance: admitted by the conservative database
    # prefilter, but outside the exact 80 m Phase 2 boundary.
    outside_exact_boundary = (
        '{"type":"LineString","coordinates":[[-96.784,32.842723],[-96.7838,32.842723]]}'
    )
    session = _ReadOnlySession(
        execute_results=[
            [
                (_segment_model("segment-near-east"), near_second_access_point),
                (_segment_model("segment-outside"), outside_exact_boundary),
            ],
            [],
            [],
        ]
    )

    candidates = PostGISCandidateSegmentService(session).get_candidate_segments(  # type: ignore[arg-type]
        destination,
        SearchConstraints(max_walk_minutes=1, max_candidates=1),
    )

    assert [candidate.segment_id for candidate in candidates] == ["segment-near-east"]
    candidate_statement = session.executed[0].compile(dialect=postgresql.dialect())
    candidate_sql = str(candidate_statement)
    assert candidate_sql.count("ST_DWithin(") == 2
    assert candidate_sql.count("street_segments.geometry && ST_Expand(") == 2
    assert " OR " in candidate_sql
    assert candidate_statement.params["ST_MakePoint_1"] == pytest.approx(-96.784)
    assert candidate_statement.params["ST_MakePoint_3"] == pytest.approx(-96.78)
    assert candidate_statement.params["ST_DWithin_1"] == pytest.approx(80.8)
    assert candidate_statement.params["ST_DWithin_2"] == pytest.approx(80.8)
    for reference_statement in session.executed[1:]:
        assert reference_statement.compile().params == {"segment_id_1": ["segment-near-east"]}


def test_postgis_candidate_service_stops_after_empty_spatial_prefilter() -> None:
    session = _ReadOnlySession(execute_results=[[]])

    candidates = PostGISCandidateSegmentService(session).get_candidate_segments(  # type: ignore[arg-type]
        _destination(),
        SearchConstraints(max_walk_minutes=1, max_candidates=2),
    )

    assert candidates == []
    assert len(session.executed) == 1
    candidate_sql = str(session.executed[0].compile(dialect=postgresql.dialect()))
    assert "WHERE (street_segments.geometry && ST_Expand(" in candidate_sql
    assert ") AND ST_DWithin(" in candidate_sql


def test_regulation_factory_loads_candidate_snapshot_and_never_writes_back() -> None:
    rule_model = ParkingRuleModel(
        rule_id="rule-a",
        segment_id="segment-a",
        rule_type=ParkingRuleType.PAID,
        days=[],
        payment_required=False,
        permit_required=False,
        exceptions=[],
        source_evidence_id="evidence-a",
        extraction_confidence=0.96,
    )
    evidence_model = EvidenceModel(
        evidence_id="evidence-a",
        source_type=EvidenceSourceType.OFFICIAL_CODE,
        source_uri_or_identifier="city-code:test",
        publisher="Test City",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        normalized_claims=[],
        reliability_tier=EvidenceReliabilityTier.A,
    )
    session = _ReadOnlySession(
        scalar_results=[[rule_model], [evidence_model]],
        execute_results=[[("evidence-a", "segment-a")]],
    )

    engine = build_regulation_engine(  # type: ignore[arg-type]
        session,
        ["segment-a", "segment-a"],
        rule_engine_version="phase6-test-rules-v1",
    )
    evaluation = engine.evaluate_legality(
        _segment("segment-a", -96.784),
        UserProfile(requested_parking_duration_min=30),
        NOW,
    )

    assert evaluation.legal_state is LegalState.LEGAL
    assert evaluation.free_state is FreeState.FREE
    assert evaluation.evidence_refs == ["evidence-a"]
    assert evaluation.rule_engine_version == "phase6-test-rules-v1"
    assert len(session.scalar_statements) == 2
    assert len(session.executed) == 1


def test_regulation_factory_rejects_evidence_bound_to_a_different_segment() -> None:
    rule_model = ParkingRuleModel(
        rule_id="rule-a",
        segment_id="segment-a",
        rule_type=ParkingRuleType.PAID,
        days=[],
        payment_required=False,
        permit_required=False,
        exceptions=[],
        source_evidence_id="evidence-a",
        extraction_confidence=0.96,
    )
    evidence_model = EvidenceModel(
        evidence_id="evidence-a",
        source_type=EvidenceSourceType.OFFICIAL_CODE,
        source_uri_or_identifier="city-code:test",
        publisher="Test City",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        raw_storage_policy=EvidenceStoragePolicy.REFERENCE_ONLY,
        normalized_claims=[],
        reliability_tier=EvidenceReliabilityTier.A,
    )
    session = _ReadOnlySession(
        scalar_results=[[rule_model], [evidence_model]],
        execute_results=[[("evidence-a", "segment-other")]],
    )

    with pytest.raises(ValueError, match="not bound to their segment"):
        build_regulation_engine(  # type: ignore[arg-type]
            session,
            ["segment-a"],
            rule_engine_version="phase6-test-rules-v1",
        )


def test_local_matrix_is_complete_bound_content_addressed_and_order_independent() -> None:
    provider = LocalDeterministicRouteMatrixProvider(
        fallback_location=GeoPoint(latitude=32.842, longitude=-96.78),
        fallback_id="garage-a",
        fallback_description="Use Garage A.",
        driving_speed_m_per_min=60.0,
    )
    origin = GeoPoint(latitude=32.842, longitude=-96.785)
    candidates = [
        _segment("segment-b", -96.783),
        _segment("segment-a", -96.784),
    ]

    first = provider.build_route_matrix(origin, candidates, _destination())
    second = provider.build_route_matrix(origin, list(reversed(candidates)), _destination())

    node_ids = {ORIGIN_NODE_ID, "segment-a", "segment-b", FALLBACK_NODE_ID}
    assert set(first.travel_time_seconds) == node_ids
    assert all(set(targets) == node_ids for targets in first.travel_time_seconds.values())
    assert first.binding is not None
    assert first.binding.candidate_segment_ids == ["segment-a", "segment-b"]
    assert first.binding.fallback.fallback_id == "garage-a"
    assert first.binding.fallback.location == GeoPoint(latitude=32.842, longitude=-96.78)
    assert first.matrix_id == route_matrix_content_id(first)
    assert first == second
    assert first.travel_time_seconds[ORIGIN_NODE_ID]["segment-a"] > 0


def test_local_matrix_requires_explicit_meaningful_fallback_configuration() -> None:
    location = GeoPoint(latitude=32.842, longitude=-96.78)
    with pytest.raises(ValueError, match="fallback_id"):
        LocalDeterministicRouteMatrixProvider(
            fallback_location=location,
            fallback_id=" ",
            fallback_description="Garage",
        )
    with pytest.raises(ValueError, match="fallback_description"):
        LocalDeterministicRouteMatrixProvider(
            fallback_location=location,
            fallback_id="garage",
            fallback_description=" ",
        )
