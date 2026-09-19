import pytest

from parking_ai.database.session import create_database_engine, normalize_database_url


def test_bare_postgresql_url_is_normalized_to_psycopg_three() -> None:
    bare_url = "postgresql://user:password@localhost:5432/parking"

    assert normalize_database_url(bare_url) == (
        "postgresql+psycopg://user:password@localhost:5432/parking"
    )

    engine = create_database_engine(bare_url)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
    finally:
        engine.dispose()


def test_explicit_psycopg_three_url_is_unchanged() -> None:
    database_url = "postgresql+psycopg://user:password@localhost:5432/parking"

    assert normalize_database_url(database_url) == database_url


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg2://user:password@localhost:5432/parking",
        "sqlite:///parking.db",
    ],
)
def test_non_psycopg_three_database_url_is_rejected(database_url: str) -> None:
    with pytest.raises(ValueError, match=r"postgresql\+psycopg"):
        normalize_database_url(database_url)
