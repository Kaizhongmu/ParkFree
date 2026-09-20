from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from parking_ai.api.routes import (
    SEARCH_CLOCK_STATE_KEY,
    SEARCH_HANDLER_STATE_KEY,
    SearchHandler,
    router,
)
from parking_ai.config import Settings, get_settings
from parking_ai.logging import configure_logging
from parking_ai.orchestrator.runtime import build_database_search_handler

_WEB_ROOT = Path(__file__).parent / "web"
_INDEX_PATH = _WEB_ROOT / "index.html"


def create_app(
    settings: Settings | None = None,
    *,
    search_handler: SearchHandler | None = None,
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
    if clock is not None:
        setattr(application.state, SEARCH_CLOCK_STATE_KEY, clock)
    application.include_router(router)
    application.mount("/assets", StaticFiles(directory=_WEB_ROOT), name="assets")

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
