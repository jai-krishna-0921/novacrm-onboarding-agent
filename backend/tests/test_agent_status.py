"""GET /agents/status: status rules (stalled scheduler, tracking down, degraded integrations), heartbeats written
by the loops, stats from audit/tool rows, cached integration health that never blocks a request, login required."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_api import TOKEN, FakeInbox
from test_workflow import EMAIL

from app.container import Container
from app.main import create_app
from app.services import agent_status as st
from app.services.health import LLM_CHECK_EVERY, HealthMonitor, iso
from app.services.store import MemoryStore
from app.sim import Harness

ORIGIN = {"Origin": "http://testserver"}


def make(settings, h=None, **kw):
    s = settings.model_copy(update=kw.pop("overrides", {}))
    h = h or Harness(s)
    c = Container(settings=s, store=h.store, runner=h.runner, start_poller=False, registry=h.deps.registry, **kw)
    return TestClient(create_app(c)), h


def agent(body, aid):
    return next(a for a in body["agents"] if a["id"] == aid)


def test_shape_and_static_automations(settings):
    c, _ = make(settings)
    body = c.get("/agents/status").json()
    assert set(body) == {"agents", "scheduler", "gmail", "integrations", "rocketlane_automations"}
    assert [a["id"] for a in body["agents"]] == ["intake", "comms", "tracking"]
    for a in body["agents"]:
        assert set(a) == {"id", "name", "description", "status", "last_activity_at", "last_activity", "stats",
                          "next_run_at"}
        assert a["status"] in {"running", "idle", "degraded", "down"}
        assert all(isinstance(v, int) for v in a["stats"].values())
    assert body["scheduler"]["interval_seconds"] == st.TICK_SECONDS
    assert body["gmail"] == {"last_sync_at": None, "last_push_at": None, "watch_expires_at": None}
    assert [i["name"] for i in body["integrations"]] == ["gmail", "retell", "rocketlane", "slack", "anthropic"]
    assert all(i["ok"] and i["checked_at"] for i in body["integrations"])   # fake mode: the fakes are ok
    assert [a["name"] for a in body["rocketlane_automations"]] == [
        "Overdue 1 day → notify Project Manager", "Overdue 4 days → escalate to Project Owner"]
    assert all(set(a) == {"name", "trigger", "notify"} for a in body["rocketlane_automations"])


def test_status_requires_login(settings):
    c, _ = make(settings, overrides={"auth_disabled": False, "admin_password": "pw", "session_secret": "s"})
    r = c.get("/agents/status")
    assert r.status_code == 401 and "agents" not in r.text


# ---------------- scheduler ----------------

def test_scheduler_is_stalled_until_it_ticks_and_again_when_ticks_stop(settings):
    c, _ = make(settings)
    app = c.app
    app.state.started_at = time.time() - 100            # up 100 s, never ticked
    assert c.get("/agents/status").json()["scheduler"]["status"] == "stalled"
    app.state.tick()
    sched = c.get("/agents/status").json()["scheduler"]
    assert sched["status"] == "running" and sched["last_tick_at"]
    app.state.tick(time.time() - 3 * st.TICK_SECONDS - 1)   # last tick older than 3 intervals
    assert c.get("/agents/status").json()["scheduler"]["status"] == "stalled"


def test_fresh_app_is_not_stalled_before_its_first_tick(settings):
    c, _ = make(settings)
    assert c.get("/agents/status").json()["scheduler"]["status"] == "running"


# ---------------- tracking ----------------

def test_tracking_is_down_when_its_sync_is_stale_and_projects_need_it(settings, ent):
    h, _ = ent
    c, _ = make(settings, h)
    c.app.state.tick()                                      # runs the first tracking sweep + heartbeats
    body = c.get("/agents/status").json()
    t = agent(body, "tracking")
    assert t["status"] == "running" and t["stats"]["projects_tracked"] == 1 and t["next_run_at"]
    old = time.time() - 3 * settings.tracking_interval_seconds - 5
    h.store.set_kv(st.HB_TRACKING, iso(old))
    assert agent(c.get("/agents/status").json(), "tracking")["status"] == "down"


def test_tracking_with_nothing_to_track_is_idle_even_if_the_sync_is_old(settings):
    c, h = make(settings)
    h.store.set_kv(st.HB_TRACKING, iso(time.time() - 10**6))
    assert agent(c.get("/agents/status").json(), "tracking")["status"] == "idle"


# ---------------- rules on build_status directly ----------------

def _status(store, settings, integrations=(), now=None, started_at=None, inbox=False):
    now = now or time.time()
    return st.build_status(store, settings, list(integrations), now=now, started_at=started_at or now,
                           inbox_enabled=inbox)


def test_intake_is_down_when_the_inbox_poller_stops_syncing(settings):
    store, now = MemoryStore(), time.time()
    st.beat(store, st.HB_TICK, now)
    st.beat(store, st.HB_SYNC, now - 3 * st.POLL_SECONDS - 1)
    assert agent(_status(store, settings, now=now, started_at=now - 3600, inbox=True), "intake")["status"] == "down"
    st.beat(store, st.HB_SYNC, now)
    i = agent(_status(store, settings, now=now, started_at=now - 3600, inbox=True), "intake")
    assert i["status"] == "idle" and i["next_run_at"]
    # Fake mode has no inbox: the poller is not expected to run, so a stale sync is no reason for "down".
    st.beat(store, st.HB_SYNC, now - 10**6)
    assert agent(_status(store, settings, now=now, started_at=now - 3600), "intake")["status"] == "idle"
    st.beat(store, st.HB_TICK, now - 3600)                   # scheduler stalled: retries/reconciles stop
    assert agent(_status(store, settings, now=now, started_at=now - 3600), "intake")["status"] == "degraded"


@pytest.mark.parametrize("name,affected", [("rocketlane", "tracking"), ("slack", "comms"), ("retell", "intake"),
                                           ("anthropic", "comms")])
def test_failed_integration_degrades_its_agent(settings, name, affected):
    store, now = MemoryStore(), time.time()
    st.beat(store, st.HB_TICK, now)
    bad = [{"name": name, "ok": False, "detail": "401", "checked_at": iso(now)}]
    assert agent(_status(store, settings, bad, now=now), affected)["status"] == "degraded"
    pending = [{"name": name, "ok": False, "detail": "first check pending", "checked_at": None}]
    assert agent(_status(store, settings, pending, now=now), affected)["status"] == "idle"


def test_stats_and_last_activity_come_from_the_audit_trail(settings, ent):
    h, _ = ent
    c, _ = make(settings, h)
    body = c.get("/agents/status").json()
    i, cm = agent(body, "intake"), agent(body, "comms")
    assert i["stats"] == {"emails_today": 1, "calls_today": 1, "projects_today": 1}
    assert cm["stats"] == {"channels_created": 1}
    assert i["last_activity_at"] and i["last_activity"].split(":")[0] in {r["step"] for r in h.store.audit_rows}
    assert "+91" not in str(body)


def test_intake_is_running_while_a_run_is_in_flight(settings):
    c, h = make(settings)
    h.store.upsert_run("r1", status="AWAITING_CALL")
    c.app.state.tick()
    assert agent(c.get("/agents/status").json(), "intake")["status"] == "running"


# ---------------- heartbeats from the webhook / watch ----------------

def test_push_sync_and_watch_expiry_are_recorded(settings):
    class WatchInbox(FakeInbox):
        def watch(self, topic):
            return {"historyId": "1", "expiration": "1790000000000"}

    c, h = make(settings, overrides={"gmail_push_token": TOKEN}, inbox=WatchInbox([EMAIL]))
    assert c.post(f"/webhooks/gmail?token={TOKEN}").status_code == 200
    g = c.get("/agents/status").json()["gmail"]
    assert g["last_push_at"] and g["last_sync_at"]
    c.post("/admin/gmail/watch", headers=ORIGIN)
    assert c.get("/agents/status").json()["gmail"]["watch_expires_at"] == iso(1790000000)


def test_rejected_push_records_nothing(settings):
    c, h = make(settings, overrides={"gmail_push_token": TOKEN}, inbox=FakeInbox([]))
    assert c.post("/webhooks/gmail?token=wrong").status_code == 401
    assert h.store.get_kv(st.HB_PUSH) is None


# ---------------- integration health: cached, never blocks, LLM check rate-limited ----------------

def test_a_hanging_health_check_never_blocks_the_status_request(settings):
    release = threading.Event()

    def hangs():
        release.wait(10)
        return "late"

    monitor = HealthMonitor({"gmail": hangs, "slack": lambda: "ok"})
    c, _ = make(settings, health=monitor)
    worker = threading.Thread(target=monitor.run_once)
    worker.start()
    try:
        t0 = time.monotonic()
        r = c.get("/agents/status")
        assert time.monotonic() - t0 < 2 and r.status_code == 200
        assert r.json()["integrations"][0] == {"name": "gmail", "ok": False, "detail": "first check pending",
                                               "checked_at": None}
        monitor.run_once()                       # a second pass while one runs: returns at once, no pile-up
    finally:
        release.set()
        worker.join(5)
    assert [i["ok"] for i in c.get("/agents/status").json()["integrations"]] == [True, True]


def test_llm_health_check_runs_at_most_once_an_hour_and_failures_are_reported():
    calls = {"anthropic": 0, "gmail": 0}
    t = [0.0]

    def counted(name, fail=False):
        def fn():
            calls[name] += 1
            if fail:
                raise RuntimeError("401 invalid x-api-key\nsecond line")
            return "ok"
        return fn

    m = HealthMonitor({"gmail": counted("gmail", fail=True), "anthropic": counted("anthropic")}, clock=lambda: t[0])
    for t[0] in (0, 300, 600, LLM_CHECK_EVERY + 1):
        m.run_once()
    assert calls == {"gmail": 4, "anthropic": 2}
    gmail = m.snapshot()[0]
    assert gmail["ok"] is False and gmail["detail"] == "RuntimeError: 401 invalid x-api-key"
