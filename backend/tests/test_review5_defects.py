"""Review 5 (stress/chaos pass): fast deterministic repros of defects first seen in tests/stress.

Every test here FAILS until its defect is fixed (no xfail): the docstring names the severity, the cause and the fix
direction. Assertions are on what reached the fake external systems and on run status, not on internals.
"""

import time

from test_auth import COOKIE, ORIGIN, login, make
from test_workflow import EMAIL, ENTERPRISE_CALL, GOOD, NO_ANSWER

from app.config import Tier
from app.container import Container
from app.guardrails.output import channel_name
from app.llm.schemas import DealFields
from app.main import create_app
from app.services.tracking import PHASES, Tracker
from app.sim import FakeLLM, Harness
from app.tools.registry import AmbiguousToolError


def _email(n: int, opp: str):
    url = f"https://novacrm.lightning.force.com/lightning/r/Opportunity/{opp}/view"
    return EMAIL.model_copy(update={"message_id": f"gm-{n}", "thread_id": f"th-{n}", "rfc_message_id": f"<m{n}@mail>",
                                    "body": f"We just closed a deal. Opp: {url}"})


def _receive(h, n: int, opp: str, **fields):
    h.llm.fields = DealFields(**{**GOOD, **fields})
    return h.receive(_email(n, opp))


def _fillers(h, n=500):
    for i in range(n):  # everyday AE mail: every one becomes a (NOT_A_DEAL) run, newer than the run under test
        h.store.upsert_run(f"newer-{i}", status="NOT_A_DEAL", gmail_message_id=f"filler-{i}")


# ---------------- scheduler window ----------------

def test_scheduler_resumes_a_due_call_retry_even_with_500_newer_runs(settings):
    """DEFECT (HIGH): tick() only scans `store.list_runs(500)` (main.py tick), the 500 NEWEST runs. Every AE email
    becomes a run (NOT_A_DEAL included), so once 500 newer runs exist an older run in AWAITING_RETRY is never
    resumed, an AWAITING_CALL whose webhook was lost is never reconciled, and a run stuck mid-node is never
    escalated: non-terminal forever with no escalation. Fix: query the runs that need attention by status
    (AWAITING_*, MID_NODE) instead of the newest N."""
    s = settings.model_copy(update={"call_retry_delay_seconds": 60})
    h = Harness(s, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.auto_wait = False
    h.world.voice.script = [NO_ANSWER, ENTERPRISE_CALL]
    rid = h.receive(EMAIL)
    assert h.status(rid) == "AWAITING_RETRY" and len(h.world.voice.calls) == 1
    _fillers(h)
    app = create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False))
    app.state.tick(now=time.time() + 3600)  # the retry is long overdue
    assert len(h.world.voice.calls) == 2, "the due retry call was never placed"
    assert h.status(rid) == "AWAITING_CALL"


def test_tracking_still_watches_a_project_after_500_newer_runs(ent):
    """DEFECT (HIGH): Tracker.due_runs() (services/tracking.py) also only reads `list_runs(500)`. A live project
    whose run is older than the 500 newest runs is silently dropped from Agent 3: no progress posts, no migration
    guard (an unverified "Migration verified" is never reopened), no handoff. Fix: select runs with a project and
    without handed_off_at, not the newest N."""
    h, rid = ent
    _fillers(h)
    pid = h.store.get_run(rid)["rocketlane_project_id"]
    h.world.rocketlane.mark(pid, phase="Kickoff")
    app = create_app(Container(settings=h.settings, store=h.store, runner=h.runner, start_poller=False,
                               registry=h.deps.registry, llm=h.llm))
    app.state.tick(now=time.time())
    channel = h.world.slack.channels[h.store.get_run(rid)["slack_channel_id"]]
    assert len(channel["messages"]) == 2, "Kickoff completion was never announced: the project is not tracked"


# ---------------- duplicate calls ----------------

