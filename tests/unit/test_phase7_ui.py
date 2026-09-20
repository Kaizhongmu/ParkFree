from importlib.resources import files

from fastapi.testclient import TestClient

from parking_ai.config import Settings
from parking_ai.main import create_app


def _client() -> TestClient:
    return TestClient(create_app(Settings(environment="test", log_level="CRITICAL")))


def test_home_serves_dependency_free_accessible_map_ui() -> None:
    response = _client().get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["permissions-policy"] == (
        "camera=(), geolocation=(self), microphone=()"
    )
    assert response.headers["x-frame-options"] == "DENY"
    assert 'id="search-form"' in response.text
    assert 'id="parking-map"' in response.text
    assert 'aria-live="polite"' in response.text
    assert "Unknown · verify signs" in response.text
    assert "Guaranteed fallback" not in response.text
    assert "https://" not in response.text
    assert "http://" not in response.text
    assert "<style" not in response.text
    assert " style=" not in response.text
    assert " onclick=" not in response.text


def test_ui_assets_are_local_and_have_expected_types() -> None:
    client = _client()

    stylesheet = client.get("/assets/app.css")
    script = client.get("/assets/app.js")
    favicon = client.get("/assets/favicon.svg")

    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "@media (max-width: 600px)" in stylesheet.text
    assert "prefers-reduced-motion" in stylesheet.text
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert 'fetch("/v1/parking/search"' in script.text
    assert "candidate_decisions" in script.text
    assert "evidence_refs" in script.text
    assert "unknown_segment_ids" in script.text
    assert "result.fallback" in script.text
    assert "lastRequestBody" in script.text
    assert "lastRequestBody = null" in script.text
    assert "clearResults()" in script.text
    assert "markSearchFailed()" in script.text
    assert 'setAttribute("aria-busy"' in script.text
    assert "route-guide" in script.text
    assert "validCoordinate" in script.text
    assert "setMapEmptyVisibility(false)" in script.text
    assert 'setAttribute("aria-pressed"' in script.text
    assert "decision.exclusion_reason" in script.text
    assert "decision.legality.confidence" in script.text
    assert "decision.legality.evaluated_at" in script.text
    assert "https://" not in script.text
    assert 'fetch("http' not in script.text
    assert "import(" not in script.text
    assert "innerHTML" not in script.text
    assert "insertAdjacentHTML" not in script.text
    assert "eval(" not in script.text
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")


def test_static_mount_rejects_missing_and_parent_paths() -> None:
    client = _client()

    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/assets/../main.py").status_code == 404


def test_static_resources_are_in_the_installable_package() -> None:
    package = files("parking_ai")

    assert package.joinpath("web/index.html").is_file()
    assert package.joinpath("web/app.css").is_file()
    assert package.joinpath("web/app.js").is_file()
    assert package.joinpath("web/favicon.svg").is_file()


def test_ui_mount_does_not_change_existing_endpoints() -> None:
    client = _client()

    health = client.get("/health")
    search = client.post(
        "/v1/parking/search",
        json={
            "origin": {"lat": 32.842, "lon": -96.784},
            "destination": {"query": "Fondren Library"},
            "arrival_time": "now",
            "parking_duration_minutes": 60,
        },
    )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert search.status_code == 503
