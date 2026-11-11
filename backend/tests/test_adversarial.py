"""Adversarial graph tests: resume paths, duplicate detection, call identity, guardrail bypasses.

Each test asserts on what reached the fake external systems and on the run status, not on internals.
Tests whose docstring starts with DEFECT fail against the current code on purpose (tester review
2026-11-11). Fix the code; do not weaken the test.
"""

import contextlib

import pytest
from langgraph.types import Command
from test_workflow import EMAIL, ENTERPRISE_CALL, GOOD, HEDGED, NO_ANSWER

from app.config import Tier, tier_rules
from app.guardrails.output import check_welcome
from app.llm.schemas import DealFields
from app.sim import FakeLLM, Harness
from app.tools.registry import ToolNotAllowed, TransientToolError

HUMAN = "priya@novacrm.example"


def harness(settings, calls=(), fields=None, **world):
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**(fields or GOOD))), **world)
    h.world.voice.script = list(calls)
    return h


def cfg(run_id):
    return {"configurable": {"thread_id": run_id}}


def tier_of_project(p):
    return {"tmpl-ent": "enterprise", "tmpl-growth": "growth"}[p["sources"][0]["templateId"]]


# ---------------- duplicate projects ----------------

def test_timeout_after_accepted_create_with_search_lag_creates_one_project(settings):
    """DEFECT (CRITICAL): rl_create retries the POST inside the registry's retry loop. If the first
    POST was accepted but timed out and Rocketlane search has not indexed it yet (docs: 'a couple of
    seconds'), the find returns None and the retry creates a second project."""
    h = harness(settings, calls=[ENTERPRISE_CALL], rocketlane_timeout_after_create=True,
                rocketlane_search_lag=1)
    rid = h.receive(EMAIL)
    assert len(h.world.rocketlane.projects) == 1, "blind re-POST after an ambiguous timeout"
    assert h.status(rid) in {"COMPLETED", "ESCALATED"}


@pytest.mark.parametrize("variant", ["acme corp", "Acme  Corp", "Acme Corp ", "ACME CORP.", "Acme-Corp"])
def test_existing_rocketlane_project_is_found_for_name_variants(settings, variant):
    """DEFECT (CRITICAL) for whitespace/punctuation variants: the local check uses customer_key()
    but the Rocketlane check passes the raw name to the provider. The fake already casefolds, which is
    more lenient than real Rocketlane (companyName.eq is case-sensitive), and still misses these."""
    existing = [{"projectId": 77, "customer": {"companyName": "Acme Corp"}, "externalReferenceId": "other"}]
    h = harness(settings, calls=[ENTERPRISE_CALL], fields={**GOOD, "customer_name": variant},
                existing_projects=existing)
    rid = h.receive(EMAIL)
    assert len(h.world.rocketlane.projects) == 1, f"duplicate project created for {variant!r}"
    assert h.status(rid) == "ESCALATED"


def test_15_and_18_char_salesforce_ids_are_the_same_opportunity(settings):
    """DEFECT (HIGH): an 18-char Salesforce ID is the 15-char ID plus a checksum suffix. A second
    email for the same opportunity with the short link and a differently written customer name
    creates a second project."""
    h = harness(settings, calls=[ENTERPRISE_CALL, ENTERPRISE_CALL])
    first = h.receive(EMAIL)
    h.llm.fields = DealFields(**{**GOOD, "customer_name": "Acme Corporation",
                                 "opportunity_id": GOOD["opportunity_id"][:15]})
    short_link = f"https://novacrm.my.salesforce.com/{GOOD['opportunity_id'][:15]}"
    second = h.receive(EMAIL.model_copy(update={"message_id": "gm-2", "body": f"Acme Corporation closed. {short_link}"}))
    assert h.status(first) == "COMPLETED"
    assert len(h.world.rocketlane.projects) == 1
    assert h.status(second) == "ESCALATED"


def test_human_set_tier_still_runs_duplicate_check(settings):
    existing = [{"projectId": 77, "customer": {"companyName": "Acme Corp"}, "externalReferenceId": "x"}]
    h = harness(settings, calls=[NO_ANSWER] * 3, existing_projects=existing)
    rid = h.receive(EMAIL)
    h.resolve(rid, {"action": "set_tier", "tier": "growth", "by": HUMAN})
    assert len(h.world.rocketlane.projects) == 1
    assert h.status(rid) == "ESCALATED"
    assert "existing" in h.escalation(rid)["reason"].lower()


# ---------------- call identity / webhook replay ----------------