def test_resent_deal_email_while_the_first_run_is_on_the_phone_does_not_call_the_ae_again(settings):
    """DEFECT (MEDIUM): precheck_duplicate (graph/intake.py) only sees runs that already HAVE a project
    (store.find_active_run requires rocketlane_project_id). An AE who re-sends the deal email while the first
    run is still waiting on its call gets a second call for the same opportunity. The stress mix phoned the AE
    from 38 runs for 15 opportunities (up to 5 runs for one deal). The second run is later escalated as a duplicate
    (so no second project), but the AE has already been called. Fix: also treat in-flight runs for the same
    opportunity/customer (no project yet, not terminal) as duplicates before dialling."""
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.runner.start(EMAIL.model_dump())  # first run now waits for its call result (webhook not yet arrived)
    assert len(h.world.voice.calls) == 1
    h.runner.start(_email(2, "006Hs00001AbCdEIAV").model_dump())  # same opportunity, re-sent by the AE
    assert len(h.world.voice.calls) == 1, "the AE was phoned a second time for the same opportunity"


# ---------------- Slack channel identity ----------------

LONG = "Northwind Traders International Logistics and Supply Chain Holdings"


def test_slack_timeout_never_adopts_another_customers_channel(settings):
    """DEFECT (CRITICAL, cross-customer leak): the channel-create reconcile (tools/definitions.py
    slack_channel_exists) adopts ANY bot-created channel with the same name, and channel_name() truncates to 80
    chars, so two different customers can share a name ("...Holdings Europe" / "...Holdings Asia"). After a Slack
    timeout on customer B's create, B's run adopts customer A's channel: B's welcome is posted and B's contact is
    invited (Slack Connect) into A's customer-shared channel. Fix: reconcile only a channel this run created (e.g.
    store the channel id/name per run before the create, or include a run-unique suffix), never by name alone."""
    a_name, b_name = f"{LONG} Europe", f"{LONG} Asia"
    assert channel_name(a_name, Tier.ENTERPRISE) == channel_name(b_name, Tier.ENTERPRISE)  # precondition: collision
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL, ENTERPRISE_CALL]
    a = _receive(h, 1, "006Hs00001AaAaAIAV", customer_name=a_name, customer_email="eu@northwind-eu.example")
    assert h.status(a) == "COMPLETED"
    a_channel = h.store.get_run(a)["slack_channel_id"]

    def timeout(name):
        raise AmbiguousToolError("read timeout")  # request lost / response lost

    h.world.slack.create_channel = timeout
    b = _receive(h, 2, "006Hs00001BbBbBIAV", customer_name=b_name, customer_email="asia@northwind-asia.example")
    leaked = h.world.slack.channels[a_channel]
    assert "asia@northwind-asia.example" not in leaked["shared_invites"], "customer B invited into A's channel"
    assert len(leaked["messages"]) == 1, f"customer B's welcome posted in A's channel: {leaked['messages']}"
    assert h.store.get_run(b)["slack_channel_id"] != a_channel


def test_two_different_non_latin_customers_are_not_treated_as_one(settings):
    """DEFECT (MEDIUM): customer_key() (services/keys.py) keeps only [a-z0-9], so every name in a non-Latin script
    normalises to "" and all such customers collide: the second Japanese customer is escalated as a "duplicate" of
    the first (store.find_active_run matches customer_key ""; the real Rocketlane find_project would also match any
    project whose company name normalises to ""). channel_name() also maps both to "onboarding-customer-<tier>",
    so even without the false duplicate the channel create hits name_taken. Fix: NFKC + keep Unicode letters in
    the key (and refuse an empty key), and make the slug unique when the ASCII fold is empty."""
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL, ENTERPRISE_CALL]
    a = _receive(h, 1, "006Hs00001AaAaAIAV", customer_name="トヨタ自動車", customer_email="a@toyota.example")
    b = _receive(h, 2, "006Hs00001BbBbBIAV", customer_name="ソニーグループ", customer_email="b@sony.example")
    assert h.status(a) == "COMPLETED"
    assert h.status(b) == "COMPLETED", f"different customer blocked: {h.escalation(b)['reason']}"
    assert len(h.world.rocketlane.projects) == 2 and len(h.world.slack.channels) == 2


