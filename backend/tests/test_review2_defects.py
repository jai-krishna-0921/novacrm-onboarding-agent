"""Second tester review (commits 3902128..6cf903e). Each test pins one defect or one behaviour that was
untested. Tests marked as defects FAIL on the current code on purpose; do not weaken them to go green.

Assertions are on what reached the (fake/mocked) external systems, the run status and the escalation queue.
"""

import json
import sys
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from test_api import FakeInbox, analyzed, push, retell_post
from test_workflow import EMAIL, ENTERPRISE_CALL, GOOD

from app.container import Container
from app.llm.client import LLMRefused
from app.llm.schemas import DealFields
from app.main import create_app
from app.providers.real.gmail import GmailClient
from app.providers.real.retell import RetellVoice
from app.providers.real.rocketlane import RocketlaneClient
from app.services.store import MemoryStore
from app.sim import FakeLLM, Harness
from app.tools.definitions import RlCreateIn, VoiceStartIn, build_registry
from app.tools.registry import AmbiguousToolError, PermanentToolError, ToolFailed, TransientToolError

KEY = "retell-webhook-key"
TOKEN = "push-secret"
RL_BASE = "https://api.rocketlane.com/api/1.0"


# ---------------------------------------------------------------- helpers

class FlakyLLM(FakeLLM):
    """FakeLLM that raises (as LangChainLLM does after its own retries) on the named node(s)."""

    def __init__(self, fail_nodes: set[str], **kw):
        super().__init__(**kw)
        self.fail_nodes = fail_nodes

    def parse(self, *, node, **kw):
        if node in self.fail_nodes:
            self.calls[node] = self.calls.get(node, 0) + 1
            raise LLMRefused(f"{node}: stop_reason=None parsing_error=overloaded")
        return super().parse(node=node, **kw)


def api_env(settings, llm=None, raise_server_exceptions=True, **settings_kw):
    settings = settings.model_copy(update={"gmail_push_token": TOKEN, "retell_api_key": KEY, **settings_kw})
    h = Harness(settings, llm=llm or FakeLLM(fields=DealFields(**GOOD)))
    inbox = FakeInbox([EMAIL])
    c = Container(settings=settings, store=h.store, runner=h.runner, inbox=inbox, start_poller=False)
    return TestClient(create_app(c), raise_server_exceptions=raise_server_exceptions), h, inbox


def open_escalations(h, run_id):
    return [e for e in h.store.escalations.values() if e["run_id"] == run_id and e["status"] == "open"]


# ---------------------------------------------------------------- 1. Retell webhook path

def test_webhook_dedupe_key_is_written_before_work_so_a_failed_resume_loses_the_result(settings):
    """DEFECT: main.py:107 marks the call_analyzed event as processed before resume_call runs. If the background
    resume fails before touching the graph (DB blip, pool exhausted), a redelivery of the same event (Retell
    dashboard resend, or our own replay tooling) is dropped and the run waits in AWAITING_CALL forever."""
    client, h, _ = api_env(settings, raise_server_exceptions=False)
    push(client), push(client)
    [run] = h.store.list_runs()
    rid = run["id"]

    real_pending, failed = h.runner.pending, []

    def pending_once_broken(run_id):
        if not failed:
            failed.append(run_id)
            raise ConnectionError("checkpointer connection reset")
        return real_pending(run_id)

    h.runner.pending = pending_once_broken
    retell_post(client, analyzed(rid))  # 204 to Retell, background resume crashes
    assert h.status(rid) == "AWAITING_CALL"
    retell_post(client, analyzed(rid))  # redelivery of the very same event
    assert h.status(rid) == "COMPLETED", "redelivered result was swallowed by the dedupe key"


def test_webhook_signed_with_empty_key_is_rejected_when_no_key_is_configured(settings):
    """DEFECT: Gmail push rejects an empty configured token (main.py:89 `or not token`) but the Retell
    webhook does not: with RETELL_API_KEY unset, HMAC with key b"" is a valid signature anyone can forge."""
    client, h, _ = api_env(settings, retell_api_key="")
    push(client), push(client)
    [run] = h.store.list_runs()
    r = retell_post(client, analyzed(run["id"]), key="")
    assert r.status_code == 401
    assert h.status(run["id"]) == "AWAITING_CALL" and h.world.rocketlane.projects == []


