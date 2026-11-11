"""Adversarial review of Agent 3 (project tracking). Tests here that FAIL expose real defects; they are left
failing on purpose so they stay visible. Pure false-positive checks (cost a retry, not a wrong action) are
strict xfails."""

import pytest
from test_migration_check import rows
from test_tracking import FACTS, OK, api, headlines, mark, mig_escalations, project, slack_messages, task, tracker  # noqa: F401

from app.db.seed import demo_contacts
from app.guardrails.output import check_handoff
from app.providers.fake import TODO
from app.services.tracking import PHASES, progress_message, static_handoff
from app.tools.registry import PermanentToolError


def _set_status_calls(h, rid):
    return [c for c in h.store.tool_calls if c["run_id"] == rid and c["tool"] == "rocketlane_set_task_status"]


# ---------------- handoff must not happen while migration is unverified ----------------

def test_reopen_failure_does_not_unblock_handoff(ent):
    """Reopen PUT fails -> MV stays completed in Rocketlane AND in the snapshot. Nothing retries the reopen, and
    _maybe_handoff only looks at MV's status, so Go-Live completion hands off with no check ever run."""
    h, rid = ent

    def boom(task_id, status_value):
        raise PermanentToolError("403: status change not permitted")

    h.world.rocketlane.set_task_status = boom
    t = tracker(h, contacts=None)
    t.sync(rid)
    mark(h, phase="Data Migration")      # includes "Migration verified"; import check cannot run (no DBs)
    t.sync(rid)
    assert len(mig_escalations(h, rid, "open")) == 1  # escalated: fine
    for p in PHASES:
        mark(h, phase=p)
    t.sync(rid)
    assert h.status(rid) != "HANDED_OFF", "handed off although no migration check ever passed"
    assert not (h.store.get_tracking(rid) or {}).get("handoff_summary")


def test_reopen_failure_does_not_announce_data_migration_to_customer(ent):
    h, rid = ent

    def boom(task_id, status_value):
        raise PermanentToolError("403: status change not permitted")

    h.world.rocketlane.set_task_status = boom
    t = tracker(h, contacts=None)
    t.sync(rid)
    mark(h, phase="Data Migration")
    t.sync(rid)
    # Customer must not be told Data Migration is complete while the agent itself says it is unverified.
    assert progress_message("Data Migration") not in headlines(h)


def test_failing_recheck_after_acceptance_blocks_handoff(ent):
    """MV accepted on a passing check; later the import is redone and the new check FAILS (open escalation).
    MV is still completed, so rule (c) never fires again and the handoff goes ahead on failed data."""
    h, rid = ent
    data = {"contacts": (rows(5), rows(5))}
    t = tracker(h, contacts=lambda: data["contacts"])
    t.sync(rid)
    mark(h, "Full import")
    t.sync(rid)
    mark(h, "Migration verified")
    t.sync(rid)                                          # accepted (passed)
    mark(h, "Full import", status=TODO)
    t.sync(rid)
    data["contacts"] = demo_contacts()                    # re-import with bad data
    mark(h, "Full import")
    t.sync(rid)
    assert len(mig_escalations(h, rid, "open")) == 1
    for p in PHASES:
        mark(h, phase=p)
    t.sync(rid)
    assert h.status(rid) != "HANDED_OFF", "handed off with the latest migration check failed and escalation open"


def test_stale_dismissal_is_not_reported_as_csm_acceptance_of_a_newer_failure(ent):
    """Dismissal of batch A must not label a later, undismissed failing batch B as 'accepted by CSM'."""
    h, rid = ent
    data = {"contacts": demo_contacts()}
    t = tracker(h, contacts=lambda: data["contacts"])
    client = api(h, lambda: data["contacts"])
    t.sync(rid)
    mark(h, "Full import")
    t.sync(rid)                                          # batch A fails
    client.post(f"/runs/{rid}/migration-escalation/dismiss", json={"reason": "row 17 is junk", "by": "csm"})
    client.post(f"/runs/{rid}/migration-check")         # batch B fails -> new OPEN escalation
    assert len(mig_escalations(h, rid, "open")) == 1
    facts = t.handoff_facts(h.store.get_run(rid), h.store.get_tracking(rid)["tasks"])
    assert facts["migration"]["result"] != "accepted by CSM"


# ---------------- reopen loop when the provider does not honour the status write ----------------

def test_reopen_not_honoured_by_rocketlane_does_not_loop_every_sync(ent):
    """DEFAULT_OPEN_STATUS / status PUT are UNVERIFIED. If Rocketlane accepts the PUT but the task stays
    completed, the snapshot (patched to 'not completed') makes every sync see a fresh completion: a PUT, a new
    private note and an escalation refresh on every tick, forever."""
    h, rid = ent
    h.world.rocketlane.set_task_status = lambda task_id, status_value: None   # 200 OK, no effect
    t = tracker(h, contacts=None)
    t.sync(rid)
    mark(h, "Migration verified")
    for _ in range(4):
        t.sync(rid)
    assert len(_set_status_calls(h, rid)) <= 1, f"{len(_set_status_calls(h, rid))} reopen PUTs for one tick by a human"
    assert task(h, "Migration verified")["taskPrivateNote"].count("Reopened by the onboarding agent") <= 1


