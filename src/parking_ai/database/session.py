from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_database_url(database_url: str) -> str:
    """Require PostgreSQL and select the installed psycopg 3 SQLAlchemy driver."""

    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    raise ValueError("DATABASE_URL must use postgresql+psycopg:// or postgresql://")


def create_database_engine(database_url: str, *, echo: bool = False) -> Engine:
    normalized_url = normalize_database_url(database_url)
    return create_engine(normalized_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session, session.begin():
        yield session