def _start_waiting_for_call(h, run_id="r1"):
    out = h.graph.invoke({"run_id": run_id, "email": EMAIL.model_dump()}, cfg(run_id))
    [intr] = out["__interrupt__"]
    assert intr.value["provider_call_id"] == "call-1"
    return run_id


def test_call_result_for_a_different_call_id_is_rejected(settings):
    """DEFECT (HIGH): await_call accepts any CallResult. A confirmed result belonging to another call
    (late webhook from an earlier attempt, misrouted webhook) is applied to the pending call and
    creates a project."""
    h = harness(settings)
    rid = _start_waiting_for_call(h)
    # Rejecting loudly is acceptable; creating a project is not.
    with contextlib.suppress(Exception):
        h.graph.invoke(Command(resume={"provider_call_id": "call-999", **ENTERPRISE_CALL}), cfg(rid))
    assert h.world.rocketlane.projects == []
    assert h.store.runs[rid]["status"] not in {"TIER_CONFIRMED", "PROJECT_CREATED", "COMPLETED"}


def test_redelivered_call_result_is_not_consumed_by_the_next_attempt(settings):
    """DEFECT (HIGH): a duplicate delivery of call-1's result, arriving while the run waits on call-2,
    is taken as call-2's outcome and burns an attempt (a third call is placed)."""
    h = harness(settings)
    rid = _start_waiting_for_call(h)
    first = {"provider_call_id": "call-1", **HEDGED}
    h.graph.invoke(Command(resume=first), cfg(rid))
    assert len(h.world.voice.calls) == 2
    with contextlib.suppress(Exception):
        h.graph.invoke(Command(resume=first), cfg(rid))
    assert len(h.world.voice.calls) == 2, "stale result consumed an attempt"


# ---------------- non-terminal states after unexpected failures ----------------

def test_allowlist_violation_inside_graph_marks_run_failed(settings):
    """DEFECT (HIGH): plan §8 says 'raise, run FAILED'. The exception escapes the graph and the run
    stays in TIER_CONFIRMED forever."""
    h = harness(settings, calls=[ENTERPRISE_CALL])
    reg = h.deps.registry
    reg.allowlist = {**reg.allowlist, "intake": reg.allowlist["intake"] - {"rocketlane_create_project"}}
    with pytest.raises(ToolNotAllowed):
        h.receive(EMAIL)
    [run] = h.store.runs.values()
    assert h.world.rocketlane.projects == []
    assert any(r["status"] == "blocked" and "not allowed" in r["rationale"] for r in h.store.audit_rows)
    assert run["status"] == "FAILED"


def test_ae_directory_failure_escalates_instead_of_crashing(settings):
    """DEFECT (HIGH): lookup_ae does not catch ToolFailed (every other tool node does). The run
    stays RECEIVED with no escalation."""
    h = harness(settings, calls=[ENTERPRISE_CALL])

    def down(email):
        raise TransientToolError("directory 503")

    h.world.directory.lookup = down
    with contextlib.suppress(Exception):
        h.receive(EMAIL)
    [run] = h.store.runs.values()
    assert h.world.voice.calls == []
    assert run["status"] == "ESCALATED"


# ---------------- human resolution cannot bypass other guardrails ----------------

@pytest.mark.parametrize("fields,email", [
    pytest.param({**GOOD, "deal_count": 2}, EMAIL, id="two_deals"),
    pytest.param({**GOOD, "deal_count": 2, "customer_email": None}, EMAIL, id="two_deals_missing_email"),
    pytest.param(GOOD, EMAIL.model_copy(update={"sender": "stranger@evil.example"}), id="unknown_sender"),
])
def test_set_tier_on_a_non_tier_escalation_does_not_create_a_project(settings, fields, email):
    """DEFECT (HIGH): set_tier is accepted for any escalation. On a 'two deals in one email' or
    'sender not an AE' escalation it skips validation, AE lookup and the call and creates a project.
    With a missing field it crashes inside create_project (pydantic ValidationError) and the run is
    stuck ESCALATED with a resolved escalation."""
    h = harness(settings, calls=[ENTERPRISE_CALL], fields=fields)
    rid = h.receive(email)
    assert h.status(rid) == "ESCALATED" and h.world.voice.calls == []
    # Rejecting set_tier for this escalation type is a correct outcome (note: pydantic's
    # ValidationError is a ValueError too, hence the open-escalation check below).
    with contextlib.suppress(ValueError):
        h.resolve(rid, {"action": "set_tier", "tier": "enterprise", "by": HUMAN})
    assert h.world.rocketlane.projects == []
    assert h.status(rid) in {"ESCALATED", "CANCELLED"}
    if h.status(rid) == "ESCALATED":
        assert h.escalation(rid)["status"] == "open", "run is ESCALATED but its escalation was closed"


