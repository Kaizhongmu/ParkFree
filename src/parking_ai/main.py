from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI

from parking_ai.api.routes import (
    SEARCH_CLOCK_STATE_KEY,
    SEARCH_HANDLER_STATE_KEY,
    SearchHandler,
    router,
)
from parking_ai.config import Settings, get_settings
from parking_ai.logging import configure_logging
from parking_ai.orchestrator.runtime import build_database_search_handler


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
    return application


app = create_app()
