"""In-app login (replaces Caddy basic auth): every guard is proven by the request it BLOCKS."""

import re
import time

import pytest
from fastapi.testclient import TestClient

from app.auth import COOKIE, SESSION_SECONDS, Auth, safe_next
from app.container import Container
from app.main import create_app
from app.sim import Harness

ORIGIN = {"Origin": "http://testserver"}
CREDS = {"username": "admin", "password": "s3cret-pw"}


def make(settings, **overrides) -> TestClient:
    s = settings.model_copy(update={"auth_disabled": False, "admin_username": "admin", "admin_password": "s3cret-pw",
                                    "session_secret": "test-session-secret", **overrides})
    h = Harness(s)
    return TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False)),
                      follow_redirects=False)


def login(c: TestClient, **body):
    return c.post("/auth/login", json={**CREDS, **body}, headers=ORIGIN)


# ---------- login ----------
def test_login_with_correct_credentials_sets_a_hardened_session_cookie(settings):
    c = make(settings)
    r = login(c)
    assert r.status_code == 200 and r.json()["user"] == "admin"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    low = cookie.lower()
    assert "httponly" in low and "samesite=strict" in low and "path=/" in low
    assert f"max-age={SESSION_SECONDS}" in low and SESSION_SECONDS == 12 * 3600
    assert "secure" not in low.replace("samesite", "")  # plain http test server: no Secure flag
    assert c.get("/auth/me").json() == {"user": "admin"}
    assert c.get("/runs").status_code == 200


def test_cookie_is_secure_when_the_request_is_https(settings):
    c = make(settings)
    c.base_url = "https://testserver"
    r = c.post("/auth/login", json=CREDS, headers={"Origin": "https://testserver"})
    assert r.status_code == 200 and "; secure" in r.headers["set-cookie"].lower()


def test_cookie_is_secure_behind_the_trusted_tls_proxy(settings):
    c = make(settings, trust_proxy=True)
    r = c.post("/auth/login", json=CREDS, headers={**ORIGIN, "X-Forwarded-Proto": "https"})
    assert "; secure" in r.headers["set-cookie"].lower()


@pytest.mark.parametrize("body", [{"password": "wrong"}, {"username": "root"}, {"password": ""},
                                  {"password": "S3CRET-PW"}])
def test_wrong_credentials_are_rejected_without_a_cookie(settings, body):
    c = make(settings)
    r = login(c, **body)
    assert r.status_code == 401 and "set-cookie" not in r.headers
    assert c.get("/runs").status_code == 401


def test_password_compare_is_constant_time():
    import inspect

    import app.auth as mod
    assert "compare_digest" in inspect.getsource(mod.Auth.check_credentials)


def test_logout_clears_the_session(settings):
    c = make(settings)
    login(c)
    r = c.post("/auth/logout", headers=ORIGIN)
    assert r.status_code == 200 and f'{COOKIE}=""' in r.headers["set-cookie"]
    assert c.get("/auth/me").status_code == 401


# ---------- session token ----------
def test_tampered_or_expired_or_foreign_tokens_are_rejected(settings):
    s = settings.model_copy(update={"admin_password": "pw", "session_secret": "k"})
    a = Auth(lambda: s)
    tok = a.sign("admin", now=1000)
    assert a.verify(tok, now=1000 + 60) == "admin"
    assert a.verify(tok, now=1000 + SESSION_SECONDS + 1) is None                  # expired
    body, sig = tok.rsplit(".", 1)
    assert a.verify(f"{body}.{'0' * len(sig)}", now=1001) is None                   # bad signature
    assert a.verify("garbage", now=1001) is None and a.verify("", now=1001) is None
    other = Auth(lambda: s.model_copy(update={"session_secret": "other"}))
    assert other.verify(tok, now=1001) is None                                       # different secret
    rotated = Auth(lambda: s.model_copy(update={"admin_password": "new"}))
    assert rotated.verify(tok, now=1001) is None                                     # password changed