def test_set_tier_after_project_exists_cannot_desync_slack_from_project(settings):
    """DEFECT (HIGH): after a Slack failure the project already exists (enterprise). A human
    set_tier=growth overwrites plan_tier, so the channel is named/topic'd Growth, 14-day, pooled,
    while the Rocketlane project uses the Enterprise template and the welcome still says 30-day."""
    h = harness(settings, calls=[ENTERPRISE_CALL], taken_channel_names={"onboarding-acme-corp-enterprise"})
    rid = h.receive(EMAIL)
    assert h.status(rid) == "ESCALATED"
    with contextlib.suppress(ValueError):
        h.resolve(rid, {"action": "set_tier", "tier": "growth", "by": HUMAN})
    [project] = h.world.rocketlane.projects
    assert tier_of_project(project) == "enterprise"
    for ch in h.world.slack.channels.values():
        assert "growth" not in ch["name"] and "Growth" not in ch["topic"], "channel disagrees with project"


# ---------------- resume / idempotency (regression guards, expected to pass) ----------------

def test_human_retry_after_rocketlane_down_creates_one_project_without_recalling(settings):
    h = harness(settings, calls=[ENTERPRISE_CALL], rocketlane_failures=3)
    rid = h.receive(EMAIL)
    assert h.status(rid) == "ESCALATED" and h.world.rocketlane.projects == []
    h.world.rocketlane.failures_left = 0
    h.resolve(rid, {"action": "retry", "by": HUMAN})
    assert h.status(rid) == "COMPLETED"
    assert len(h.world.rocketlane.projects) == 1 and len(h.world.voice.calls) == 1
    assert h.state(rid)["tier_source"] == "ae_call"


def test_repeated_human_retries_place_fresh_calls_each_time(settings):
    h = harness(settings, calls=[NO_ANSWER] * 6 + [ENTERPRISE_CALL])
    rid = h.receive(EMAIL)
    h.resolve(rid, {"action": "retry", "by": HUMAN})
    assert h.status(rid) == "ESCALATED" and len(h.world.voice.calls) == 6
    assert h.world.rocketlane.projects == []
    h.resolve(rid, {"action": "retry", "by": HUMAN})
    assert h.status(rid) == "COMPLETED" and len(h.world.voice.calls) == 7
    assert len(h.world.rocketlane.projects) == 1
    replayed = [r for r in h.store.audit_rows if r["step"] == "tool:voice_start_call" and r["status"] == "replayed"]
    assert replayed == [], "a stale call idempotency key skipped a real call"


def test_crash_mid_slack_setup_resumes_from_checkpoint_without_duplicates(settings):
    h = harness(settings, calls=[ENTERPRISE_CALL])
    real_post, crashed = h.world.slack.post, []

    def post_once_crashing(cid, text):
        if not crashed:
            crashed.append(1)
            raise RuntimeError("worker killed")
        real_post(cid, text)

    h.world.slack.post = post_once_crashing
    with pytest.raises(RuntimeError):
        h._drive("r1", {"run_id": "r1", "email": EMAIL.model_dump()})
    h._drive("r1", None)  # resume from the last checkpoint
    assert h.status("r1") == "COMPLETED"
    assert len(h.world.rocketlane.projects) == 1 and len(h.world.voice.calls) == 1
    [channel] = h.world.slack.channels.values()
    assert len(channel["messages"]) == 1


# ---------------- output guardrail blind spots ----------------

@pytest.mark.parametrize("extra", [
    pytest.param("Your kickoff is on 15 November 2026 at 3pm.", id="invented_date"),
    pytest.param("Go-live is targeted for Dec 1.", id="invented_short_date"),
    pytest.param("All this for Rs. 40,000 a year.", id="rupee_abbrev_price"),
])
def test_welcome_with_invented_date_or_price_is_rejected(settings, extra):
    """DEFECT (MEDIUM): plan §8 blocklists 'prices, dates not in state'. check_welcome has no date
    check at all, and the price regex misses 'Rs.'."""
    rule = tier_rules(settings)[Tier.ENTERPRISE]
    text = ("Welcome Acme Corp! Your 30-day onboarding starts today with your dedicated Customer "
            "Success Manager. " + extra)
    assert check_welcome(text, "Acme Corp", rule) != []
