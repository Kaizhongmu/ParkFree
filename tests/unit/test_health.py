from fastapi.testclient import TestClient

from parking_ai.config import Settings
from parking_ai.main import create_app


def test_health_endpoint() -> None:
    app = create_app(Settings(environment="test", log_level="WARNING"))

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
