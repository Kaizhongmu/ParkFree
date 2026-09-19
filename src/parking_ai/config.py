from functools import lru_cache
from typing import Literal

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
