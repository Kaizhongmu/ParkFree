import datetime as dt
from typing import Protocol

from parking_ai.domain.geometry import GeoPoint
from parking_ai.domain.schemas import (
    AvailabilityContext,
    AvailabilityPrediction,
    Destination,
    LegalityEvaluation,
    ParkingSegment,
    RouteMatrix,
    SearchConstraints,
    SearchRoute,
    UserProfile,
)


class CandidateSegmentService(Protocol):
    def get_candidate_segments(
        self,
        destination: Destination,
        search_constraints: SearchConstraints,
    ) -> list[ParkingSegment]: ...


class LegalityService(Protocol):
    def evaluate_legality(
        self,
        segment: ParkingSegment,
        user_profile: UserProfile,
        datetime: dt.datetime,
    ) -> LegalityEvaluation: ...


class AvailabilityService(Protocol):
    def predict_availability(
        self,
        segment: ParkingSegment,
        context: AvailabilityContext,
    ) -> AvailabilityPrediction: ...


class SearchRoutePlanner(Protocol):
    def plan_search_route(
        self,
        candidates: list[ParkingSegment],
        origin: GeoPoint,
        destination: Destination,
        route_matrix: RouteMatrix,
    ) -> SearchRoute: ...
