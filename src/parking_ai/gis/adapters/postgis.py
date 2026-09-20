from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from parking_ai.database.models import (
    DestinationAccessPointModel,
    DestinationModel,
    ParkingRuleModel,
    ParkingSegmentModel,
    parking_source_segments,
)
from parking_ai.domain import (
    Destination,
    DestinationAccessPoint,
    GeoPoint,
    LineStringGeometry,
    ParkingSegment,
    SearchConstraints,
)
from parking_ai.gis.generator import DEFAULT_WALKING_SPEED_M_PER_MIN, point_geometry_distance_m


def _geojson_object(value: str | Mapping[str, Any]) -> Mapping[str, Any]:
    parsed: Any = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, Mapping):
        raise ValueError("PostGIS geometry must decode to a GeoJSON object")
    return parsed


def _point_from_geojson(value: str | Mapping[str, Any]) -> GeoPoint:
    geometry = _geojson_object(value)
    if geometry.get("type") != "Point":
        raise ValueError("destination geometry must be a GeoJSON Point")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)):
        raise ValueError("GeoJSON Point coordinates must be a coordinate pair")
    if len(coordinates) < 2:
        raise ValueError("GeoJSON Point coordinates must contain longitude and latitude")
    return GeoPoint(latitude=float(coordinates[1]), longitude=float(coordinates[0]))


def _line_from_geojson(value: str | Mapping[str, Any]) -> LineStringGeometry:
    geometry = _geojson_object(value)
    return LineStringGeometry.model_validate(geometry)


class PostGISDestinationResolver:
    """Resolve persisted destinations without exposing GeoAlchemy values to the domain."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_destination(
        self,
        *,
        query: str | None,
        destination_id: str | None,
    ) -> Destination | None:
        if (query is None) == (destination_id is None):
            raise ValueError("provide exactly one of query or destination_id")

        statement = select(
            DestinationModel,
            func.ST_AsGeoJSON(DestinationModel.geometry),
        )
        if destination_id is not None:
            normalized_id = destination_id.strip()
            if not normalized_id:
                raise ValueError("destination_id must not be blank")
            statement = statement.where(DestinationModel.destination_id == normalized_id)
        else:
            normalized_query = query.strip() if query is not None else ""
            if not normalized_query:
                raise ValueError("query must not be blank")
            statement = statement.where(
                func.lower(DestinationModel.name) == normalized_query.casefold()
            )

        row = self._session.execute(
            statement.order_by(DestinationModel.destination_id).limit(1)
        ).one_or_none()
        if row is None:
            return None
        model, geometry_json = row

        access_statement = (
            select(
                DestinationAccessPointModel,
                func.ST_AsGeoJSON(DestinationAccessPointModel.geometry),
            )
            .where(DestinationAccessPointModel.destination_id == model.destination_id)
            .order_by(DestinationAccessPointModel.access_point_id)
        )
        access_points = [
            DestinationAccessPoint(
                access_point_id=access_model.access_point_id,
                destination_id=access_model.destination_id,
                name=access_model.name,
                location=_point_from_geojson(access_geometry),
            )
            for access_model, access_geometry in self._session.execute(access_statement).all()
        ]
        return Destination(
            destination_id=model.destination_id,
            name=model.name,
            location=_point_from_geojson(geometry_json),
            destination_type=model.destination_type,
            access_points=access_points,
        )


class PostGISCandidateSegmentService:
    """Read persisted segments and apply the deterministic Phase 2 candidate policy."""

    def __init__(
        self,
        session: Session,
        *,
        walking_speed_m_per_min: float = DEFAULT_WALKING_SPEED_M_PER_MIN,
    ) -> None:
        if walking_speed_m_per_min <= 0:
            raise ValueError("walking speed must be positive")
        self._session = session
        self._walking_speed_m_per_min = walking_speed_m_per_min

    def get_candidate_segments(
        self,
        destination: Destination,
        search_constraints: SearchConstraints,
    ) -> list[ParkingSegment]:
        segment_rows = self._session.execute(
            select(
                ParkingSegmentModel,
                func.ST_AsGeoJSON(ParkingSegmentModel.geometry),
            ).order_by(ParkingSegmentModel.segment_id)
        ).all()
        if not segment_rows:
            return []

        segment_ids = [model.segment_id for model, _ in segment_rows]
        rule_refs = self._reference_map(
            select(ParkingRuleModel.segment_id, ParkingRuleModel.rule_id)
            .where(ParkingRuleModel.segment_id.in_(segment_ids))
            .order_by(ParkingRuleModel.segment_id, ParkingRuleModel.rule_id)
        )
        evidence_refs = self._reference_map(
            select(
                parking_source_segments.c.segment_id,
                parking_source_segments.c.evidence_id,
            )
            .where(parking_source_segments.c.segment_id.in_(segment_ids))
            .order_by(
                parking_source_segments.c.segment_id,
                parking_source_segments.c.evidence_id,
            )
        )

        access_points = [point.location for point in destination.access_points]
        if not access_points:
            access_points = [destination.location]
        maximum_distance_m = search_constraints.max_walk_minutes * self._walking_speed_m_per_min

        ranked: list[tuple[float, ParkingSegment]] = []
        for model, geometry_json in segment_rows:
            geometry = _line_from_geojson(geometry_json)
            distance_m = min(
                point_geometry_distance_m(point, geometry.coordinates) for point in access_points
            )
            if distance_m > maximum_distance_m:
                continue
            ranked.append(
                (
                    distance_m,
                    ParkingSegment(
                        segment_id=model.segment_id,
                        geometry=geometry,
                        street_name=model.street_name,
                        side=model.side,
                        length_m=model.length_m,
                        estimated_capacity=model.estimated_capacity,
                        road_type=model.road_type,
                        physical_state=model.physical_state,
                        regulation_refs=rule_refs.get(model.segment_id, []),
                        # Contextual decisions are recomputed per search. The legacy flattened
                        # database columns must never become an implicit regulation/prediction
                        # input or leak into the request snapshot.
                        evidence_refs=evidence_refs.get(model.segment_id, []),
                        data_freshness=model.data_freshness,
                    ),
                )
            )

        ranked.sort(key=lambda item: (round(item[0], 6), item[1].segment_id))
        return [item[1] for item in ranked[: search_constraints.max_candidates]]

    def _reference_map(self, statement: Any) -> dict[str, list[str]]:
        references: defaultdict[str, list[str]] = defaultdict(list)
        for segment_id, reference_id in self._session.execute(statement).all():
            references[segment_id].append(reference_id)
        return dict(references)
