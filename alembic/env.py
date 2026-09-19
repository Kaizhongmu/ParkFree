import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from parking_ai.database import models  # noqa: F401
from parking_ai.database.base import Base
from parking_ai.database.session import normalize_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL")
if database_url:
    normalized_database_url = normalize_database_url(database_url)
    config.set_main_option("sqlalchemy.url", normalized_database_url.replace("%", "%%"))

target_metadata = Base.metadata


def _require_database_url() -> None:
    if not config.get_main_option("sqlalchemy.url"):
        raise RuntimeError("DATABASE_URL must be set before running Alembic")


def run_migrations_offline() -> None:
    _require_database_url()
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _require_database_url()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
