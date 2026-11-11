"""Recovery after a worker died mid-node: stuck-run sweep, its human resolution, and the call-result guard."""

import contextlib
import time

from fastapi.testclient import TestClient
from test_api import retell_post
from test_idempotency_audit import LATER, Killed, analyzed, api_env, kill_after, only_run, push, welcome_count

from app.main import create_app

HUMAN = "priya@novacrm.example"


def _killed_in_create(settings):
    app, c, client, h = api_env(settings)
    push(client), push(client)
    rid = only_run(h)
    real = h.world.rocketlane.create_project
    h.world.rocketlane.create_project = kill_after(real)
    with contextlib.suppress(Killed):
        retell_post(client, analyzed(rid))
    h.world.rocketlane.create_project = real
    assert h.status(rid) == "TIER_CONFIRMED" and len(h.world.rocketlane.projects) == 1
    return c, client, h, rid


def test_stuck_run_is_escalated_as_system_with_retry_and_cancel(settings):
    c, client, h, rid = _killed_in_create(settings)
    create_app(c).state.tick(now=time.time() + LATER)
    assert h.status(rid) == "ESCALATED"
    [esc] = [e for e in h.store.list_escalations("open") if e["run_id"] == rid]
    assert esc["kind"] == "system" and "TIER_CONFIRMED" in esc["reason"]


def test_recent_mid_node_run_is_not_swept(settings):
    c, client, h, rid = _killed_in_create(settings)
    create_app(c).state.tick(now=time.time() + 60)  # well under STUCK_RUN_MINUTES
    assert h.status(rid) == "TIER_CONFIRMED"


def test_retry_of_a_stuck_run_resumes_from_the_checkpoint_without_a_second_project(settings):
    c, client, h, rid = _killed_in_create(settings)
    app = create_app(c)
    app.state.tick(now=time.time() + LATER)
    r = TestClient(app).post(f"/runs/{rid}/resolve", json={"action": "retry", "by": HUMAN})
    assert r.status_code == 200, r.text
    assert h.status(rid) == "COMPLETED"
    assert len(h.world.rocketlane.projects) == 1 and len(h.world.slack.channels) == 1 and welcome_count(h) == 1
    assert not [e for e in h.store.list_escalations("open") if e["run_id"] == rid]


def test_cancel_of_a_stuck_run(settings):
    c, client, h, rid = _killed_in_create(settings)
    app = create_app(c)
    app.state.tick(now=time.time() + LATER)
    tc = TestClient(app)
    assert tc.post(f"/runs/{rid}/resolve", json={"action": "set_tier", "tier": "growth", "by": HUMAN}).status_code == 422
    assert tc.post(f"/runs/{rid}/resolve", json={"action": "cancel", "by": HUMAN}).status_code == 200
    assert h.status(rid) == "CANCELLED" and h.world.slack.channels == {}


def test_redelivered_call_result_is_ignored_once_the_run_moved_on(settings):
    """The pending interrupt can be stale after a crash; the run status is the second witness."""
    c, client, h, rid = _killed_in_create(settings)
    assert h.runner.pending(rid)["type"] == "call"           # stale: the call was consumed
    retell_post(client, analyzed(rid))
    steps = [(a["step"], a["status"]) for a in h.store.list_audit(rid) if a["step"] == "voice_webhook"]
    assert steps[-1] == ("voice_webhook", "ignored")
    assert h.status(rid) == "TIER_CONFIRMED"


def test_run_killed_between_unclear_call_and_wait_interrupt_is_not_stuck_forever(settings):
    """DEFECT (HIGH, tester 2026-11-12): interpret_call writes status AWAITING_RETRY, then the worker dies before
    wait_retry's interrupt is checkpointed. pending() still shows the consumed call interrupt, so tick's
    AWAITING_RETRY branch (needs type 'wait') never resumes it, the stuck sweep skips it (not in MID_NODE) and
    the webhook guard ignores redeliveries (status != AWAITING_CALL). Expected: resumed or escalated."""
    app, c, client, h = api_env(settings, call_retry_delay_seconds=30)
    push(client), push(client)
    rid = only_run(h)
    real = h.store.upsert_run

    def upsert(run_id, **f):
        real(run_id, **f)
        if f.get("status") == "AWAITING_RETRY":
            raise Killed("after AWAITING_RETRY")
    h.store.upsert_run = upsert
    with contextlib.suppress(Killed):
        retell_post(client, analyzed(rid, tier="unclear", confirmed=False))
    h.store.upsert_run = real
    assert h.status(rid) == "AWAITING_RETRY"
    create_app(c).state.tick(now=time.time() + LATER)
    open_esc = [e for e in h.store.list_escalations("open") if e["run_id"] == rid]
    assert h.status(rid) != "AWAITING_RETRY" or open_esc, \
        f"run stuck in AWAITING_RETRY, pending={h.runner.pending(rid)}, no escalation"


def test_recovery_lookup_never_adopts_another_project_that_only_matches_by_name():
    from app.providers.fake import FakeWorld
    from app.services.store import MemoryStore
    from app.tools.definitions import build_registry
    world = FakeWorld(existing_projects=[{"projectId": 77, "customer": {"companyName": "Acme Corp"},
                                          "externalReferenceId": "someone-else"}])
    store = MemoryStore()
    store.reserve_idem("project:r1")                      # our create was interrupted
    out = build_registry(store, world, sleep=lambda s: None).call("intake", "rocketlane_create_project", dict(
        project_name="Acme Onboarding", customer_name="Acme Corp", owner_email="o@x", start_date="2026-11-12",
        template_id="1", member_emails=[], customer_contact_email="c@x", external_reference_id="006X"),
        run_id="r1", idempotency_key="project:r1")
    assert out.project_id != "77" and len(world.rocketlane.projects) == 2
