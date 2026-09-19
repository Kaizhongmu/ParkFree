from fastapi import FastAPI

from parking_ai.api.routes import router
from parking_ai.config import Settings, get_settings
from parking_ai.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    application = FastAPI(title=resolved_settings.app_name, version="0.1.0")
    application.include_router(router)
    return application


app = create_app()
