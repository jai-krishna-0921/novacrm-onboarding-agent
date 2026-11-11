"""Idempotency / duplicate-side-effect / concurrency audit (tester review 2026-11-12).

Every real side effect (AE phone call, Rocketlane project / task write, Slack channel / message, Gmail reply)
must happen at most once per logical event, whatever is retried, replayed, restarted or clicked twice.

Assertions are on the fake providers' call logs (what would have reached the real API), run status and the
escalation queue. Concurrency tests use real threads and record how many threads were inside the critical
section at once, so they prove the lock, not just a lucky interleaving.

Tests whose docstring starts with DEFECT fail on the current code on purpose. Fix the code; do not weaken them.
"""

import contextlib
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from test_api import KEY, TOKEN, FakeInbox, analyzed, push, retell_post
from test_migration_check import rows
from test_workflow import EMAIL, ENTERPRISE_CALL, GOOD, NO_ANSWER

from app.container import Container
from app.llm.schemas import DealFields
from app.main import create_app
from app.providers.fake import FakeGmail, FakeVoice, FakeWorld
from app.providers.real.gmail import GmailClient
from app.providers.real.retell import RetellVoice
from app.providers.real.slack import SlackClient
from app.services.store import MemoryStore
from app.sim import FakeLLM, Harness
from app.tools.definitions import build_registry
from app.tools.registry import PermanentToolError, TransientToolError

HUMAN = "priya@novacrm.example"
LATER = 10_000


class Killed(BaseException):
    """The worker process dying mid-node. BaseException: neither the registry nor Runner.invoke catches it,
    so nothing after the side effect (idempotency record, checkpoint, status) gets written."""


def kill_after(real, armed=lambda: True):
    """Wrap a provider function: perform the real side effect, then die (once)."""
    fired = []

    def wrapper(*a, **kw):
        out = real(*a, **kw)
        if armed() and not fired:
            fired.append(1)
            raise Killed(getattr(real, "__name__", "side effect"))
        return out
    return wrapper


class Inside:
    """Counts threads concurrently inside a critical section; `peak` > 1 means the lock did not serialize."""

    def __init__(self, hold: float = 0.05):
        self.hold, self.now, self.peak, self._m = hold, 0, 0, threading.Lock()

    def wrap(self, fn):
        def wrapper(*a, **kw):
            with self._m:
                self.now += 1
                self.peak = max(self.peak, self.now)
            try:
                time.sleep(self.hold)
                return fn(*a, **kw)
            finally:
                with self._m:
                    self.now -= 1
        return wrapper


