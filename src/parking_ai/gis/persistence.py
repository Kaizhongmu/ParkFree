from collections.abc import Sequence
from typing import cast

from geoalchemy2 import WKTElement
from geoalchemy2.elements import WKBElement
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from parking_ai.database.models import (
    DestinationAccessPointModel,
    DestinationModel,
    EvidenceModel,
    ParkingSegmentModel,
    parking_source_segments,
)
from parking_ai.domain import Destination, Evidence, ParkingSegment

GIS_OWNED_SEGMENT_COLUMNS = (
    "geometry",
    "street_name",
    "side",
    "length_m",
    "estimated_capacity",
    "road_type",
    "physical_state",
    "data_freshness",
)


def _point_wkt(longitude: float, latitude: float) -> WKBElement:
    return cast(WKBElement, WKTElement(f"POINT({longitude} {latitude})", srid=4326))


def _line_wkt(coordinates: Sequence[tuple[float, float]]) -> WKBElement:
    points = ", ".join(f"{longitude} {latitude}" for longitude, latitude in coordinates)
    return cast(WKBElement, WKTElement(f"LINESTRING({points})", srid=4326))


def upsert_gis_slice(
    session: Session,
    destination: Destination,
    evidence: Evidence,
    segments: Sequence[ParkingSegment],
) -> None:
    """Upsert a complete deterministic fixture without committing the caller's transaction."""

    destination_values = {
        "destination_id": destination.destination_id,
        "name": destination.name,
        "geometry": _point_wkt(destination.location.longitude, destination.location.latitude),
        "destination_type": destination.destination_type,
    }
    destination_insert = insert(DestinationModel).values(**destination_values)
    session.execute(
        destination_insert.on_conflict_do_update(
            index_elements=[DestinationModel.destination_id],
            set_={
                "name": destination_insert.excluded.name,
                "geometry": destination_insert.excluded.geometry,
                "destination_type": destination_insert.excluded.destination_type,
            },
        )
    )

    for access_point in destination.access_points:
        access_values = {
            "access_point_id": access_point.access_point_id,
            "destination_id": destination.destination_id,
            "name": access_point.name,
            "geometry": _point_wkt(access_point.location.longitude, access_point.location.latitude),
        }
        access_insert = insert(DestinationAccessPointModel).values(**access_values)
        session.execute(
            access_insert.on_conflict_do_update(
                index_elements=[DestinationAccessPointModel.access_point_id],
                set_={
                    "destination_id": access_insert.excluded.destination_id,
                    "name": access_insert.excluded.name,
                    "geometry": access_insert.excluded.geometry,
                },
            )
        )

    evidence_values = {
        "evidence_id": evidence.evidence_id,
        "source_type": evidence.source_type,
        "source_uri_or_identifier": evidence.source_uri_or_identifier,
        "publisher": evidence.publisher,
        "published_at": evidence.published_at,
        "observed_at": evidence.observed_at,
        "retrieved_at": evidence.retrieved_at,
        "raw_storage_policy": evidence.raw_storage_policy,
        "normalized_claims": [
            claim.model_dump(mode="json") for claim in evidence.normalized_claims
        ],
        "reliability_tier": evidence.reliability_tier,
        "extractor_version": evidence.extractor_version,
        "content_hash": evidence.content_hash,
    }
    evidence_insert = insert(EvidenceModel).values(**evidence_values)
    session.execute(
        evidence_insert.on_conflict_do_update(
            index_elements=[EvidenceModel.evidence_id],
            set_={
                key: getattr(evidence_insert.excluded, key)
                for key in evidence_values
                if key != "evidence_id"
            },
        )
    )

    for segment in segments:
        segment_values = {
            "segment_id": segment.segment_id,
            "geometry": _line_wkt(segment.geometry.coordinates),
            "street_name": segment.street_name,
            "side": segment.side,
            "length_m": segment.length_m,
            "estimated_capacity": segment.estimated_capacity,
            "road_type": segment.road_type,
            "physical_state": segment.physical_state,
            "legal_state": segment.legal_state,
            "free_state": segment.free_state,
            "legal_confidence": segment.legal_confidence,
            "availability_probability": segment.availability_probability,
            "availability_interval": (
                list(segment.availability_interval)
                if segment.availability_interval is not None
                else None
            ),
            "data_freshness": segment.data_freshness,
        }
        segment_insert = insert(ParkingSegmentModel).values(**segment_values)
        session.execute(
            segment_insert.on_conflict_do_update(
                index_elements=[ParkingSegmentModel.segment_id],
                set_={
                    key: getattr(segment_insert.excluded, key) for key in GIS_OWNED_SEGMENT_COLUMNS
                },
            )
        )
        association_insert = insert(parking_source_segments).values(
            evidence_id=evidence.evidence_id,
            segment_id=segment.segment_id,
        )
        session.execute(
            association_insert.on_conflict_do_nothing(
                index_elements=[
                    parking_source_segments.c.evidence_id,
                    parking_source_segments.c.segment_id,
                ]
            )
        )