# ---------------- internal handoff channel ----------------

def test_internal_handoff_is_never_posted_into_any_customer_channel(settings):
    """DEFECT (MEDIUM): _post_internal_handoff (services/tracking.py) only refuses SLACK_HANDOFF_CHANNEL when it
    equals THIS run's channel. If it is set to any other customer's shared channel (a one-character slip in the
    channel id), every other customer's internal handoff summary (migration result, CSM notes) is posted into that
    customer's channel. Fix: refuse when the channel is any run's slack_channel_id (or require it to be a
    non-shared channel)."""
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL, ENTERPRISE_CALL]
    a = _receive(h, 1, "006Hs00001AaAaAIAV", customer_name="Acme Corp", customer_email="dana@acme.example")
    b = _receive(h, 2, "006Hs00001BbBbBIAV", customer_name="Globex", customer_email="hank@globex.example")
    assert h.status(a) == h.status(b) == "COMPLETED"
    a_channel = h.store.get_run(a)["slack_channel_id"]
    s = h.settings.model_copy(update={"slack_handoff_channel": a_channel})  # misconfigured: Acme's channel
    contacts = [{"id": i, "name": f"P{i}", "email": f"p{i}@globex.example"} for i in range(1, 6)]
    t = Tracker(s, h.store, h.deps.registry, h.llm, lambda: (contacts, contacts))
    pid = h.store.get_run(b)["rocketlane_project_id"]
    for p in PHASES[:-1]:
        h.world.rocketlane.mark(pid, phase=p)
    t.sync(b)
    h.world.rocketlane.mark(pid, phase="Go-Live")
    t.sync(b)
    assert h.status(b) == "HANDED_OFF"
    acme_msgs = h.world.slack.channels[a_channel]["messages"]
    assert not any("Globex" in m for m in acme_msgs), f"Globex's internal handoff posted to Acme: {acme_msgs[-1]}"


# ---------------- auth ----------------

def test_logout_revokes_the_session_server_side(settings):
    """DEFECT (LOW): sessions are stateless HMAC tokens (app/auth.py); /auth/logout only asks the browser to drop
    the cookie. A copied cookie (shared machine, proxy log, XSS elsewhere on the origin) stays valid for the full
    12 h after the user logged out. Fix: a server-side session id / revocation list (or a per-user
    session generation bumped on logout)."""
    c = make(settings)
    assert login(c).status_code == 200
    token = c.cookies[COOKIE]
    assert c.post("/auth/logout", headers=ORIGIN).status_code == 200
    c.cookies.set(COOKIE, token)  # replay the captured cookie
    assert c.get("/runs").status_code == 401, "a logged-out session token is still accepted"


# ---------------- tracking ----------------

def test_handoff_waits_until_every_phase_is_complete(ent):
    """DEFECT (LOW, design gap): rule d (services/tracking.py _maybe_handoff) fires when every Go-Live task is done
    and migration is verified, even if Kickoff/Configuration tasks are still open. The run is handed to support,
    tracking stops (due_runs skips handed-off runs), so the phases completed later are never announced to the
    customer. Seen in the 50-project stress run whenever tasks were ticked out of order. Fix: require all
    template phases complete (or keep tracking after handoff for progress posts)."""
    h, rid = ent
    pid = h.store.get_run(rid)["rocketlane_project_id"]
    contacts = [{"id": i, "name": f"P{i}", "email": f"p{i}@acme.example"} for i in range(1, 6)]
    t = Tracker(h.settings, h.store, h.deps.registry, h.llm, lambda: (contacts, contacts))
    for p in ("Data Migration", "Configuration", "Go-Live"):
        h.world.rocketlane.mark(pid, phase=p)
    h.world.rocketlane.mark(pid, phase="Kickoff")
    h.world.rocketlane.mark(pid, "Kickoff call", status={"value": 1, "label": "To do"})  # one Kickoff task open
    t.sync(rid)
    assert h.status(rid) != "HANDED_OFF", "handed off to support with a Kickoff task still open"