@pytest.mark.parametrize("body", [
    "not json",
    "[]",
    json.dumps({"event": "call_analyzed"}),                       # no call object
    json.dumps({"event": "call_analyzed", "call": {"metadata": {"run_id": "x"}}}),  # no call_id
])
def test_malformed_signed_webhook_is_a_client_error_not_a_500(settings, body):
    """LOW: a correctly signed but malformed body raises inside the handler (json.loads / KeyError / AttributeError)
    -> 500 -> Retell retries 3x. Expected a 4xx (or a logged 204), never a 500."""
    import hashlib
    import hmac
    import time

    client, h, _ = api_env(settings, raise_server_exceptions=False)
    ts = str(int(time.time() * 1000))
    sig = f"v={ts},d={hmac.new(KEY.encode(), (body + ts).encode(), hashlib.sha256).hexdigest()}"
    r = client.post("/webhooks/voice", content=body, headers={"X-Retell-Signature": sig})
    assert r.status_code < 500


def test_late_result_for_an_earlier_attempt_is_ignored_and_audited(settings):
    """Coverage: out-of-order delivery. call-1's analyzed event arrives (again, under a new event) after call-2
    was placed: it must not resume the run, must not create a project, and must leave an 'ignored' audit row."""
    client, h, _ = api_env(settings)
    push(client), push(client)
    [run] = h.store.list_runs()
    rid = run["id"]
    retell_post(client, analyzed(rid, call_id="call-1", reason="dial_no_answer"))
    assert len(h.world.voice.calls) == 2
    # A confirmed-enterprise result for a call that is not the pending one (e.g. the duplicate call a Retell
    # create-call timeout + registry retry can leave behind, retell.py:76-78).
    retell_post(client, analyzed(rid, call_id="call-orphan"))
    assert h.status(rid) == "AWAITING_CALL" and h.world.rocketlane.projects == []
    assert any(a["step"] == "voice_webhook" and a["status"] == "ignored" for a in h.store.list_audit(rid))


# ---------------------------------------------------------------- 2. LLM failure after side effects