def test_revoking_one_session_leaves_other_sessions_valid(settings):
    s = settings.model_copy(update={"admin_password": "pw", "session_secret": "k"})
    a = Auth(lambda: s)
    laptop, phone = a.sign("admin"), a.sign("admin")
    a.revoke(laptop)
    assert a.verify(laptop) is None and a.verify(phone) == "admin"
    a.revoke("garbage")  # unknown/invalid tokens are a no-op


def test_empty_session_secret_gets_a_random_one_per_process(settings):
    s = settings.model_copy(update={"admin_password": "pw", "session_secret": ""})
    a, b = Auth(lambda: s), Auth(lambda: s)
    assert a.verify(a.sign("admin", now=1), now=2) == "admin"
    assert b.verify(a.sign("admin", now=1), now=2) is None


# ---------- protected routes ----------
@pytest.mark.parametrize("path", ["/runs", "/runs/x", "/escalations", "/auth/me", "/ui/config", "/docs",
                                  "/openapi.json"])
def test_api_routes_need_a_session(settings, path):
    r = make(settings).get(path)
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/runs/x/resolve", "/runs/x/sync", "/runs/x/migration-check", "/admin/gmail/watch"])
def test_actions_need_a_session_even_from_the_same_origin(settings, path):
    assert make(settings).post(path, json={}, headers=ORIGIN).status_code == 401


def test_browser_visiting_the_dashboard_is_redirected_to_login(settings):
    r = make(settings).get("/ui?x=1")
    assert r.status_code == 303 and r.headers["location"] == "/ui/login?next=/ui%3Fx%3D1"


def test_forged_cookie_is_rejected(settings):
    c = make(settings)
    c.cookies.set(COOKIE, "admin.deadbeef")
    assert c.get("/runs").status_code == 401


@pytest.mark.parametrize("path", ["/healthz", "/ui/login", "/ui/assets/app.css", "/ui/assets/login.js",
                                  "/ui/assets/favicon.svg"])
def test_public_paths_stay_public(settings, path):
    assert make(settings).get(path).status_code == 200


def test_webhooks_skip_login_and_origin_checks(settings):
    """Webhooks authenticate themselves (push token, Retell HMAC); they are reached, then reject bad credentials."""
    c = make(settings, gmail_push_token="tok", retell_api_key="k")
    assert c.post("/webhooks/gmail?token=nope").status_code == 401   # reached the handler (not the login guard)
    assert c.post("/webhooks/gmail?token=tok").status_code == 200
    assert c.post("/webhooks/voice", content="{}").json()["detail"] == "bad signature"


def test_login_page_redirects_home_when_already_signed_in(settings):
    c = make(settings)
    login(c)
    r = c.get("/ui/login")
    assert r.status_code == 303 and r.headers["location"] == "/ui"


# ---------- CSRF: same-origin check on top of SameSite=Strict ----------
@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Origin": "null"},
                                     {"Referer": "https://evil.example/ui"}, {"Origin": "http://testserver.evil.example"}])
def test_cross_origin_posts_are_blocked_even_with_a_valid_session(settings, headers):
    c = make(settings)
    login(c)
    r = c.post("/runs/x/resolve", json={"action": "cancel", "by": "x"}, headers=headers)
    assert r.status_code == 403


def test_same_origin_referer_is_accepted_when_origin_is_missing(settings):
    c = make(settings)
    login(c)
    r = c.post("/auth/logout", headers={"Referer": "http://testserver/ui"})
    assert r.status_code == 200