def together(*fns):
    """Run callables in parallel threads released by one barrier; return their results in order."""
    gate, out = threading.Barrier(len(fns)), [None] * len(fns)

    def go(i, f):
        gate.wait()
        out[i] = f()
    ts = [threading.Thread(target=go, args=(i, f)) for i, f in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    return out


def api_env(settings, inbox=None, voice=None, auto_wait=False, contacts=None, **kw):
    s = settings.model_copy(update={"gmail_push_token": TOKEN, "retell_api_key": KEY, **kw})
    h = Harness(s, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.auto_wait = auto_wait
    c = Container(settings=s, store=h.store, runner=h.runner, inbox=inbox or FakeInbox([EMAIL]), start_poller=False,
                  voice=voice, registry=h.deps.registry, llm=h.llm, migration_contacts=contacts)
    app = create_app(c)
    return app, c, TestClient(app, raise_server_exceptions=False), h


def registry_with(**real):
    """Registry over the fake world with one real provider client swapped in (its transport is faked)."""
    world = FakeWorld()
    for k, v in real.items():
        setattr(world, k, v)
    return build_registry(MemoryStore(), world, sleep=lambda s: None)


def only_run(h):
    [run] = h.store.list_runs()
    return run["id"]


def welcome_count(h):
    return sum(len(ch["messages"]) for ch in h.world.slack.channels.values())


# =====================================================================================================
# 1. Gmail
# =====================================================================================================

class HistoryInbox:
    """Realistic history API: returns the deal email for any cursor older than '200' (not a pop-once queue)."""

    def __init__(self, emails, inside: Inside | None = None):
        self.emails, self.reads = list(emails), 0
        if inside:
            self.new_deal_emails = inside.wrap(self.new_deal_emails)

    def current_history_id(self):
        return "200"

    def new_deal_emails(self, cursor, is_relevant=None):
        self.reads += 1
        return (list(self.emails) if cursor < "200" else []), "200"

    def recent_deal_emails(self, is_relevant):
        return list(self.emails)


def test_pubsub_push_and_poller_racing_on_one_cursor_start_one_run_and_one_call(settings):
    """Protected by the app-wide lock around the whole sync (cursor read -> runs -> cursor write)."""
    inside = Inside()
    app, _, client, h = api_env(settings, inbox=HistoryInbox([EMAIL], inside))
    h.store.set_kv("gmail_history_id", "100")
    codes = together(lambda: push(client).status_code, lambda: push(client).status_code,
                     lambda: push(client).status_code)
    assert codes == [200, 200, 200]
    assert inside.peak == 1, "two inbox syncs ran concurrently"
    assert len(h.store.list_runs()) == 1 and len(h.world.voice.calls) == 1
    assert h.store.get_kv("gmail_history_id") == "200"


def test_history_too_old_full_sync_rereading_processed_mail_starts_nothing(settings):
    """Protected by Runner.start's dedupe on Gmail message id (plus the unique column in Postgres)."""
    inbox = HistoryInbox([EMAIL])
    app, _, client, h = api_env(settings, inbox=inbox)
    h.store.set_kv("gmail_history_id", "100")
    push(client)
    rid = only_run(h)

    def too_old(cursor, is_relevant=None):
        raise PermanentToolError("history_too_old")
    inbox.new_deal_emails = too_old
    push(client), push(client)
    assert [r["id"] for r in h.store.list_runs()] == [rid] and len(h.world.voice.calls) == 1
    assert [a["status"] for a in h.store.list_audit(rid) if a["step"] == "dedupe"] == ["ignored", "ignored"]


def test_gmail_reply_timeout_after_send_does_not_email_the_ae_twice():
    """DEFECT (MEDIUM): a socket timeout on messages.send happens after Gmail may have accepted the message.
    gmail.py:131-140 maps it (status 0) to TransientToolError and the registry re-sends: the AE gets two
    identical clarification emails. Expected: one send (treat a post-send timeout on a write as ambiguous)."""
    sent = []

    class Send:
        def execute(self):
            sent.append(1)
            if len(sent) == 1:
                raise TimeoutError("The read operation timed out")
            return {"id": f"m{len(sent)}"}

    svc = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: SimpleNamespace(
        send=lambda userId, body: Send())))
    reg = registry_with(gmail=GmailClient(svc, "cs@novacrm.example"))
    with contextlib.suppress(Exception):  # failing/escalating instead of repeating is an acceptable fix
        reg.call("intake", "gmail_reply", dict(thread_id="t", rfc_message_id="<m@x>", to="jai@novacrm.example",
                                               subject="Re: x", body="please send the contact email"),
                 run_id="r", idempotency_key="clarify:r")
    assert len(sent) == 1, f"clarification email sent {len(sent)} times"


# =====================================================================================================
# 2. Retell
# =====================================================================================================

def test_same_call_webhook_delivered_twice_concurrently_creates_one_project(settings):
    """Protected by the lock + the pending-call guard: the second delivery finds the run no longer waiting."""
    inside = Inside()
    app, _, client, h = api_env(settings)
    h.world.rocketlane.create_project = inside.wrap(h.world.rocketlane.create_project)
    push(client), push(client)
    rid = only_run(h)
    codes = together(lambda: retell_post(client, analyzed(rid)).status_code,
                     lambda: retell_post(client, analyzed(rid)).status_code)
    assert codes == [204, 204]
    assert h.status(rid) == "COMPLETED"
    assert len(h.world.rocketlane.projects) == 1 and len(h.world.slack.channels) == 1 and welcome_count(h) == 1
    assert sorted(a["status"] for a in h.store.list_audit(rid) if a["step"] == "voice_webhook") == ["ignored", "ok"]


