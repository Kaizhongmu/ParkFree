# Contributing to ParkFree

Thanks for helping improve ParkFree. Parking recommendations can affect safety and legal
compliance, so changes should preserve the distinction between regulation evidence,
availability estimates, and route optimization.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -m "not integration"
```

For PostGIS integration tests:

```bash
cp .env.example .env
docker compose up -d db
set -a
source .env
set +a
TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/python -m pytest -m integration
```

## Pull requests

1. Keep legality, payment status, availability, and route ordering as separate contracts.
2. Preserve `UNKNOWN` when evidence is missing or contradictory.
3. Add tests for every behavior change.
4. Run Ruff, mypy, and pytest before opening a pull request.
5. Document new provider retention, attribution, timeout, and caching policies.

Please do not include secrets, private location history, copyrighted provider payloads, or
unreviewed AI output in fixtures.
