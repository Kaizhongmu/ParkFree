from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Parking Intelligence System"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str | None = None
    parking_fallback_latitude: float | None = Field(default=None, ge=-90, le=90)
    parking_fallback_longitude: float | None = Field(default=None, ge=-180, le=180)
    parking_fallback_id: str = "configured-parking-fallback"
    parking_fallback_description: str = "Proceed to the configured parking fallback."
    local_driving_speed_m_per_min: float = Field(default=400.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