def test_webhook_landing_while_scheduler_reconciles_the_same_call_is_applied_once(settings):
    """Protected: reconcile polls Retell outside the lock, then applies under the lock through the same guard.
    The webhook is delivered deterministically *inside* get_call, i.e. in the race window."""
    app, _, client, h = api_env(settings)
    push(client), push(client)
    rid = only_run(h)

    class Racing:
        def get_call(self, call_id):
            retell_post(client, analyzed(rid, call_id=call_id))   # the real webhook wins the race
            return analyzed(rid, call_id=call_id)["call"]
    app.state.container.voice = Racing()
    app.state.tick(now=time.time() + LATER)
    assert h.status(rid) == "COMPLETED" and len(h.world.rocketlane.projects) == 1
    steps = [(a["step"], a["status"]) for a in h.store.list_audit(rid) if a["step"].startswith("voice_")]
    assert steps == [("voice_webhook", "ok"), ("voice_reconcile", "ignored")]


@respx.mock
def test_retell_read_timeout_after_create_call_was_sent_does_not_redial_the_ae():
    """DEFECT (HIGH): retell.py:72-78 maps every httpx.TransportError, including ReadTimeout (request already
    sent, call possibly ringing), to TransientToolError; the registry then POSTs create-phone-call again. The AE's
    phone rings twice for one attempt. Expected: one POST (ambiguous -> no blind redial)."""
    route = respx.post("https://api.retellai.com/v2/create-phone-call").mock(
        side_effect=[httpx.ReadTimeout("read timed out"), httpx.Response(201, json={"call_id": "c-2"})])
    reg = registry_with(voice=RetellVoice("k", "agent", "+10000000000"))
    with contextlib.suppress(Exception):  # failing/escalating instead of repeating is an acceptable fix
        reg.call("intake", "voice_start_call", dict(to_number="+19999999999", ae_name="Jai", customer_name="Acme",
                                                    run_id="r", attempt=1), run_id="r", idempotency_key="call:r:1:0")
    assert route.call_count == 1, f"AE dialled {route.call_count} times for one attempt"


def test_restart_after_retell_accepted_a_retry_call_does_not_dial_that_attempt_again(settings, monkeypatch):
    """DEFECT (HIGH): place_call records its idempotency key only after start_call returns. If the worker dies
    between Retell accepting call 2 and that record, the run is still AWAITING_RETRY with a due 'wait' in the
    checkpoint, so the scheduler on the restarted process re-runs place_call and dials attempt 2 again
    (3 real calls for 2 attempts). Expected: 2 calls."""
    armed = []
    monkeypatch.setattr(FakeVoice, "start_call", kill_after(FakeVoice.start_call, armed=lambda: bool(armed)))
    app, c, client, h = api_env(settings, call_retry_delay_seconds=30)
    push(client), push(client)
    rid = only_run(h)
    retell_post(client, analyzed(rid, reason="dial_no_answer"))
    assert h.status(rid) == "AWAITING_RETRY" and len(h.world.voice.calls) == 1
    armed.append(1)
    with pytest.raises(Killed):
        app.state.tick(now=time.time() + 31)          # call 2 is placed, then the worker dies
    assert len(h.world.voice.calls) == 2
    create_app(c).state.tick(now=time.time() + 31)    # process restarted: fresh app, same DB + checkpoints
    assert len(h.world.voice.calls) == 2, [x["attempt"] for x in h.world.voice.calls]


# =====================================================================================================
# 3. Rocketlane create
# =====================================================================================================

