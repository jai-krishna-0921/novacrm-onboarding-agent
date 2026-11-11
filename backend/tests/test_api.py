"""FastAPI layer with a fake container: webhooks auth/dedupe/routing, run + escalation endpoints."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from test_workflow import EMAIL, GOOD

from app.container import Container
from app.llm.schemas import DealFields
from app.main import create_app
from app.sim import FakeLLM, Harness

KEY = "retell-webhook-key"
TOKEN = "push-secret"


class FakeInbox:
    def __init__(self, emails):
        self.emails, self.cursor_seen = list(emails), []

    def current_history_id(self):
        return "100"

    def new_deal_emails(self, start_history_id, is_relevant=None):
        self.cursor_seen.append(start_history_id)
        out, self.emails = self.emails, []
        return out, "200"


@pytest.fixture
def env(settings):
    settings = settings.model_copy(update={"gmail_push_token": TOKEN, "retell_api_key": KEY})
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    inbox = FakeInbox([EMAIL])
    c = Container(settings=settings, store=h.store, runner=h.runner, inbox=inbox, start_poller=False)
    return TestClient(create_app(c)), h, inbox


def retell_post(client, payload: dict, key: str = KEY):
    body = json.dumps(payload)
    ts = str(int(time.time() * 1000))
    sig = f"v={ts},d={hmac.new(key.encode(), (body + ts).encode(), hashlib.sha256).hexdigest()}"
    return client.post("/webhooks/voice", content=body, headers={"X-Retell-Signature": sig,
                                                                 "Content-Type": "application/json"})


def analyzed(run_id, call_id="call-1", tier="enterprise", confirmed=True, reason="user_hangup"):
    return {"event": "call_analyzed", "call": {
        "call_id": call_id, "call_status": "ended", "disconnection_reason": reason,
        "metadata": {"run_id": run_id}, "transcript": f"Agent: Which plan?\nUser: {tier.title()}.\nUser: Yes.",
        "call_analysis": {"custom_analysis_data": {"plan_tier": tier, "tier_confirmed": confirmed}}}}


def push(client, token=TOKEN):
    return client.post(f"/webhooks/gmail?token={token}", json={"message": {"data": "e30=", "messageId": "1"}})


def only_run(h):
    [run] = h.store.list_runs()
    return run["id"]


def test_gmail_push_with_bad_token_is_rejected(env):
    client, h, _ = env
    assert push(client, token="nope").status_code == 401
    assert h.store.list_runs() == []


def test_first_push_initializes_cursor_then_processes_new_mail(env):
    client, h, inbox = env
    assert push(client).status_code == 200  # no cursor yet: initialise from profile, process nothing older
    assert h.store.get_kv("gmail_history_id") == "100" and h.store.list_runs() == []
    assert push(client).status_code == 200
    assert inbox.cursor_seen == ["100"] and h.store.get_kv("gmail_history_id") == "200"
    rid = only_run(h)
    assert h.status(rid) == "AWAITING_CALL"


def test_voice_webhook_full_flow_completes_run(env):
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    r = retell_post(client, analyzed(rid))
    assert r.status_code == 204
    assert h.status(rid) == "COMPLETED"
    assert h.world.rocketlane.projects[0]["sources"][0]["templateId"] == "tmpl-ent"


def test_voice_webhook_bad_signature_is_rejected_and_ignored(env):
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    assert retell_post(client, analyzed(rid), key="wrong").status_code == 401
    assert h.status(rid) == "AWAITING_CALL"


def test_duplicate_and_stale_voice_events_are_ignored(env):
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    assert retell_post(client, analyzed(rid, call_id="call-999")).status_code == 204  # not the pending call
    assert h.status(rid) == "AWAITING_CALL"
    retell_post(client, analyzed(rid, tier="unclear", confirmed=False))  # attempt 1 unclear -> call-2 placed
    assert len(h.world.voice.calls) == 2
    retell_post(client, analyzed(rid, tier="unclear", confirmed=False))  # exact redelivery of call-1 event
    assert len(h.world.voice.calls) == 2 and h.status(rid) == "AWAITING_CALL"


def test_non_analyzed_events_are_acknowledged_without_action(env):
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    assert retell_post(client, {"event": "call_ended", "call": analyzed(rid)["call"]}).status_code == 204
    assert h.status(rid) == "AWAITING_CALL"


def test_run_detail_and_escalation_resolution(env):
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    for i in (1, 2, 3):
        retell_post(client, analyzed(rid, call_id=f"call-{i}", reason="dial_no_answer"))
    assert h.status(rid) == "ESCALATED"
    [esc] = client.get("/escalations?status=open").json()
    assert esc["run_id"] == rid and esc["kind"] == "tier"
    bad = client.post(f"/runs/{rid}/resolve", json={"action": "set_tier", "tier": "platinum", "by": "priya"})
    assert bad.status_code == 422
    ok = client.post(f"/runs/{rid}/resolve", json={"action": "set_tier", "tier": "growth", "by": "priya"})
    assert ok.status_code == 200 and h.status(rid) == "COMPLETED"
    detail = client.get(f"/runs/{rid}").json()
    assert detail["run"]["status"] == "COMPLETED"
    steps = [a["step"] for a in detail["audit"]]
    assert "decide_tier" in steps and "resolve" in steps
    assert detail["llm"]["calls"] >= 1 and "cost_usd" in detail["llm"]
    assert all("+91" not in json.dumps(t) for t in detail["tool_calls"])


def test_resolve_on_a_run_that_is_not_escalated_is_rejected(env):
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    r = client.post(f"/runs/{rid}/resolve", json={"action": "cancel", "by": "priya"})
    assert r.status_code == 422 and h.status(rid) == "AWAITING_CALL"


def test_healthz(env):
    client, _, _ = env
    assert client.get("/healthz").json()["ok"] is True


class FakeRetellPoll:
    def __init__(self, calls):
        self.calls, self.asked = calls, []

    def get_call(self, call_id):
        self.asked.append(call_id)
        return self.calls.get(call_id)


def test_scheduler_resumes_due_retry_waits(settings):
    s = settings.model_copy(update={"gmail_push_token": TOKEN, "retell_api_key": KEY, "call_retry_delay_seconds": 30})
    h = Harness(s, llm=FakeLLM(fields=DealFields(**GOOD)))
    c = Container(settings=s, store=h.store, runner=h.runner, inbox=FakeInbox([EMAIL]), start_poller=False)
    app = create_app(c)
    client = TestClient(app)
    push(client), push(client)
    rid = only_run(h)
    retell_post(client, analyzed(rid, reason="dial_no_answer"))
    assert h.status(rid) == "AWAITING_RETRY" and len(h.world.voice.calls) == 1
    app.state.tick(now=time.time() + 5)          # not due yet
    assert len(h.world.voice.calls) == 1
    app.state.tick(now=time.time() + 31)         # due -> next call placed
    assert h.status(rid) == "AWAITING_CALL" and len(h.world.voice.calls) == 2


def test_scheduler_reconciles_a_call_whose_webhook_never_arrived(settings):
    s = settings.model_copy(update={"gmail_push_token": TOKEN, "retell_api_key": KEY})
    h = Harness(s, llm=FakeLLM(fields=DealFields(**GOOD)))
    poll = FakeRetellPoll({})
    c = Container(settings=s, store=h.store, runner=h.runner, inbox=FakeInbox([EMAIL]), start_poller=False, voice=poll)
    app = create_app(c)
    client = TestClient(app)
    push(client), push(client)
    rid = only_run(h)
    app.state.tick(now=time.time() + 60)          # too recent to poll
    assert poll.asked == []
    poll.calls["call-1"] = {**analyzed(rid)["call"], "call_status": "ongoing"}
    app.state.tick(now=time.time() + 400)         # stale, but the call is still ongoing
    assert poll.asked == ["call-1"] and h.status(rid) == "AWAITING_CALL"
    poll.calls["call-1"] = analyzed(rid)["call"]
    app.state.tick(now=time.time() + 400)         # ended + analyzed -> resumed via the same guarded path
    assert h.status(rid) == "COMPLETED"
    assert any(a["step"] == "voice_reconcile" for a in h.store.list_audit(rid))
