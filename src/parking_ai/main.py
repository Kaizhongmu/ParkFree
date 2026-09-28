from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

from parking_ai.api.destinations import (
    DESTINATION_SEARCH_HANDLER_STATE_KEY,
    DestinationSearchHandler,
)
from parking_ai.api.destinations import (
    router as destination_router,
)
from parking_ai.api.on_demand import (
    ON_DEMAND_CLOCK_STATE_KEY,
    ON_DEMAND_HANDLER_STATE_KEY,
    OnDemandParkingHandler,
)
from parking_ai.api.on_demand import (
    router as on_demand_router,
)
from parking_ai.api.routes import (
    SEARCH_CLOCK_STATE_KEY,
    SEARCH_HANDLER_STATE_KEY,
    SearchHandler,
    router,
)
from parking_ai.config import Settings, get_settings
from parking_ai.coverage import (
    FailoverRoadCoverageProvider,
    OfflineDestinationTimezoneResolver,
    OnDemandParkingService,
    RoadCoverageProvider,
    TIGERwebRoadCoverageProvider,
)
from parking_ai.coverage.overpass import OverpassRoadCoverageProvider
from parking_ai.geocoding import NominatimGeocoder
from parking_ai.logging import configure_logging
from parking_ai.orchestrator.runtime import build_database_search_handler

_WEB_ROOT = Path(__file__).parent / "web"
_INDEX_PATH = _WEB_ROOT / "index.html"
_WEB_ASSETS = {
    "app.css": _WEB_ROOT / "app.css",
    "app.js": _WEB_ROOT / "app.js",
    "favicon.svg": _WEB_ROOT / "favicon.svg",
}


def create_app(
    settings: Settings | None = None,
    *,
    search_handler: SearchHandler | None = None,
    destination_search_handler: DestinationSearchHandler | None = None,
    on_demand_parking_handler: OnDemandParkingHandler | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    resolved_clock = clock
    resolved_handler = search_handler or build_database_search_handler(
        resolved_settings,
        clock=resolved_clock,
    )

    application = FastAPI(title=resolved_settings.app_name, version="0.1.0")
    setattr(application.state, SEARCH_HANDLER_STATE_KEY, resolved_handler)
    resolved_destination_handler = destination_search_handler
    geocoder: NominatimGeocoder | None = None
    if resolved_settings.nominatim_user_agent is not None:
        geocoder = NominatimGeocoder(
            user_agent=resolved_settings.nominatim_user_agent,
            search_url=resolved_settings.nominatim_search_url,
            timeout_seconds=resolved_settings.nominatim_timeout_seconds,
            cache_ttl_seconds=resolved_settings.nominatim_cache_ttl_seconds,
        )
    if resolved_destination_handler is None and geocoder is not None:
        resolved_destination_handler = geocoder.geocode
    setattr(
        application.state,
        DESTINATION_SEARCH_HANDLER_STATE_KEY,
        resolved_destination_handler,
    )
    resolved_on_demand_handler = on_demand_parking_handler
    if resolved_on_demand_handler is None and geocoder is not None:
        tigerweb_provider = TIGERwebRoadCoverageProvider(
            endpoint=resolved_settings.tigerweb_url,
            timeout_seconds=resolved_settings.tigerweb_timeout_seconds,
            cache_ttl_seconds=resolved_settings.tigerweb_cache_ttl_seconds,
        )
        road_provider: RoadCoverageProvider = tigerweb_provider
        if resolved_settings.overpass_user_agent is not None:
            overpass_provider = OverpassRoadCoverageProvider(
                user_agent=resolved_settings.overpass_user_agent,
                endpoint=resolved_settings.overpass_url,
                timeout_seconds=resolved_settings.overpass_timeout_seconds,
                cache_ttl_seconds=resolved_settings.overpass_cache_ttl_seconds,
            )
            road_provider = FailoverRoadCoverageProvider(
                overpass_provider,
                tigerweb_provider,
                primary_name="Overpass API / OpenStreetMap",
                fallback_name="U.S. Census Bureau TIGERweb",
            )
        resolved_on_demand_handler = OnDemandParkingService(
            geocoder,
            road_provider,
            timezone_resolver=OfflineDestinationTimezoneResolver(),
            prediction_clock=resolved_clock,
        ).search
    setattr(
        application.state,
        ON_DEMAND_HANDLER_STATE_KEY,
        resolved_on_demand_handler,
    )
    if clock is not None:
        setattr(application.state, SEARCH_CLOCK_STATE_KEY, clock)
        setattr(application.state, ON_DEMAND_CLOCK_STATE_KEY, clock)
    application.include_router(router)
    application.include_router(destination_router)
    application.include_router(on_demand_router)

    @application.get(
        "/assets/{asset_name}",
        include_in_schema=False,
        response_class=FileResponse,
    )
    def web_asset(asset_name: str) -> FileResponse:
        """Serve only the three package assets referenced by the secured HTML entrypoint."""

        asset_path = _WEB_ASSETS.get(asset_name)
        if asset_path is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(asset_path, headers={"X-Content-Type-Options": "nosniff"})

    @application.get("/", include_in_schema=False, response_class=FileResponse)
    def parking_map() -> FileResponse:
        """Serve the dependency-free Phase 7 map client."""

        return FileResponse(
            _INDEX_PATH,
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; base-uri 'none'; connect-src 'self'; "
                    "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
                    "img-src 'self' data:; object-src 'none'; script-src 'self'; "
                    "style-src 'self'"
                ),
                "Permissions-Policy": "camera=(), geolocation=(self), microphone=()",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )

    return application


app = create_app()