def test_two_deal_emails_for_one_opportunity_confirmed_concurrently_create_one_project(settings):
    """Protected by the lock + the local active-run check, even while Rocketlane search can't see the first
    project yet (search_lag)."""
    s = settings.model_copy(update={"gmail_push_token": TOKEN, "retell_api_key": KEY})
    h = Harness(s, llm=FakeLLM(fields=DealFields(**GOOD)), rocketlane_search_lag=5)
    client = TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False)))
    ids = []
    for mid in ("gm-a", "gm-b"):
        rid, _ = h.runner.start(EMAIL.model_copy(update={"message_id": mid, "thread_id": mid}).model_dump())
        ids.append((rid, h.runner.pending(rid)["provider_call_id"]))
    together(*[lambda r=r, c=cid: retell_post(client, analyzed(r, call_id=c)) for r, cid in ids])
    assert len(h.world.rocketlane.projects) == 1 and len(h.world.slack.channels) == 1
    assert sorted(h.status(r) for r, _ in ids) == ["COMPLETED", "ESCALATED"]
    [loser] = [r for r, _ in ids if h.status(r) == "ESCALATED"]
    assert h.escalation(loser)["kind"] == "duplicate"


def test_second_deal_email_after_project_exists_does_not_phone_the_ae_again(settings):
    """DEFECT (MEDIUM): a resent/forwarded deal email (new Gmail id, same opportunity) whose project already
    exists still phones the AE: check_duplicate runs only after the tier call (intake.py graph order
    lookup_ae -> place_call -> ... -> check_duplicate). The opportunity id is known from the email, so the
    duplicate is detectable before the call. Expected: escalated as duplicate with zero new calls."""
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL, ENTERPRISE_CALL]
    first = h.receive(EMAIL)
    assert h.status(first) == "COMPLETED" and len(h.world.voice.calls) == 1
    second = h.receive(EMAIL.model_copy(update={"message_id": "gm-resent"}))
    assert h.status(second) == "ESCALATED" and len(h.world.rocketlane.projects) == 1
    assert len(h.world.voice.calls) == 1, "AE phoned again for a deal that is already onboarded"


def test_retry_resolution_clicked_twice_concurrently_applies_once(settings):
    """Protected by the lock + Runner.resolve's pending-escalation check: the second click gets 422."""
    app, _, client, h = api_env(settings, auto_wait=True)
    push(client), push(client)
    rid = only_run(h)
    for i in (1, 2, 3):
        retell_post(client, analyzed(rid, call_id=f"call-{i}", reason="dial_no_answer"))
    assert h.status(rid) == "ESCALATED"
    h.world.rocketlane.create_project = Inside().wrap(h.world.rocketlane.create_project)
    body = {"action": "set_tier", "tier": "growth", "by": HUMAN}
    codes = together(lambda: client.post(f"/runs/{rid}/resolve", json=body).status_code,
                     lambda: client.post(f"/runs/{rid}/resolve", json=body).status_code)
    assert sorted(codes) == [200, 422]
    assert h.status(rid) == "COMPLETED"
    assert len(h.world.rocketlane.projects) == 1 and len(h.world.slack.channels) == 1 and welcome_count(h) == 1
    assert [a["step"] for a in h.store.list_audit(rid)].count("resolve") == 1


def test_redelivered_call_webhook_after_worker_died_mid_create_makes_a_second_project(settings):
    """DEFECT (CRITICAL): the worker dies after Rocketlane accepted POST /projects but before the registry stored
    `project:{run}`. The parent checkpoint still holds the consumed call interrupt, so Runner.pending() keeps
    reporting {'type':'call', call-1}. A redelivered call_analyzed (Retell is at-least-once) passes the
    pending-call guard (main.py _apply_call_result), the graph re-runs create_project, and it POSTs again
    with no find-before-create (intake.py create_project). Expected: one project."""
    app, _, client, h = api_env(settings)
    push(client), push(client)
    rid = only_run(h)
    h.world.rocketlane.create_project = kill_after(h.world.rocketlane.create_project)
    with contextlib.suppress(Killed):                       # background task: TestClient may swallow it
        retell_post(client, analyzed(rid))
    assert h.status(rid) == "TIER_CONFIRMED"                  # died inside create_project
    assert len(h.world.rocketlane.projects) == 1
    retell_post(client, analyzed(rid))                       # duplicate delivery of the same event
    assert len(h.world.rocketlane.projects) == 1, "second Rocketlane project created for one deal"


