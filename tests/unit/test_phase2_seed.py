import pytest

import parking_ai.gis.seed as seed_module


class _SettingsWithoutDatabase:
    database_url = None


def test_seed_cli_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seed_module, "Settings", _SettingsWithoutDatabase)

    with pytest.raises(SystemExit, match="DATABASE_URL is required"):
        seed_module.main()
