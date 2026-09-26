from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
    nominatim_user_agent: str | None = Field(default=None, min_length=8, max_length=255)
    nominatim_search_url: str = Field(
        default="https://nominatim.openstreetmap.org/search",
        min_length=1,
        max_length=2_048,
    )
    nominatim_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
    nominatim_cache_ttl_seconds: float = Field(default=3_600.0, gt=0, le=86_400)

    @field_validator("nominatim_user_agent")
    @classmethod
    def validate_nominatim_user_agent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            len(normalized) < 8
            or "/" not in normalized
            or any(character in normalized for character in "\r\n")
        ):
            raise ValueError("nominatim_user_agent must be an identifying single-line value")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