@pytest.mark.parametrize("node", ["interpret_call", "draft_welcome"])
def test_llm_outage_after_side_effects_escalates_instead_of_dead_end_failed(settings, node):
    """DEFECT (HIGH): LangChainLLM raises LLMRefused / anthropic errors after max_retries=2. No graph node catches
    them, so Runner.invoke marks the run FAILED (runner.py:53). FAILED has no escalation row and /resolve refuses it,
    so:
      - interpret_call: the AE answered and confirmed, yet the run is a dead end nobody is told about.
      - draft_welcome: the Rocketlane project already exists, there is no Slack channel, and the plan's static
        welcome fallback (plan §8 'Regenerate once, then static template') is never used.
    Expected: run ends ESCALATED with an open escalation (or COMPLETED via the static welcome for draft_welcome)."""
    h = Harness(settings, llm=FlakyLLM({node}, fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL]
    try:
        rid = h.receive(EMAIL)
    except Exception:  # noqa: BLE001 - Runner re-raises after marking FAILED
        [rid] = list(h.store.runs)
    status = h.status(rid)
    if node == "draft_welcome":
        assert len(h.world.rocketlane.projects) == 1
        ok = status == "COMPLETED" and len(h.world.slack.channels) == 1
        assert ok or (status == "ESCALATED" and open_escalations(h, rid)), \
            f"project created, then run {status} with no channel and no escalation"
    else:
        assert h.world.rocketlane.projects == []
        assert status == "ESCALATED" and open_escalations(h, rid), f"run {status} with no escalation"


# ---------------------------------------------------------------- 3. Provider error mapping vs registry retry

def _rl_registry(store):
    rl = RocketlaneClient(RL_BASE, "test-key", sleep=lambda s: None)
    from app.providers.fake import FakeWorld

    world = FakeWorld()
    world.rocketlane = rl
    return build_registry(store, world, sleep=lambda s: None)


CREATE = RlCreateIn(project_name="Globex Onboarding (Growth, 14-day)", customer_name="Globex",
                    owner_email="o@x.example", start_date="2026-11-12", template_id="5000000114999",
                    member_emails=["pm@x.example"], customer_contact_email="c@globex.example",
                    external_reference_id="006A00000000001").model_dump()


@respx.mock
@pytest.mark.parametrize("status", [500, 502, 504])
def test_gateway_5xx_on_project_create_is_not_blindly_reposted(status):
    """DEFECT (HIGH, CRITICAL outcome): rocketlane.py:212 maps every 5xx to TransientToolError, so the registry
    re-POSTs /projects. A 502/504 from a gateway (or a 500 after commit) can mean the project WAS created; the
    retry then creates a duplicate. Plan §9: 'Timeout after a create: do not retry blind. Call find first.'
    Expected: exactly one POST; the existing project is found and returned."""
    posts = respx.post(f"{RL_BASE}/projects").mock(side_effect=[
        httpx.Response(status, text="upstream timeout"), httpx.Response(201, json={"projectId": 99})])
    respx.get(f"{RL_BASE}/projects").mock(return_value=httpx.Response(200, json={
        "data": [{"projectId": 42, "customer": {"companyName": "Globex"}}], "pagination": {"hasMore": False}}))
    reg = _rl_registry(MemoryStore())
    try:
        out = reg.call("intake", "rocketlane_create_project", CREATE, run_id="r1", idempotency_key="project:r1")
    except ToolFailed:
        out = None
    assert posts.call_count == 1, f"POST /projects sent {posts.call_count}x after a {status}"
    assert out is None or out.project_id == "42"


@respx.mock
@pytest.mark.parametrize("exc", [httpx.ReadError("connection reset"), httpx.RemoteProtocolError("peer closed")])
def test_connection_dropped_after_sending_create_is_ambiguous_not_a_crash(exc):
    """DEFECT (MEDIUM): rocketlane.py:205-211 only catches ConnectError/TimeoutException. A reset after the body
    was sent escapes as a raw httpx error: the registry neither records nor audits the tool call, the graph
    raises, and the run goes FAILED (no escalation) while the project may exist. Expected AmbiguousToolError."""
    respx.post(f"{RL_BASE}/projects").mock(side_effect=exc)
    rl = RocketlaneClient(RL_BASE, "k", sleep=lambda s: None)
    with pytest.raises(AmbiguousToolError):
        rl.create_project({"projectName": "x", "sources": []})


@respx.mock
def test_retell_connection_reset_is_a_tool_error_not_a_crash():
    """DEFECT (MEDIUM): retell.py:72-78 has the same gap; a ReadError escapes the registry -> run FAILED."""
    respx.post("https://api.retellai.com/v2/create-phone-call").mock(side_effect=httpx.ReadError("reset"))
    v = RetellVoice("k", "agent", "+10000000000")
    with pytest.raises((TransientToolError, PermanentToolError)):
        v.start_call(VoiceStartIn(to_number="+19999999999", ae_name="Jai", customer_name="Acme", run_id="r",
                                  attempt=1))


# ---------------------------------------------------------------- 4. Gmail sync

class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HttpError {status}")
        self.resp = SimpleNamespace(status=status)


class _Svc:
    def __init__(self, history, messages):
        self._h, self._m = history, messages

    def users(self): return self
    def history(self): return self
    def messages(self): return self
    def list(self, **kw): return _X(lambda: self._h)

    def get(self, userId, id, format):
        def go():
            if self._m[id] is None:
                raise _HttpError(404)
            return self._m[id]
        return _X(go)


class _X:
    def __init__(self, f): self.f = f
    def execute(self): return self.f()


def _msg(mid, subject):
    return {"id": mid, "threadId": "t" + mid, "payload": {"mimeType": "text/plain", "headers": [
        {"name": "From", "value": "Jai <jai@novacrm.example>"}, {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{mid}@mail>"}], "body": {"data": ""}}}


def test_message_deleted_between_history_and_get_does_not_drop_the_whole_batch():
    """DEFECT (HIGH): gmail.py:81 maps ANY 404 to PermanentToolError('history_too_old'). messages.get returns 404
    when the user deletes/moves a message between history.list and get. sync_inbox (main.py:52) then resets the
    cursor to 'now', silently dropping every OTHER deal email in the batch: no run, no escalation, no reply.
    Expected: the vanished message is skipped and the remaining deal email is returned."""
    history = {"historyId": "900", "history": [{"messagesAdded": [{"message": {"id": "a"}}, {"message": {"id": "b"}}]}]}
    g = GmailClient(_Svc(history, {"a": None, "b": _msg("b", "Closed Won: Globex")}), "cs@novacrm.example")
    emails, cursor = g.new_deal_emails("800")
    assert [e.message_id for e in emails] == ["b"] and cursor == "900"


def test_exception_mid_batch_keeps_cursor_and_later_emails_are_processed_next_sync(settings):
    """Coverage (passes today): if starting run #1 raises, the cursor is NOT advanced; the next sync re-lists the
    batch, #1 is deduped by message id (no second run, no second call) and #2 gets its run."""
    second = EMAIL.model_copy(update={"message_id": "gm-2", "thread_id": "th-2", "subject": "Closed Won: Globex"})

    class StickyInbox(FakeInbox):
        def new_deal_emails(self, start_history_id, is_relevant=None):
            self.cursor_seen.append(start_history_id)
            return list(self.emails), "200"

    # (Trigger changed after review-2 fixes: an LLM outage now escalates instead of raising. The unhandled error is
    # raised after runner.start has created gm-1's run, e.g. the database dropping mid-batch.)
    client, h, _ = api_env(settings, raise_server_exceptions=False)
    inbox = StickyInbox([EMAIL, second])
    h_container = client.app.state.container
    h_container.inbox = inbox
    real_start, broken = h.runner.start, {"on": False}

    def start(email):
        out = real_start(email)  # run row + graph progress exist before the failure, so dedupe is exercised
        if broken["on"] and email["message_id"] == "gm-1":
            raise ConnectionError("database connection lost")
        return out

    h.runner.start = start
    push(client)                                   # initialise cursor -> "100"
    broken["on"] = True
    push(client)                                   # starting gm-1 raises -> loop aborts
    assert h.store.get_kv("gmail_history_id") == "100"
    assert h.store.find_run_by_message("gm-2") is None
    broken["on"] = False
    push(client)
    assert h.store.get_kv("gmail_history_id") == "200"
    # gm-2 is the same Acme deal as gm-1 (same opportunity link, same fake extraction), so since review 5 it is
    # escalated as an in-flight duplicate before dialling. What this test needs: gm-2 got its run on the next sync.
    assert h.store.find_run_by_message("gm-2")["status"] == "ESCALATED"
    [gm1] = [r for r in h.store.list_runs() if r["gmail_message_id"] == "gm-1"]
    assert any(a["step"] == "dedupe" and a["status"] == "ignored" for a in h.store.list_audit(gm1["id"])), \
        "re-listed gm-1 must be deduped by message id, not silently skipped"
    # gm-1's one call only: the re-listed gm-1 is deduped and gm-2 (same deal, in-flight duplicate) never dials.
    assert len(h.world.voice.calls) == 1, "the re-listed gm-1 (or its duplicate gm-2) placed a second call"


# ---------------------------------------------------------------- 5. Eval runner thresholds

def test_transcript_suite_fails_when_the_model_never_confirms_anything(settings, monkeypatch, tmp_path):
    """DEFECT (MEDIUM): run_evals.py:160 only gates on false-confirmed. A model that answers 'unclear' to every
    transcript (every real run escalates; the feature is effectively deleted) exits 0. README §transcripts:
    'Plan §14 threshold: 100%'. Expected exit code 1."""
    from app.llm.schemas import TranscriptReading
    from evals import run_evals

    cases = run_evals.load("transcripts.jsonl")
    assert any(c["expected"]["ae_confirmed"] for c in cases), "dataset must contain confirmable cases"

    class Never:
        def __init__(self, *a, **k):
            self.store = a[0]

        def parse(self, **kw):
            return TranscriptReading(plan_tier="unclear", ae_confirmed=False, reason="always cautious")

    monkeypatch.setattr(run_evals, "LangChainLLM", Never)
    monkeypatch.setattr(run_evals, "Settings", lambda: settings)
    monkeypatch.setattr(run_evals, "load", lambda name: cases)
    monkeypatch.setattr(run_evals, "HERE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_evals", "--suite", "transcripts"])
    assert run_evals.main() == 1


# ---------------------------------------------------------------- 6. Store parity (MemoryStore vs PgStore)



@pytest.fixture(params=["memory", "pg"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    yield request.getfixturevalue("pg_store")  # conftest: shared test database, emptied per test


def test_idempotency_first_write_wins(store):
    """PARITY (LOW): PgStore.put_idem is ON CONFLICT DO NOTHING (first write wins); MemoryStore overwrites.
    A test that relies on overwrite semantics would pass in memory and silently differ in prod."""
    store.put_idem("project:r1", {"project_id": "1"})
    store.put_idem("project:r1", {"project_id": "2"})
    assert store.get_idem("project:r1") == {"project_id": "1"}


def test_unknown_run_field_is_rejected(store):
    """PARITY (LOW): PgStore.upsert_run raises on unknown fields; MemoryStore accepts anything, so a typo'd
    run field (e.g. a new set_status kwarg) passes every fake-mode test and crashes only in prod."""
    with pytest.raises(ValueError):
        store.upsert_run("r1", status="RECEIVED", plan_teir="growth")


def test_active_run_without_status_is_still_a_duplicate(store):
    """PARITY (LOW): PgStore filters `status NOT IN (...)`, which is NULL (excluded) for a NULL status;
    MemoryStore includes it."""
    store.upsert_run("a", customer_key="globex", rocketlane_project_id="7")
    assert store.find_active_run(opportunity_id=None, customer_key="globex", exclude_run_id="z")["id"] == "a"


def test_full_happy_path_persists_cleanly_in_pgstore(store, settings):
    """Coverage: run the real graph against each store. Catches JSON-unserialisable audit/tool payloads and
    column mismatches that MemoryStore can't."""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.graph.root import build_graph
    from app.graph.state import Deps
    from app.providers.fake import FakeWorld
    from app.services.runner import Runner

    world = FakeWorld()
    world.voice.script = [ENTERPRISE_CALL]
    llm = FakeLLM(fields=DealFields(**GOOD))
    llm.store = store
    deps = Deps(settings, store, build_registry(store, world, sleep=lambda s: None), llm, sleep=lambda s: None)
    runner = Runner(build_graph(deps, InMemorySaver()), store)
    rid, out = runner.start(EMAIL.model_dump())
    while out.get("__interrupt__"):
        req = out["__interrupt__"][0].value
        out = runner.resume_call(rid, world.voice.result_for(req["provider_call_id"]))
    run = store.get_run(rid)
    assert run["status"] == "COMPLETED" and run["plan_tier"] == "enterprise" and run["rocketlane_project_id"]
    steps = [a["step"] for a in store.list_audit(rid)]
    assert "decide_tier" in steps and "create_project" in steps
    assert all("+91" not in json.dumps(t) for t in store.list_tool_calls(rid))


# ---------------------------------------------------------------- 7. Small error-mapping findings (review 2, no test)

@respx.mock
def test_retell_409_outbound_hold_is_retryable():
    respx.post("https://api.retellai.com/v2/create-phone-call").mock(return_value=httpx.Response(409, json={}))
    with pytest.raises(TransientToolError):
        RetellVoice("k", "a", "+1").start_call(VoiceStartIn(to_number="+2", ae_name="x", customer_name="y",
                                                            run_id="r", attempt=1))


def test_gmail_403_rate_limit_is_retryable_but_403_forbidden_is_not():
    class E(Exception):
        def __init__(self, reason):
            super().__init__(f'<HttpError 403 "{reason}">')
            self.resp = SimpleNamespace(status=403)

    g = GmailClient(service=None, self_address="cs@x.example")
    with pytest.raises(TransientToolError):
        g._exec(_X(lambda: (_ for _ in ()).throw(E("rateLimitExceeded"))))
    with pytest.raises(PermanentToolError):
        g._exec(_X(lambda: (_ for _ in ()).throw(E("insufficientPermissions"))))


def test_slack_missing_scope_is_a_config_error_not_a_quiet_degradation():
    from test_slack_gmail import FakeWeb

    from app.providers.real.slack import SlackClient
    with pytest.raises(PermanentToolError, match="missing_scope"):
        SlackClient(client=FakeWeb({"shared": "missing_scope"})).invite_shared("C1", "x@y.example")


def test_auto_replies_are_not_treated_as_deal_emails():
    from app.providers.real.gmail import to_inbound
    ooo = _msg("c", "Automatic reply: Re: Closed Won: Globex")
    assert to_inbound(ooo).auto_reply
    hdr = _msg("d", "Re: Closed Won: Globex")
    hdr["payload"]["headers"].append({"name": "Auto-Submitted", "value": "auto-replied"})
    assert to_inbound(hdr).auto_reply
    assert not to_inbound(_msg("e", "Closed Won: Globex")).auto_reply
    history = {"historyId": "9", "history": [{"messagesAdded": [{"message": {"id": "c"}}, {"message": {"id": "e"}}]}]}
    g = GmailClient(_Svc(history, {"c": ooo, "e": _msg("e", "Closed Won: Globex")}), "cs@novacrm.example")
    assert [e.message_id for e in g.new_deal_emails("1", is_relevant=lambda e: True)[0]] == ["e"]
