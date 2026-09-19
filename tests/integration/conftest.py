import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine

from alembic import command
from parking_ai.database.session import create_database_engine, normalize_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL/PostGIS integration tests skipped")
    try:
        return normalize_database_url(url)
    except ValueError:
        pytest.fail(
            "TEST_DATABASE_URL must point to a dedicated PostgreSQL database using psycopg 3"
        )


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> Iterator[str]:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield database_url
    command.downgrade(config, "base")


@pytest.fixture
def engine(migrated_database: str) -> Iterator[Engine]:
    database_engine = create_database_engine(migrated_database)
    yield database_engine
    database_engine.dispose()