# ---------------- handoff content accuracy ----------------

def test_handoff_does_not_claim_all_phases_complete_when_some_are_open(ent):
    """Handoff fires on Go-Live + MV only. Configuration still open -> summary must not say all four phases done."""
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    mark(h, "Full import")
    t.sync(rid)
    mark(h, phase="Data Migration")
    mark(h, phase="Go-Live")
    t.sync(rid)
    tr = h.store.get_tracking(rid)
    done = sum(x["completed"] for x in tr["tasks"])
    assert done < len(tr["tasks"])
    summary = tr.get("handoff_summary") or ""
    assert "All four phases are complete" not in summary, summary


@pytest.mark.parametrize("result", ["failed", "not run"])
def test_static_handoff_does_not_claim_csm_acceptance_without_one(result):
    f = {**FACTS, "migration": {**FACTS["migration"], "result": result, "csm_note": None,
                                "failed_checks": ["duplicates"] if result == "failed" else []}}
    assert "accepted by the CSM" not in static_handoff(f), static_handoff(f)


# ---------------- check_handoff false negatives (wrong facts that pass) ----------------

FAILED = {**FACTS, "migration": {**FACTS["migration"], "result": "failed", "failed_checks": ["duplicates"]}}


@pytest.mark.parametrize("facts,text,why", [
    (FAILED, OK.replace("Data migration check result: passed.",
                        "Data migration passed with no failed checks."), "contradicts migration result 'failed'"),
    (FACTS, OK.replace("Data migration check result: passed.",
                       "Data migration check result: passed. The first run failed and was not run again."),
     "invents an earlier failed run (passes because 'passed' appears)"),
    (FACTS, OK + " They later moved to the Growth plan.", "names the other tier"),
])
def test_check_handoff_misses_contradictions(facts, text, why):
    assert check_handoff(text, facts) != [], why


def test_check_handoff_misses_mixed_case_surnames():
    assert check_handoff(OK + " Their champion Dana McArthur signed off.", FACTS) != []


# ---------------- check_handoff false positives (cost: retry, then static fallback) ----------------

GROWTH = {**FACTS, "plan": "Growth", "duration_days": 14, "csm_model": "pooled"}
GROWTH_OK = ("Acme Corp completed the Growth plan, 14-day onboarding with a pooled CSM. All four phases are "
             "complete. Data migration check result: passed.")


@pytest.mark.xfail(strict=True, reason="false positive: a non-onboarding duration in the 'watch' sentence")
def test_check_handoff_allows_a_monitoring_window():
    assert check_handoff(GROWTH_OK + " Support should monitor usage over the first 30 days.", GROWTH) == []


@pytest.mark.xfail(strict=True, reason="false positive: Title Case paraphrase of a task read as a person")
def test_check_handoff_allows_title_case_task_paraphrase():
    assert check_handoff(OK + " Integrations Setup went smoothly.", FACTS) == []


# ---------------- delivery / idempotency ----------------

def test_failed_progress_post_is_retried_on_a_later_sync(ent):
    """Slack post fails once: the snapshot already marks the phase done, so the announcement is lost for good."""
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    real_post, state = h.world.slack.post, {"n": 0}

    def flaky(channel_id, text):
        state["n"] += 1
        if state["n"] == 1:
            raise PermanentToolError("channel_not_found")
        real_post(channel_id, text)

    h.world.slack.post = flaky
    mark(h, phase="Kickoff")
    t.sync(rid)
    t.sync(rid)
    assert headlines(h) == [progress_message("Kickoff")]


def test_task_order_change_is_not_a_change(ent):
    """Rocketlane pagination order is not guaranteed; same tasks in another order must be a no-op sync."""
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    project(h)["tasks"].reverse()
    out = t.sync(rid)
    assert out["changed"] is False


def test_two_completed_phases_and_mv_in_one_sync_post_each_phase_once(ent):
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    mark(h, phase="Kickoff")
    mark(h, phase="Data Migration")   # import + MV ticked together; check passes first, MV accepted
    t.sync(rid)
    t.sync(rid)
    assert headlines(h) == [progress_message("Kickoff"), progress_message("Data Migration")]
    assert _set_status_calls(h, rid) == []


def test_check_handoff_accepts_dates_that_are_in_the_data():
    """First real handoff eval (2026-11-12): citing the migration check's own date was flagged as invented."""
    f = {**FACTS, "migration": {**FACTS["migration"], "last_check_at": "2026-11-20T10:00:00+00:00"}}
    assert check_handoff(OK + " The data was verified on 2026-11-20.", f) == []
    assert any("dates not in the data" in p for p in check_handoff(OK + " Kickoff was on 2026-10-01.", f))


@pytest.mark.parametrize("phrase", ["no failed checks", "0 failed checks", "zero failed checks", "none failed"])
def test_check_handoff_allows_negated_failure_words_when_passed(phrase):
    """Eval hof-01 (2026-11-12): 'passed, with no failed checks' tripped the contradiction check."""
    assert check_handoff(OK + f" There were {phrase}.", FACTS) == []


def test_check_handoff_still_catches_a_real_failure_claim_when_passed():
    assert check_handoff(OK + " Two checks failed.", FACTS)
