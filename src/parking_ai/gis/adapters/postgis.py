from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import and_, cast, func, or_, select
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

# PostGIS geography and the deterministic local projection use slightly different earth models.
# Keep the database predicate a conservative prefilter, then apply the existing exact Phase 2
# distance boundary below. The maximum supported search radius is small enough that one percent is
# comfortably conservative without materially widening the rows hydrated from PostGIS.
_SPATIAL_PREFILTER_SAFETY_FACTOR = 1.01
_CONSERVATIVE_METRES_PER_DEGREE = 110_000.0


def _spatial_padding_degrees(point: GeoPoint, distance_m: float) -> tuple[float, float]:
    """Return conservative longitude/latitude padding for an indexable WGS84 bounding box."""

    latitude_padding = distance_m / _CONSERVATIVE_METRES_PER_DEGREE
    furthest_absolute_latitude = min(90.0, abs(point.latitude) + latitude_padding)
    longitude_scale = math.cos(math.radians(furthest_absolute_latitude))
    if longitude_scale <= 0:
        return 180.0, latitude_padding
    longitude_padding = min(
        180.0,
        distance_m / (_CONSERVATIVE_METRES_PER_DEGREE * longitude_scale),
    )
    return longitude_padding, latitude_padding


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
        access_points = [point.location for point in destination.access_points]
        if not access_points:
            access_points = [destination.location]
        maximum_distance_m = search_constraints.max_walk_minutes * self._walking_speed_m_per_min
        prefilter_distance_m = maximum_distance_m * _SPATIAL_PREFILTER_SAFETY_FACTOR
        segment_geography = cast(ParkingSegmentModel.geometry, Geography(srid=4326))
        spatial_predicates = []
        for point in access_points:
            point_geometry = func.ST_SetSRID(
                func.ST_MakePoint(point.longitude, point.latitude),
                4326,
            )
            longitude_padding, latitude_padding = _spatial_padding_degrees(
                point,
                prefilter_distance_m,
            )
            spatial_predicates.append(
                and_(
                    ParkingSegmentModel.geometry.op("&&")(
                        func.ST_Expand(
                            point_geometry,
                            longitude_padding,
                            latitude_padding,
                        )
                    ),
                    func.ST_DWithin(
                        segment_geography,
                        cast(point_geometry, Geography(srid=4326)),
                        prefilter_distance_m,
                    ),
                )
            )
        segment_rows = self._session.execute(
            select(
                ParkingSegmentModel,
                func.ST_AsGeoJSON(ParkingSegmentModel.geometry),
            )
            .where(or_(*spatial_predicates))
            .order_by(ParkingSegmentModel.segment_id)
        ).all()
        if not segment_rows:
            return []

        ranked_rows: list[tuple[float, ParkingSegmentModel, LineStringGeometry]] = []
        for model, geometry_json in segment_rows:
            geometry = _line_from_geojson(geometry_json)
            distance_m = min(
                point_geometry_distance_m(point, geometry.coordinates) for point in access_points
            )
            if distance_m <= maximum_distance_m:
                ranked_rows.append((distance_m, model, geometry))
        ranked_rows.sort(key=lambda item: (round(item[0], 6), item[1].segment_id))
        ranked_rows = ranked_rows[: search_constraints.max_candidates]
        if not ranked_rows:
            return []

        segment_ids = [model.segment_id for _, model, _ in ranked_rows]
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

        return [
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
                # Contextual decisions are recomputed per search. The legacy flattened database
                # columns must never become an implicit regulation/prediction input or leak into
                # the request snapshot.
                evidence_refs=evidence_refs.get(model.segment_id, []),
                data_freshness=model.data_freshness,
            )
            for _, model, geometry in ranked_rows
        ]

    def _reference_map(self, statement: Any) -> dict[str, list[str]]:
        references: defaultdict[str, list[str]] = defaultdict(list)
        for segment_id, reference_id in self._session.execute(statement).all():
            references[segment_id].append(reference_id)
        return dict(references)