def test_run_killed_mid_node_is_recovered_or_escalated_by_the_scheduler(settings):
    """DEFECT (HIGH): nothing resumes a run whose worker died between interrupts. tick() only looks at
    AWAITING_RETRY and AWAITING_CALL, so a run killed in create_project sits in TIER_CONFIRMED forever with a
    project in Rocketlane, no Slack channel and nothing in the escalation queue. Expected: the scheduler
    resumes it (safely) or escalates it to a human."""
    app, c, client, h = api_env(settings)
    push(client), push(client)
    rid = only_run(h)
    h.world.rocketlane.create_project = kill_after(h.world.rocketlane.create_project)
    with contextlib.suppress(Killed):                       # background task: TestClient may swallow it
        retell_post(client, analyzed(rid))
    assert h.status(rid) == "TIER_CONFIRMED"                  # died inside create_project
    create_app(c).state.tick(now=time.time() + LATER)        # restarted process, much later
    open_esc = [e for e in h.store.list_escalations("open") if e["run_id"] == rid]
    assert h.status(rid) in {"COMPLETED", "ESCALATED"} or open_esc, \
        f"run stuck in {h.status(rid)} with no escalation"


# =====================================================================================================
# 4. Slack
# =====================================================================================================

def test_slack_post_timeout_after_send_does_not_post_the_welcome_twice():
    """DEFECT (MEDIUM): chat.postMessage has no idempotency. slack.py:30-31 maps a socket timeout (message may
    already be in the channel) to TransientToolError and the registry re-posts: the customer sees the welcome
    (and any phase progress line) twice. Expected: one post."""
    posts = []

    class Web:
        def chat_postMessage(self, **kw):
            posts.append(kw)
            if len(posts) == 1:
                raise TimeoutError("The read operation timed out")
            return {"ok": True}

    reg = registry_with(slack=SlackClient(client=Web()))
    with contextlib.suppress(Exception):  # failing/escalating instead of repeating is an acceptable fix
        reg.call("comms", "slack_post_message", {"channel_id": "C1", "text": "Welcome"}, run_id="r",
                 idempotency_key="welcome:r")
    assert len(posts) == 1, f"welcome posted {len(posts)} times"


def test_channel_created_but_response_lost_still_lets_a_human_retry_complete(settings):
    """DEFECT (MEDIUM): conversations.create times out after Slack created the channel. The registry retries ->
    'name_taken' (permanent) -> system escalation. The human's only non-cancel option, 'retry', re-runs
    setup_channel with the same name -> name_taken again, forever. The run can never complete; the orphan channel
    has no topic, invites or welcome. Expected: retry adopts/finds the channel (or a new name) and completes with
    one channel and one welcome."""
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL]
    real, fired = h.world.slack.create_channel, []

    def created_then_timeout(name):
        cid = real(name)
        if not fired:
            fired.append(1)
            raise TransientToolError("slack transport: The read operation timed out")
        return cid
    h.world.slack.create_channel = created_then_timeout
    rid = h.receive(EMAIL)
    assert h.status(rid) == "ESCALATED" and len(h.world.rocketlane.projects) == 1
    h.resolve(rid, {"action": "retry", "by": HUMAN})
    assert len(h.world.slack.channels) == 1
    assert h.status(rid) == "COMPLETED", f"retry cannot finish: {h.escalation(rid)['reason']}"
    assert welcome_count(h) == 1


