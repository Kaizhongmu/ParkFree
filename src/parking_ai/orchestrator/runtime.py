from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from parking_ai.availability import MODEL_VERSION, DeterministicAvailabilityBaseline
from parking_ai.config import Settings
from parking_ai.database.session import create_database_engine, create_session_factory
from parking_ai.domain import GeoPoint
from parking_ai.gis import PostGISCandidateSegmentService, PostGISDestinationResolver
from parking_ai.orchestrator.persistence import SQLAlchemySearchSessionRepository
from parking_ai.orchestrator.schemas import ParkingSearchCommand, ParkingSearchResponse
from parking_ai.orchestrator.search import ParkingSearchOrchestrator, SearchConfigurationError
from parking_ai.regulations import RULE_ENGINE_VERSION, build_regulation_engine
from parking_ai.routing import LocalDeterministicRouteMatrixProvider

SearchHandler = Callable[[ParkingSearchCommand, str | None], ParkingSearchResponse]
Clock = Callable[[], dt.datetime]


def build_database_search_handler(
    settings: Settings,
    *,
    clock: Clock | None = None,
) -> SearchHandler | None:
    """Build the local Phase 6 application handler when database/fallback config is complete."""

    if settings.database_url is None:
        return None
    fallback = _fallback_location(settings)
    if fallback is None:
        return _unconfigured_fallback_handler
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    resolved_clock = clock or (lambda: dt.datetime.now(dt.UTC))

    def handle(command: ParkingSearchCommand, idempotency_key: str | None) -> ParkingSearchResponse:
        try:
            return _execute_search(
                session_factory,
                settings,
                fallback,
                resolved_clock,
                command,
                idempotency_key,
            )
        except IntegrityError as error:
            # A concurrent request may win the idempotency-key uniqueness race. A single retry
            # reads and returns that committed replay; any other integrity failure remains closed.
            if idempotency_key is None:
                raise SearchConfigurationError("search persistence failed") from error
            try:
                return _execute_search(
                    session_factory,
                    settings,
                    fallback,
                    resolved_clock,
                    command,
                    idempotency_key,
                )
            except SQLAlchemyError as retry_error:
                raise SearchConfigurationError("search persistence failed") from retry_error
        except SQLAlchemyError as error:
            raise SearchConfigurationError("search database is unavailable") from error

    return handle


def _execute_search(
    session_factory: sessionmaker[Session],
    settings: Settings,
    fallback: GeoPoint,
    clock: Clock,
    command: ParkingSearchCommand,
    idempotency_key: str | None,
) -> ParkingSearchResponse:
    with session_factory() as session, session.begin():
        matrix_provider = LocalDeterministicRouteMatrixProvider(
            fallback_location=fallback,
            fallback_id=settings.parking_fallback_id,
            fallback_description=settings.parking_fallback_description,
            driving_speed_m_per_min=settings.local_driving_speed_m_per_min,
        )
        availability = DeterministicAvailabilityBaseline(clock=clock)
        orchestrator = ParkingSearchOrchestrator(
            destination_resolver=PostGISDestinationResolver(session),
            candidate_service=PostGISCandidateSegmentService(session),
            regulation_service_factory=lambda segment_ids: build_regulation_engine(
                session,
                segment_ids,
                rule_engine_version=RULE_ENGINE_VERSION,
            ),
            availability_service=availability,
            route_matrix_provider=matrix_provider,
            repository=SQLAlchemySearchSessionRepository(session),
            rule_engine_version=RULE_ENGINE_VERSION,
            availability_model_version=MODEL_VERSION,
            clock=clock,
            warnings=(
                "LOCAL_STRAIGHT_LINE_ROUTE_COST_APPROXIMATION",
                "SMU_FIXTURE_LIMITED_COVERAGE",
            ),
        )
        return orchestrator.search(command, idempotency_key)


def _fallback_location(settings: Settings) -> GeoPoint | None:
    latitude = settings.parking_fallback_latitude
    longitude = settings.parking_fallback_longitude
    if latitude is None and longitude is None:
        return None
    if latitude is None or longitude is None:
        return None
    return GeoPoint(latitude=latitude, longitude=longitude)


def _unconfigured_fallback_handler(
    _command: ParkingSearchCommand,
    _idempotency_key: str | None,
) -> ParkingSearchResponse:
    raise SearchConfigurationError("a guaranteed parking fallback is not configured")


__all__ = ["build_database_search_handler"]
