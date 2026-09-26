from datetime import UTC, datetime

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from parking_ai.database.models import ParkingSegmentModel
from parking_ai.domain import (
    Destination,
    DestinationAccessPoint,
    FreeState,
    GeoPoint,
    LegalState,
    PhysicalState,
    SearchConstraints,
    SegmentSide,
)
from parking_ai.gis.adapters.postgis import PostGISCandidateSegmentService

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


def _segment(segment_id: str, line_wkt: str) -> ParkingSegmentModel:
    return ParkingSegmentModel(
        segment_id=segment_id,
        geometry=WKTElement(line_wkt, srid=4326),
        street_name="Spatial Prefilter Test Street",
        side=SegmentSide.LEFT,
        length_m=20.0,
        road_type="residential",
        physical_state=PhysicalState.PARKABLE,
        legal_state=LegalState.UNKNOWN,
        free_state=FreeState.UNKNOWN,
        legal_confidence=0.0,
        data_freshness=NOW,
    )


def test_postgis_candidate_prefilter_uses_each_access_point_and_exact_bounds(
    engine: Engine,
) -> None:
    destination = Destination(
        destination_id="spatial-prefilter-destination",
        name="Spatial Prefilter Destination",
        # The centroid is deliberately outside the one-minute radius of both entrances.
        location=GeoPoint(latitude=40.0, longitude=-100.0),
        access_points=[
            DestinationAccessPoint(
                access_point_id="spatial-prefilter-west",
                destination_id="spatial-prefilter-destination",
                location=GeoPoint(latitude=40.0, longitude=-100.01),
            ),
            DestinationAccessPoint(
                access_point_id="spatial-prefilter-east",
                destination_id="spatial-prefilter-destination",
                location=GeoPoint(latitude=40.0, longitude=-99.99),
            ),
        ],
    )
    segments = [
        _segment(
            "spatial-prefilter-a-west",
            "LINESTRING(-100.01 40.0, -100.0099 40.0)",
        ),
        _segment(
            "spatial-prefilter-b-east",
            "LINESTRING(-99.99 40.0, -99.9899 40.0)",
        ),
        _segment(
            "spatial-prefilter-c-centroid",
            "LINESTRING(-100.0 40.0, -99.9999 40.0)",
        ),
        _segment(
            "spatial-prefilter-z-outside",
            "LINESTRING(-100.01 40.002, -100.0099 40.002)",
        ),
    ]

    with Session(engine) as session:
        transaction = session.begin()
        session.add_all(segments)
        session.flush()
        service = PostGISCandidateSegmentService(session)

        all_candidates = service.get_candidate_segments(
            destination,
            SearchConstraints(max_walk_minutes=1, max_candidates=10),
        )
        limited_candidates = service.get_candidate_segments(
            destination,
            SearchConstraints(max_walk_minutes=1, max_candidates=1),
        )
        centroid_candidates = service.get_candidate_segments(
            destination.model_copy(update={"access_points": []}),
            SearchConstraints(max_walk_minutes=1, max_candidates=10),
        )

        assert [candidate.segment_id for candidate in all_candidates] == [
            "spatial-prefilter-a-west",
            "spatial-prefilter-b-east",
        ]
        assert [candidate.segment_id for candidate in limited_candidates] == [
            "spatial-prefilter-a-west"
        ]
        assert [candidate.segment_id for candidate in centroid_candidates] == [
            "spatial-prefilter-c-centroid"
        ]
        transaction.rollback()
