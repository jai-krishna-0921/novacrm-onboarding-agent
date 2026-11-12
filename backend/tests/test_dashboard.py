"""The React dashboard (web/dist) at /ui: SPA fallback, asset caching, login redirects, missing build, /ui/config.
Served from the fixture build in tests/fixtures/web_dist (settings.web_dist_dir), never from a real npm build."""

import re

import pytest
from fastapi.testclient import TestClient
from test_auth import ORIGIN, login, make

from app.container import Container
from app.main import create_app
from app.sim import Harness

INDEX_MARK = '<div id="root"></div>'
PAGES = ["/ui", "/ui/", "/ui/overview", "/ui/runs", "/ui/runs/abc-123", "/ui/escalations/r1", "/ui/aes",
         "/ui/migration", "/ui/ai", "/ui/tools", "/ui/agents"]


def client(settings, **overrides) -> TestClient:
    s = settings.model_copy(update=overrides)
    h = Harness(s)
    return TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False)),
                      follow_redirects=False)


def signed_in(settings, **overrides) -> TestClient:
    c = make(settings, **overrides)
    assert login(c).status_code == 200
    return c


# ---------- SPA fallback ----------
@pytest.mark.parametrize("path", PAGES)
def test_every_dashboard_route_serves_the_spa_index_revalidated(settings, path):
    r = signed_in(settings).get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), path
    assert INDEX_MARK in r.text
    assert r.headers["cache-control"] == "no-cache"  # a new build's asset names reach browsers at once


def test_login_page_is_the_spa_index_and_public(settings):
    r = make(settings).get("/ui/login?next=/ui/runs")
    assert r.status_code == 200 and INDEX_MARK in r.text and r.headers["cache-control"] == "no-cache"


# ---------- assets ----------
def test_assets_are_public_and_cached_for_a_year(settings):
    c = make(settings)  # no session
    for ref in ("/ui/assets/index-test.js", "/ui/assets/favicon-test.svg"):
        r = c.get(ref)
        assert r.status_code == 200, ref
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable", ref


def test_index_references_only_local_assets_that_resolve(settings):
    c = signed_in(settings)
    html = c.get("/ui").text
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs
    for ref in refs:
        assert ref.startswith("/ui/assets/"), ref
        assert c.get(ref).status_code == 200, ref


@pytest.mark.parametrize("path", ["/ui/assets/nope.js", "/ui/assets/",
                                  "/ui/assets/%2e%2e/index.html", "/ui/assets/..%2findex.html",
                                  "/ui/assets/%2e%2e/%2e%2e/%2e%2e/conftest.py"])
def test_missing_or_escaping_assets_are_404_not_the_index(settings, path):
    r = make(settings).get(path)
    assert r.status_code == 404, path
    assert INDEX_MARK not in r.text


# ---------- login guard on pages ----------
@pytest.mark.parametrize("path,location", [
    ("/ui", "/ui/login?next=/ui"),
    ("/ui/runs/abc", "/ui/login?next=/ui/runs/abc"),
    ("/ui/escalations?status=open", "/ui/login?next=/ui/escalations%3Fstatus%3Dopen"),
])
def test_signed_out_page_visits_redirect_to_login_with_next(settings, path, location):
    r = make(settings).get(path)
    assert r.status_code == 303 and r.headers["location"] == location
    assert INDEX_MARK not in r.text


def test_signed_out_redirect_never_points_off_site(settings):
    r = make(settings).get("/ui//evil.example")
    loc = r.headers["location"]
    assert r.status_code == 303 and loc.startswith("/ui/login?next=/") and "//evil" not in loc.split("next=")[0]


def test_ui_config_is_an_api_call_401_not_a_redirect(settings):
    r = make(settings).get("/ui/config")
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/json")


def test_page_routes_are_get_only(settings):
    assert signed_in(settings).post("/ui/runs", headers=ORIGIN).status_code == 405


# ---------- no build ----------
def test_missing_build_is_a_clear_503_page_not_a_crash(settings, tmp_path):
    c = signed_in(settings, web_dist_dir=str(tmp_path / "nope"))
    for path in ("/ui", "/ui/runs/x"):
        r = c.get(path)
        assert r.status_code == 503 and r.headers["content-type"].startswith("text/html"), path
        assert "npm run build" in r.text
    assert make(settings, web_dist_dir=str(tmp_path / "nope")).get("/ui/login").status_code == 503
    assert c.get("/ui/assets/index-test.js").status_code == 404
    assert c.get("/runs").status_code == 200  # the API is unaffected


# ---------- /ui/config ----------
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