def test_comms_rerun_after_late_failure_does_not_repeat_channel_topic_invites_or_welcome(settings):
    """Protected by per-step idempotency keys (channel/topic/invite/shared/welcome:{run})."""
    s = settings.model_copy(update={"slack_internal_member_ids": "U1,U2"})
    h = Harness(s, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL]
    real, fails = h.world.slack.post, [1]

    def post(cid, text):
        if fails:
            fails.pop()
            raise PermanentToolError("channel_not_found")
        real(cid, text)
    h.world.slack.post = post
    rid = h.receive(EMAIL)
    assert h.status(rid) == "ESCALATED"
    h.resolve(rid, {"action": "retry", "by": HUMAN})
    assert h.status(rid) == "COMPLETED"
    [ch] = h.world.slack.channels.values()
    assert ch["members"] == ["U1", "U2"] and ch["shared_invites"] == ["dana@acme.example"]
    assert len(ch["messages"]) == 1


# =====================================================================================================
# 5. Tracking (Agent 3) and 6. human actions on it
# =====================================================================================================

@pytest.fixture
def tracked(ent):
    h, rid = ent
    s = h.settings.model_copy(update={"tracking_interval_seconds": 600})
    c = Container(settings=s, store=h.store, runner=h.runner, start_poller=False, registry=h.deps.registry,
                  llm=h.llm, migration_contacts=lambda: (rows(5), rows(5)))
    app = create_app(c)
    return app, TestClient(app, raise_server_exceptions=False), h, rid


def _mark(h, **kw):
    [p] = h.world.rocketlane.projects
    h.world.rocketlane.mark(p["projectId"], **kw)


def test_scheduler_tick_and_manual_sync_concurrently_post_a_phase_once(tracked):
    """Protected by the lock around each Tracker.sync + the `{run}:progress:{phase}` idempotency key."""
    app, client, h, rid = tracked
    inside = Inside()
    h.world.slack.post = inside.wrap(h.world.slack.post)
    _mark(h, phase="Kickoff")
    together(lambda: app.state.tick(now=time.time()), lambda: client.post(f"/runs/{rid}/sync"),
             lambda: client.post(f"/runs/{rid}/sync"))
    [ch] = h.world.slack.channels.values()
    assert len(ch["messages"]) == 2, ch["messages"]      # welcome + one Kickoff line
    assert inside.peak == 1


def test_go_live_handoff_note_is_written_once_under_concurrent_syncs(tracked):
    """Protected by the lock, the handed_off_at short-circuit and the marker in the note."""
    app, client, h, rid = tracked
    for phase in ("Kickoff", "Data Migration", "Configuration"):
        _mark(h, phase=phase)
    client.post(f"/runs/{rid}/sync")
    _mark(h, phase="Go-Live")
    together(*[lambda: client.post(f"/runs/{rid}/sync") for _ in range(3)])
    [p] = h.world.rocketlane.projects
    [handoff] = [t for t in p["tasks"] if t["taskName"] == "Handoff to support"]
    assert handoff["taskPrivateNote"].count("Handoff summary") == 1
    assert h.status(rid) == "HANDED_OFF" and h.llm.calls.get("draft_handoff") == 1
    # Monotonic: a late duplicate call webhook or a resolve can't move a handed-off run.
    assert client.post(f"/runs/{rid}/resolve", json={"action": "cancel", "by": HUMAN}).status_code == 422
    assert h.status(rid) == "HANDED_OFF"


def test_dismiss_clicked_twice_concurrently_dismisses_once(ent):
    """Protected by the lock + 'no open migration escalation' check: the second click is a 404."""
    h, rid = ent
    c = Container(settings=h.settings, store=h.store, runner=h.runner, start_poller=False, registry=h.deps.registry,
                  migration_contacts=lambda: (rows(5), rows(4)))
    client = TestClient(create_app(c))
    assert client.post(f"/runs/{rid}/migration-check").json()["passed"] is False
    body = {"reason": "known junk row", "by": HUMAN}
    codes = together(*[lambda: client.post(f"/runs/{rid}/migration-escalation/dismiss", json=body).status_code
                       for _ in range(2)])
    assert sorted(codes) == [200, 404]
    assert [a["step"] for a in h.store.list_audit(rid)].count("dismiss_migration_escalation") == 1


