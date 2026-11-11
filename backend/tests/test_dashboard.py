"""Static dashboard at /ui: served by the backend, self-contained, and /ui/config exposes nothing secret."""

import re

from fastapi.testclient import TestClient

from app.container import Container
from app.main import create_app
from app.sim import Harness


def client(settings, **overrides) -> TestClient:
    s = settings.model_copy(update=overrides)
    h = Harness(s)
    return TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False)))


def test_ui_serves_html(settings):
    r = client(settings).get("/ui")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>" in r.text


def test_ui_assets_load(settings):
    c = client(settings)
    html = c.get("/ui").text
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs, "page references its css/js"
    for ref in (r for r in refs if not r.startswith("#")):
        assert c.get(ref).status_code == 200, ref


def test_ui_references_only_local_assets(settings):
    html = client(settings).get("/ui").text
    for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert ref.startswith("/ui/") or ref.startswith("#"), f"external asset: {ref}"
    assert "http://" not in html and "https://" not in html


def test_ui_config_returns_rocketlane_app_url_and_nothing_secret(settings):
    c = client(settings, rocketlane_app_url="https://acme.rocketlane.com/", rocketlane_api_key="rl-secret-key",
               anthropic_api_key="sk-ant-secret", retell_api_key="retell-secret", slack_bot_token="xoxb-secret")
    r = c.get("/ui/config")
    assert r.status_code == 200
    assert r.json() == {"rocketlane_app_url": "https://acme.rocketlane.com"}
    for secret in ("rl-secret-key", "sk-ant-secret", "retell-secret", "xoxb-secret"):
        assert secret not in r.text


def test_ui_config_empty_when_unset(settings):
    assert client(settings).get("/ui/config").json() == {"rocketlane_app_url": ""}


def test_ui_never_renders_api_data_as_html(settings):
    """Emails and transcripts are untrusted: the dashboard builds DOM with textContent only."""
    c = client(settings)
    js = c.get("/ui/assets/app.js").text
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js, sink


def test_html_pages_are_revalidated_so_asset_version_bumps_reach_browsers(settings):
    c = client(settings)
    for path in ("/ui", "/ui/login"):
        assert c.get(path).headers.get("cache-control") == "no-cache", path


def test_sidebar_has_operations_and_observability_pages(settings):
    html = client(settings).get("/ui").text
    for route in ("#/overview", "#/runs", "#/escalations", "#/ai", "#/tools"):
        assert f'href="{route}"' in html, route
    assert "Operations" in html and "Observability" in html


def test_observability_pages_use_the_paged_endpoints(settings):
    c = client(settings)
    js = c.get("/ui/assets/app.js").text
    for path in ("/metrics/overview", "/metrics/llm", "/metrics/tools", "/llm-calls?", "/tool-calls?", "/tool-calls/${"):
        assert path in js, path
    for path in ("/metrics/overview", "/metrics/llm", "/metrics/tools", "/llm-calls", "/tool-calls"):
        assert c.get(path).status_code == 200, path


def test_asset_cache_buster_is_shared_by_css_and_js(settings):
    html = client(settings).get("/ui").text
    versions = set(re.findall(r'/ui/assets/app\.(?:css|js)\?v=([\w.-]+)', html))
    assert len(versions) == 1 and versions != {"10b"}