def test_login_itself_needs_the_same_origin(settings):
    r = make(settings).post("/auth/login", json=CREDS, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and "set-cookie" not in r.headers


# ---------- rate limit ----------
def test_failed_logins_are_rate_limited_per_ip(settings):
    c = make(settings)
    for _ in range(5):
        assert login(c, password="bad").status_code == 401
    r = login(c)  # even the right password is refused while locked out
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "set-cookie" not in r.headers


def test_rate_limit_window_expires():
    a = Auth(lambda: None)
    for _ in range(5):
        a.record_failure("1.2.3.4", now=100)
    assert a.retry_after("1.2.3.4", now=101) > 0
    assert a.retry_after("5.6.7.8", now=101) == 0          # other clients unaffected
    assert a.retry_after("1.2.3.4", now=100 + 301) == 0


def test_forwarded_for_is_only_trusted_from_the_proxy(settings):
    c = make(settings)  # trust_proxy off: spoofed X-Forwarded-For must not dodge the limit
    for _ in range(5):
        login(c, password="bad")
    r = c.post("/auth/login", json=CREDS, headers={**ORIGIN, "X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 429

    p = make(settings, trust_proxy=True)  # behind Caddy: the client IP is the last X-Forwarded-For hop
    for _ in range(5):
        p.post("/auth/login", json={**CREDS, "password": "bad"}, headers={**ORIGIN, "X-Forwarded-For": "6.6.6.6"})
    assert p.post("/auth/login", json=CREDS, headers={**ORIGIN, "X-Forwarded-For": "6.6.6.6"}).status_code == 429
    ok = p.post("/auth/login", json=CREDS, headers={**ORIGIN, "X-Forwarded-For": "6.6.6.6, 7.7.7.7"})
    assert ok.status_code == 200  # spoofed first hop ignored; Caddy-appended 7.7.7.7 is a different client


# ---------- open redirect ----------
@pytest.mark.parametrize("raw,expected", [
    ("/ui", "/ui"), ("/ui#/runs/abc", "/ui#/runs/abc"), ("/ui?x=1#/escalations", "/ui?x=1#/escalations"),
    ("https://evil.example", "/ui"), ("//evil.example", "/ui"), ("/\\evil.example", "/ui"), ("/runs", "/ui"),
    ("javascript:alert(1)", "/ui"), ("/uix", "/ui"), ("/ui\\..\\evil", "/ui"), ("/ui\n", "/ui"), ("", "/ui"),
    (None, "/ui"), ("/ui/login", "/ui"),
])
def test_next_only_allows_dashboard_paths(raw, expected):
    assert safe_next(raw) == expected


def test_login_returns_a_sanitised_next(settings):
    assert login(make(settings), next="https://evil.example").json()["next"] == "/ui"
    assert login(make(settings), next="/ui#/runs/1").json()["next"] == "/ui#/runs/1"


# ---------- fail closed ----------
@pytest.mark.parametrize("mode", ["real", "fake"])
def test_without_a_password_nothing_is_open(settings, mode):
    c = make(settings, admin_password="", provider_mode=mode)
    assert c.get("/runs").status_code == 503
    assert login(c, password="").status_code == 503
    assert c.get("/ui").status_code == 503
    assert c.get("/healthz").status_code == 200


def test_auth_disabled_is_ignored_in_real_mode(settings):
    c = make(settings, auth_disabled=True, provider_mode="real")
    assert c.get("/runs").status_code == 401


def test_auth_disabled_only_for_the_fake_test_suite(settings):
    assert make(settings, auth_disabled=True).get("/runs").status_code == 200


# ---------- static pages ----------
def test_login_page_is_self_contained_and_never_parses_html(settings):
    c = make(settings)
    html = c.get("/ui/login").text
    assert "<form" in html and 'type="password"' in html and "NovaCRM Onboarding" in html
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    for ref in refs:
        assert ref.startswith("/ui/assets/") or ref.startswith("#"), ref
        assert c.get(ref).status_code == 200, ref
    assert "http://" not in html and "https://" not in html
    js = c.get("/ui/assets/login.js").text
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js, sink


def test_dashboard_js_redirects_to_login_on_401(settings):
    c = make(settings)
    login(c)
    js = c.get("/ui/assets/app.js").text
    assert "401" in js and "/ui/login" in js and "/auth/logout" in js


def test_failed_logins_do_not_leak_which_field_was_wrong(settings):
    c = make(settings)
    a = login(c, username="nobody").json()
    b = login(c, password="nope").json()
    assert a == b


def test_session_cookie_expires(settings, monkeypatch):
    c = make(settings)
    login(c)
    real = time.time
    monkeypatch.setattr("app.auth.time.time", lambda: real() + SESSION_SECONDS + 5)
    assert c.get("/runs").status_code == 401