def test_migration_check_double_click_writes_one_evidence_note(ent):
    """DEFECT (LOW): POST /runs/{id}/migration-check is serialized but not deduplicated. A double-click runs two
    batches and appends two evidence notes to the Rocketlane task (main.py migration_check -> uuid batch per call).
    Expected: a second request within the same import state is a no-op (or returns the first batch)."""
    h, rid = ent
    c = Container(settings=h.settings, store=h.store, runner=h.runner, start_poller=False, registry=h.deps.registry,
                  migration_contacts=lambda: (rows(5), rows(5)))
    client = TestClient(create_app(c))
    together(*[lambda: client.post(f"/runs/{rid}/migration-check") for _ in range(2)])
    batches = {r["batch_id"] for r in h.store.list_migration_checks(rid)}
    assert len(batches) == 1, f"{len(batches)} batches / evidence notes for one click"


# =====================================================================================================
# 7. Process restart: side effect done, idempotency record not yet written, graph resumed from checkpoint
# =====================================================================================================

def _gmail(h):
    return len(h.world.gmail.sent)


@pytest.mark.parametrize("target, fields, count, expect_status", [
    pytest.param("gmail.reply", {**GOOD, "customer_email": None}, _gmail, "AWAITING_CLARIFICATION",
                 id="clarification_email"),
    pytest.param("voice.start_call", GOOD, lambda h: len(h.world.voice.calls), "COMPLETED", id="first_call"),
    pytest.param("rocketlane.create_project", GOOD, lambda h: len(h.world.rocketlane.projects), "COMPLETED",
                 id="rocketlane_project"),
    pytest.param("slack.create_channel", GOOD, lambda h: len(h.world.slack.channels), "COMPLETED", id="slack_channel"),
    pytest.param("slack.post", GOOD, welcome_count, "COMPLETED", id="welcome_message"),
])
def test_resume_after_crash_between_side_effect_and_idempotency_record(settings, monkeypatch, target, fields,
                                                                       count, expect_status):
    """DEFECT (CRITICAL for rocketlane_project, HIGH for first_call, MEDIUM otherwise): ToolRegistry.call
    (registry.py:69-78) records the idempotency key only AFTER the provider returns, and the write nodes do no
    lookup first. Resuming the checkpoint after a crash in that window repeats the real side effect: a second
    project, a second phone call, a second email/welcome, or name_taken -> escalation for the channel.
    Expected: exactly one side effect and the run finishes normally."""
    system, method = target.split(".")
    if system in ("gmail", "voice"):  # registered as bound methods at build time: patch the class first
        cls = {"gmail": FakeGmail, "voice": FakeVoice}[system]
        monkeypatch.setattr(cls, method, kill_after(getattr(cls, method)))
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**fields)))
    h.world.voice.script = [ENTERPRISE_CALL, ENTERPRISE_CALL]
    if system not in ("gmail", "voice"):
        obj = getattr(h.world, system)
        setattr(obj, method, kill_after(getattr(obj, method)))
    with pytest.raises(Killed):
        h.receive(EMAIL)
    rid = only_run(h)
    assert count(h) == 1
    h._drive(rid, None)                                     # restart: resume the thread from its checkpoint
    assert count(h) == 1, f"{target} executed {count(h)} times"
    assert h.status(rid) == expect_status


def test_no_answer_retries_are_three_distinct_calls_never_replays(settings):
    """Guard for the call idempotency key: attempts 1..3 are distinct keys, so retries are never swallowed."""
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [NO_ANSWER] * 3
    rid = h.receive(EMAIL)
    assert h.status(rid) == "ESCALATED" and [c["attempt"] for c in h.world.voice.calls] == [1, 2, 3]
    assert h.world.rocketlane.projects == []
