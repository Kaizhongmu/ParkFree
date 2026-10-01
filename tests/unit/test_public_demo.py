from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = PROJECT_ROOT / "demo"


class _AssetCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("src"):
            self.assets.append(attributes["src"] or "")
        if tag == "link" and attributes.get("href"):
            self.assets.append(attributes["href"] or "")


def test_public_demo_is_self_contained_and_accessible() -> None:
    html = (DEMO_ROOT / "index.html").read_text()
    parser = _AssetCollector()
    parser.feed(html)

    assert parser.assets == ["favicon.svg", "styles.css", "demo.js"]
    assert 'aria-live="polite"' in html
    assert 'aria-pressed="true"' in html
    assert 'role="img"' in html
    assert "Sample data" in html
    assert "illustrative fixtures" in html
    assert "Decision aid only" in html
    assert "ParkFree#quick-start-with-docker" in html
    assert "http://" not in html
    assert "<style" not in html
    assert " onclick=" not in html


def test_public_demo_assets_and_interactions_are_present() -> None:
    stylesheet = (DEMO_ROOT / "styles.css").read_text()
    script = (DEMO_ROOT / "demo.js").read_text()

    assert "@media (max-width: 560px)" in stylesheet
    assert "prefers-reduced-motion" in stylesheet
    assert "aspect-ratio: 1000 / 560" in stylesheet
    assert "linear-gradient" not in stylesheet
    assert "radial-gradient" not in stylesheet
    assert "destinationSelect.addEventListener" in script
    assert "walkRange.addEventListener" in script
    assert "freeOnly.addEventListener" in script
    assert "arriveNow.addEventListener" in script
    assert "renderCandidateList" in script
    assert "renderDetail" in script
    assert "innerHTML" not in script
    assert "eval(" not in script


def test_github_publication_workflows_exist() -> None:
    ci = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text()
    pages = (PROJECT_ROOT / ".github/workflows/pages.yml").read_text()

    assert "postgis/postgis:16-3.4" in ci
    assert "python -m pytest -m integration" in ci
    assert "actions/deploy-pages@v4" in pages
    assert "path: demo" in pages


def test_public_readme_links_beginner_getting_started_guide() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text()
    guide = (PROJECT_ROOT / "docs/GETTING_STARTED.md").read_text()

    assert "docs/GETTING_STARTED.md" in readme
    assert "## Start using ParkFree" in readme
    assert "docker compose up --build -d" in guide
    assert "Build your first parking approach" in guide
    assert "numbered candidates" in guide
    assert "Never treat a provisional road lead as permission" in guide
