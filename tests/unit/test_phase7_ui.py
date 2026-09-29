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
    assert response.headers["cache-control"] == "no-store"
    assert 'id="search-form" method="post" action="/v1/parking/on-demand"' in response.text
    assert 'id="parking-map" viewBox="0 0 900 580" role="group"' in response.text
    assert 'id="runtime-notice" class="runtime-notice"' in response.text
    assert "do not open this HTML file directly" in response.text
    assert 'href="/">Reload the live ParkFree website.</a>' in response.text
    assert 'href="/assets/app.css?v=20260929-1"' in response.text
    assert 'src="/assets/app.js?v=20260929-1"' in response.text
    assert (
        'id="destination" name="destination" type="text" value="" '
        'placeholder="Enter any US destination"' in response.text
    )
    assert 'id="destination-search-button" type="button"' in response.text
    assert 'id="use-demo-destination-button"' not in response.text
    assert 'id="destination-match-list" class="destination-match-list"' in response.text
    assert "Finding a place does not mean" in response.text
    assert "Fast mode uses official road geometry" in response.text
    assert "also tries live OpenStreetMap road and parking tags" in response.text
    assert "not a guarantee of higher accuracy" in response.text
    assert 'id="instant-submit-button" type="submit" data-research-mode="INSTANT"' in response.text
    assert 'id="submit-button" type="submit" data-research-mode="RESEARCH"' in response.text
    assert "conditional vacancy chance" in response.text
    assert "Provisional road leads — not a parking route." in response.text
    assert "Verified legal/free curbs: 0" in response.text
    assert 'id="provisional-legend"' in response.text
    assert 'id="research-activity"' in response.text
    assert "Request-time API research" in response.text
    assert "in this device's timezone" in response.text
    assert 'id="destination-discovery" class="destination-discovery">' in response.text
    assert (
        'id="destination-search-status" class="destination-search-status" role="status"'
        in response.text
    )
    assert 'aria-live="polite"' in response.text
    assert 'id="location-help" role="status" aria-live="polite" aria-atomic="true"' in response.text
    assert 'aria-describedby="location-help"' in response.text
    assert (
        'id="request-status" class="request-status" role="status" '
        'aria-live="polite" aria-atomic="true"' in response.text
    )
    assert 'id="permits" name="permits" type="text" maxlength="8192"' in response.text
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
    assert stylesheet.headers["cache-control"] == "no-store"
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "@media (max-width: 600px)" in stylesheet.text
    assert "prefers-reduced-motion" in stylesheet.text
    assert "aspect-ratio: 900 / 580" in stylesheet.text
    assert "stroke-width: 24" in stylesheet.text
    assert "vector-effect: non-scaling-stroke" in stylesheet.text
    assert "#parking-map { min-width: 0; }" in stylesheet.text
    assert "#parking-map { min-width: 48rem; }" not in stylesheet.text
    assert ".coverage-status-banner" in stylesheet.text
    assert ".route-marker, .route-number" in stylesheet.text
    assert "pointer-events: none" in stylesheet.text
    assert (
        ".map-segment-group:focus-visible .map-segment-hit { stroke: var(--brand-dark); }"
        in stylesheet.text
    )
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-store"
    assert "javascript" in script.headers["content-type"]
    assert 'fetch("/v1/parking/search"' not in script.text
    assert 'fetch("/v1/destinations/search"' in script.text
    assert 'fetch("/v1/parking/on-demand"' in script.text
    assert 'method: "POST"' in script.text
    assert "body: JSON.stringify({ query })" in script.text
    assert "LOCAL_DEMO_DESTINATION" not in script.text
    assert "LOCAL_DEMO_DESTINATION_ID" not in script.text
    assert 'event.submitter?.dataset.researchMode === "RESEARCH"' in script.text
    assert "await runOnDemandParking(requestedMode)" in script.text
    assert "research_mode: researchMode" in script.text
    assert "renderOnDemandCoverage(body, researchMode)" in script.text
    assert "result.research_mode !== requestedMode" in script.text
    assert 'runOnDemandParking("INSTANT")' not in script.text
    assert 'runOnDemandParking("RESEARCH")' not in script.text
    assert "Calling enhanced road and parking-tag APIs before estimating" in script.text
    assert "Rules and free status remain UNKNOWN" in script.text
    assert "availability_predictions" in script.text
    assert "Conditional vacancy chance" in script.text
    assert "destination_timezone" in script.text
    assert "calibration_status" in script.text
    assert "Uncalibrated heuristic prior" in script.text
    assert "proximity order" in script.text
    assert "ranked them by conditional availability" not in script.text
    assert "setProvisionalPresentation" in script.text
    assert "renderResearchActivity" in script.text
    assert "Primary road source" in script.text
    assert "Fallback road source" in script.text
    assert "Road sources failed" in script.text
    assert "No roads returned" in script.text
    assert '"NOT_CONFIGURED"' in script.text
    assert '"NOT_REQUESTED"' in script.text
    assert '"DEGRADED"' in script.text
    assert "every configured road API failed" in script.text
    assert "The fast official-road source failed" in script.text
    assert 'attempt.role === "FALLBACK" && attempt.outcome === "SUCCEEDED"' in script.text
    assert "form.checkValidity()" in script.text
    assert "form.reportValidity()) void runOnDemandParking" not in script.text
    assert 'element("li", "", String(warning))' in script.text
    assert "renderDiscoveredDestination" in script.text
    assert "activeDestinationSearch" in script.text
    assert "staleSearch.controller.abort()" in script.text
    assert "hasCurrentPlan" in script.text
    assert "candidate_decisions" in script.text
    assert "runtimeNotice.hidden = true" in script.text
    assert 'class: "map-segment-group"' in script.text
    assert 'class: "map-segment-hit"' in script.text
    assert "offsetProjectedPoints" in script.text
    assert "centerMapViewport" in script.text
    assert (
        "centerMapViewport();\nshowDestinationCoverageGate();\nruntimeNotice.hidden = true;"
        in script.text
    )
    assert "evidence_refs" in script.text
    assert "unknown_segment_ids" in script.text
    assert "result.fallback" in script.text
    assert "lastRequestBody" not in script.text
    assert "new AbortController()" in script.text
    assert "activeSearch !== search" in script.text
    assert "invalidatePendingSearch" in script.text
    assert 'form.addEventListener("input"' in script.text
    assert "staleSearch.controller.abort()" in script.text
    assert "Search settings changed. Submit again" in script.text
    assert "clearResults()" in script.text
    assert "markSearchFailed()" in script.text
    assert "markNoCandidates()" in script.text
    assert "Search complete · no candidates" in script.text
    assert 'setAttribute("aria-busy"' in script.text
    assert 'setAttribute("aria-atomic", "true")' in script.text
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
    assert favicon.headers["cache-control"] == "no-store"
    assert favicon.headers["content-type"].startswith("image/svg+xml")


def test_all_http_page_entries_serve_the_same_current_ui() -> None:
    client = _client()

    root = client.get("/")
    cache_busted = client.get("/?demo=8")
    named_entry = client.get("/index.html")

    assert root.status_code == cache_busted.status_code == named_entry.status_code == 200
    assert root.content == cache_busted.content == named_entry.content
    assert root.headers["cache-control"] == "no-store"
    assert cache_busted.headers["cache-control"] == "no-store"
    assert named_entry.headers["cache-control"] == "no-store"


def test_static_mount_rejects_missing_and_parent_paths() -> None:
    client = _client()

    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/assets/../main.py").status_code == 404
    assert client.get("/assets/index.html").status_code == 404


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


def test_public_api_responses_are_not_cacheable_and_production_hides_schema_ui() -> None:
    client = _client()

    response = client.post(
        "/v1/destinations/search",
        json={"query": "Seattle Center"},
    )

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"

    production = TestClient(create_app(Settings(environment="production", log_level="CRITICAL")))
    assert production.get("/docs").status_code == 404
    assert production.get("/redoc").status_code == 404
    assert production.get("/openapi.json").status_code == 404
