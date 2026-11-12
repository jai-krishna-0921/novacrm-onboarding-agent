"""Auth abuse against the real app under uvicorn: concurrent login brute force, CSRF variants with a valid
session, open-redirect fuzzing of `next`, and webhooks staying reachable while the dashboard is under attack."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import pytest
from fastapi.testclient import TestClient
from stress_harness import KEY, TOKEN, UdsServer, signed

from app.auth import COOKIE, MAX_FAILURES, safe_next
from app.container import Container
from app.main import create_app
from app.sim import Harness

pytestmark = pytest.mark.stress
PW = "correct-horse-battery"


def _app(settings, **kw):
    s = settings.model_copy(update={"auth_disabled": False, "admin_username": "admin", "admin_password": PW,
                                    "session_secret": "stress-secret", "retell_api_key": KEY,
                                    "gmail_push_token": TOKEN, **kw})
    h = Harness(s)
    return create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False)), h


def test_concurrent_brute_force_gets_at_most_max_failures_password_checks(settings, capsys):
    """retry_after() -> check -> record_failure() is not atomic, so a parallel burst could in principle slip past
    the limit; measured: 200 parallel guesses from 64 threads -> exactly MAX_FAILURES checks (held)."""
    app, _ = _app(settings)
    with UdsServer(app) as srv, ThreadPoolExecutor(64) as pool:
        c = srv.client(headers={"Origin": "http://stress.local"})

        def guess(i):
            return c.post("/auth/login", json={"username": "admin", "password": f"guess-{i}"}).status_code

        codes = list(pool.map(guess, range(200)))
        locked = c.post("/auth/login", json={"username": "admin", "password": PW}).status_code
    checked = codes.count(401)
    with capsys.disabled():
        print(f"\n[auth] 200 parallel wrong guesses: {checked} password checks (401), {codes.count(429)} rate-limited;"
              f" correct password afterwards -> {locked}")
    assert locked == 429, "the IP must stay locked out even for the right password"
    assert checked <= MAX_FAILURES, f"{checked} password guesses were checked; the limit is {MAX_FAILURES}"


CROSS = [{"Origin": "https://evil.example"}, {"Origin": "null"}, {"Referer": "https://evil.example/x"},
         {"Origin": "http://testserver.evil.example"}, {"Origin": "http://testserver:8080"},
         {"Referer": "http://testserver@evil.example/"}, {}]
ACTIONS = [("POST", "/runs/r1/resolve"), ("POST", "/runs/r1/sync"), ("POST", "/runs/r1/migration-check"),
           ("POST", "/runs/r1/migration-escalation/dismiss"), ("POST", "/admin/gmail/watch"), ("POST", "/auth/logout"),
           ("POST", "/auth/login"), ("PUT", "/runs/r1/resolve"), ("DELETE", "/runs/r1"), ("PATCH", "/runs")]


@pytest.mark.parametrize("headers", CROSS)
def test_cross_origin_state_changes_are_blocked_even_with_a_valid_session(settings, headers):
    app, _ = _app(settings)
    c = TestClient(app, follow_redirects=False)
    assert c.post("/auth/login", json={"username": "admin", "password": PW},
                  headers={"Origin": "http://testserver"}).status_code == 200
    assert c.cookies.get(COOKIE)
    for method, path in ACTIONS:
        r = c.request(method, path, json={"action": "cancel", "by": "x"}, headers={**headers,
                      "Content-Type": "text/plain"})
        assert r.status_code == 403, (method, path, headers, r.status_code)


NEXTS = ["/ui", "/ui#/runs/x", "/ui/../..//evil.example", "/ui//evil.example", "/ui/%2f%2fevil.example",
         "/ui\\@evil.example", "//evil.example", "https://evil.example", "/ui@evil.example", "/ui?x=//evil.example",
         "/ui/login?next=//evil.example", "javascript:alert(1)", "/ui\t//evil.example", "/ui#@evil.example",
         "/ui/..;/..;//evil.example", "\\\\evil.example", "/%2e%2e/evil", "/ui\r\nLocation: //evil.example"]


@pytest.mark.parametrize("raw", NEXTS)
def test_login_next_never_leaves_the_origin(raw):
    out = safe_next(raw)
    for base in ("https://onboard.example/ui/login?next=x", "https://onboard.example/"):
        assert urlsplit(urljoin(base, out)).netloc == "onboard.example", (raw, out)
    assert out.startswith("/ui") and "\n" not in out and "\r" not in out


def test_webhooks_still_work_while_the_login_is_being_brute_forced(settings):
    app, h = _app(settings)
    with UdsServer(app) as srv, ThreadPoolExecutor(32) as pool:
        c = srv.client()
        list(pool.map(lambda i: c.post("/auth/login", json={"username": "admin", "password": str(i)},
                                       headers={"Origin": "http://stress.local"}), range(50)))
        body, hdr = signed({"event": "call_started", "call": {"call_id": "c1", "metadata": {"run_id": "r"}}})
        assert c.post("/webhooks/voice", content=body, headers={**hdr, "Origin": "https://evil.example"}).status_code == 204
        body, hdr = signed({"event": "call_started", "call": {}}, key="wrong")
        assert c.post("/webhooks/voice", content=body, headers=hdr).status_code == 401
        assert c.post(f"/webhooks/gmail?token={TOKEN}", json={}).status_code == 200
        assert c.post("/webhooks/gmail?token=nope", json={}).status_code == 401
        assert c.get("/runs").status_code == 401
