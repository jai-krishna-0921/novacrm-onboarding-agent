"""Agent 3 - project tracking: snapshot diff, the four rules, and the guardrails that must BLOCK the bad path."""

import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from test_migration_check import rows
from test_workflow import GROWTH_CALL, run

from app.container import Container
from app.db.seed import demo_contacts
from app.guardrails.output import check_handoff
from app.llm.client import LLMError
from app.main import create_app
from app.providers.fake import TODO
from app.providers.real.rocketlane import RocketlaneClient
from app.services.migration_check import MigrationDataUnavailable
from app.services.tracking import PHASES, Tracker, progress_message, static_handoff
from app.tools.registry import PermanentToolError, ToolNotAllowed

BASE = "https://api.rocketlane.com/api/1.0"
IN_PROGRESS = {"value": 2, "label": "In progress"}
INTERNAL_WORDS = ("escalat", "migration check", "fail", "defect", "duplicate", "handoff", "reopen", "csm note")


def tracker(h, contacts=lambda: (rows(5), rows(5)), llm="harness"):
    return Tracker(h.settings, h.store, h.deps.registry, h.llm if llm == "harness" else llm, contacts)


def project(h):
    [p] = h.world.rocketlane.projects
    return p


def task(h, prefix):
    [t] = [t for t in project(h)["tasks"] if t["taskName"].lower().startswith(prefix.lower())]
    return t


def mark(h, *names, phase=None, status=None):
    kw = {"status": status} if status else {}
    h.world.rocketlane.mark(project(h)["projectId"], *names, phase=phase, **kw)


def slack_messages(h):
    [ch] = h.world.slack.channels.values()
    return ch["messages"][1:]  # [0] is the welcome


def headlines(h):
    """First line of each progress post (the rest is the progress bar / checklist)."""
    return [m.splitlines()[0] for m in slack_messages(h)]


def mig_escalations(h, rid, status=None):
    return [e for e in h.store.list_escalations(status) if e["run_id"] == rid and e.get("kind") == "migration"]


def writes(h, rid):
    """Every tool call except the read that every sync makes."""
    return [c for c in h.store.tool_calls if c["run_id"] == rid and c["tool"] != "rocketlane_list_tasks"]


def finish_all_but_go_live(h, t, rid):
    for p in PHASES[:-1]:
        mark(h, phase=p)
    t.sync(rid)


# ---------------- snapshot + idempotency ----------------

def test_first_sync_stores_snapshot_and_second_sync_without_change_does_nothing(ent):
    h, rid = ent
    t = tracker(h)
    out = t.sync(rid)
    assert out["changed"] is True and out["actions"] == []
    snap = h.store.get_tracking(rid)
    assert len(snap["tasks"]) == 16 and {x["phase"] for x in snap["tasks"]} == set(PHASES)
    n_writes, n_audit = len(writes(h, rid)), len(h.store.list_audit(rid))
    out = t.sync(rid)
    assert out["changed"] is False and out["actions"] == []
    assert len(writes(h, rid)) == n_writes
    # Routine read-only sync: recorded in tool_calls, no audit row (the 60 s sync flooded the activity feed live).
    assert [a["step"] for a in h.store.list_audit(rid)[n_audit:]] == []


def test_tracking_agent_allowlist_is_minimal_and_other_agents_cannot_reopen_tasks(ent):
    h, rid = ent
    reg = h.deps.registry
    for tool in ("rocketlane_create_project", "slack_create_channel", "slack_invite_shared", "gmail_reply",
                 "voice_start_call", "slack_post_message"):  # customer messages belong to Agent 2
        with pytest.raises(ToolNotAllowed):
            reg.call("tracking", tool, {}, run_id=rid)
    for agent in ("intake", "comms", "migration"):
        with pytest.raises(ToolNotAllowed):
            reg.call(agent, "rocketlane_set_task_status", {"task_id": "1", "status_value": 1}, run_id=rid)


# ---------------- rule a: phase progress in Slack ----------------

def test_completed_phase_posts_one_template_message_once(ent):
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    mark(h, phase="Kickoff")
    out = t.sync(rid)
    assert out["actions"] == ["Kickoff complete: progress posted"]
    assert headlines(h) == [progress_message("Kickoff")]
    t.sync(rid)
    # Reopened and re-completed in Rocketlane: still one post per phase.
    mark(h, "Kickoff call", status=TODO)
    t.sync(rid)
    mark(h, "Kickoff call")
    t.sync(rid)
    assert headlines(h) == [progress_message("Kickoff")]


@pytest.mark.parametrize("phase", PHASES)
def test_progress_messages_are_customer_safe(phase):
    msg = progress_message(phase).lower()
    assert phase.lower() in msg and not any(w in msg for w in INTERNAL_WORDS)


def test_unknown_phase_is_never_announced(ent):
    h, rid = ent
    project(h)["tasks"].append({"taskId": 1, "taskName": "Internal: fix Acme's broken export",
                                "phase": {"phaseName": "INTERNAL escalation notes"},
                                "status": {"value": 3, "label": "Completed"}, "dueDate": None, "taskPrivateNote": ""})
    tracker(h).sync(rid)
    assert slack_messages(h) == []


def test_phase_complete_without_slack_channel_posts_nothing(ent):
    h, rid = ent
    h.store.upsert_run(rid, slack_channel_id=None)
    t = tracker(h)
    mark(h, phase="Kickoff")
    out = t.sync(rid)
    assert out["actions"] == ["Kickoff complete (no Slack channel)"] and slack_messages(h) == []


# ---------------- rule b: import completed -> migration check ----------------

def test_import_completion_runs_the_migration_check_once(ent):
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    mark(h, "Full import")
    out = t.sync(rid)
    assert out["actions"] == ["'Full import' completed: migration check passed"]
    assert len({r["batch_id"] for r in h.store.list_migration_checks(rid)}) == 1
    assert "PASSED" in task(h, "Migration verified")["taskPrivateNote"]
    t.sync(rid)
    assert len({r["batch_id"] for r in h.store.list_migration_checks(rid)}) == 1


def test_import_completion_with_bad_data_escalates_and_keeps_customer_channel_clean(ent):
    h, rid = ent
    t = tracker(h, contacts=demo_contacts)
    t.sync(rid)
    mark(h, "Full import")
    t.sync(rid)
    assert len(mig_escalations(h, rid, "open")) == 1
    assert slack_messages(h) == []


def test_import_completion_with_unreachable_databases_escalates(ent):
    h, rid = ent

    def down():
        raise MigrationDataUnavailable("connection refused")

    t = tracker(h, contacts=down)
    t.sync(rid)
    mark(h, "Full import")
    out = t.sync(rid)
    assert "could not run" in out["actions"][0]
    [esc] = mig_escalations(h, rid, "open")
    assert "connection refused" in esc["reason"]


# ---------------- rule c: "Migration verified" without a passing check ----------------

@pytest.mark.parametrize("contacts,why", [(None, "no migration check has run"),
                                         (demo_contacts, "the latest migration check did not pass")])
def test_migration_verified_without_passing_check_is_reopened(ent, contacts, why):
    h, rid = ent
    t = tracker(h, contacts=contacts)
    t.sync(rid)
    if contacts:
        mark(h, "Full import")
        t.sync(rid)
    mark(h, "Migration verified")
    out = t.sync(rid)
    assert out["actions"][-1] == f"'Migration verified (validation checks passed)' reopened: {why}"
    mv = task(h, "Migration verified")
    assert mv["status"] == TODO
    assert "Reopened by the onboarding agent" in mv["taskPrivateNote"] and why in mv["taskPrivateNote"]
    assert {"status": {"value": 1}} in h.world.rocketlane.task_updates  # status-only PUT body
    [esc] = mig_escalations(h, rid, "open")
    assert "reopened" in esc["reason"]
    # The snapshot reflects the reopen: the next sync sees no change and does not reopen again.
    n = len(h.world.rocketlane.task_updates)
    assert t.sync(rid)["actions"] == [] and len(h.world.rocketlane.task_updates) == n


def test_reopen_restores_the_previous_non_completed_status(ent):
    h, rid = ent
    t = tracker(h, contacts=None)
    mark(h, "Migration verified", status=IN_PROGRESS)
    t.sync(rid)
    mark(h, "Migration verified")
    t.sync(rid)
    assert task(h, "Migration verified")["status"] == IN_PROGRESS


def test_ticking_it_again_while_still_unverified_reopens_again_with_one_escalation(ent):
    h, rid = ent
    t = tracker(h, contacts=None)
    t.sync(rid)
    for _ in range(2):
        mark(h, "Migration verified")
        t.sync(rid)
    assert task(h, "Migration verified")["status"] == TODO
    assert len(mig_escalations(h, rid, "open")) == 1


def test_no_reopen_after_a_passing_check(ent):
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    mark(h, "Full import")
    t.sync(rid)
    mark(h, "Migration verified")
    out = t.sync(rid)
    assert task(h, "Migration verified")["status"]["label"] == "Completed"
    assert "accepted" in out["actions"][-1]
    assert all("status" not in u for u in h.world.rocketlane.task_updates)
    assert mig_escalations(h, rid, "open") == []


def test_no_reopen_after_csm_dismissal(ent):
    h, rid = ent
    client = api(h, demo_contacts)
    t = tracker(h, contacts=demo_contacts)
    t.sync(rid)
    mark(h, "Full import")
    t.sync(rid)  # failing check -> open escalation
    assert client.post(f"/runs/{rid}/migration-escalation/dismiss",
                       json={"reason": "row 17 is a known test row", "by": "priya"}).status_code == 200
    mark(h, "Migration verified")
    t.sync(rid)
    assert task(h, "Migration verified")["status"]["label"] == "Completed"
    assert mig_escalations(h, rid, "open") == []


def test_data_migration_phase_is_not_announced_when_verified_is_reopened(ent):
    h, rid = ent
    t = tracker(h, contacts=None)
    t.sync(rid)
    mark(h, phase="Data Migration")
    t.sync(rid)
    assert progress_message("Data Migration") not in headlines(h)


# ---------------- rule d: handoff ----------------

def test_go_live_complete_writes_handoff_note_and_hands_off_never_to_slack(ent):
    h, rid = ent
    t = tracker(h)
    finish_all_but_go_live(h, t, rid)
    mark(h, phase="Go-Live")
    out = t.sync(rid)
    assert out["actions"][-1] == "handed off to support"
    summary = h.store.get_tracking(rid)["handoff_summary"]
    assert "Acme Corp" in summary and "Enterprise" in summary and "passed" in summary
    assert "Handoff summary" in task(h, "Handoff to support")["taskPrivateNote"]
    assert h.status(rid) == "HANDED_OFF" and h.store.get_tracking(rid)["handed_off_at"]
    msgs = slack_messages(h)
    assert all(summary not in m and "handoff" not in m.lower() for m in msgs)
    assert msgs[-1].splitlines()[0] == progress_message("Go-Live")
    # Handed-off runs are no longer synced.
    assert rid not in t.due_runs()
    assert t.sync(rid)["skipped"] == "already handed off"


def test_handoff_waits_while_migration_verified_is_not_complete(ent):
    h, rid = ent
    t = tracker(h, contacts=None)
    t.sync(rid)
    mark(h, phase="Go-Live")
    out = t.sync(rid)
    assert out["actions"] == ["Go-Live complete: progress posted", "handoff blocked: migration not verified"]
    assert h.status(rid) == "COMPLETED" and not h.store.get_tracking(rid)["handoff_summary"]
    assert task(h, "Handoff to support")["taskPrivateNote"] == ""


def test_handoff_records_csm_acceptance_when_check_failed_but_was_dismissed(ent):
    h, rid = ent
    t = tracker(h, contacts=demo_contacts)
    t.sync(rid)
    mark(h, "Full import")
    t.sync(rid)
    api(h, demo_contacts).post(f"/runs/{rid}/migration-escalation/dismiss",
                               json={"reason": "row 17 is junk", "by": "priya"})
    for p in PHASES:
        mark(h, phase=p)
    t.sync(rid)
    facts = t.handoff_facts(h.store.get_run(rid), h.store.get_tracking(rid)["tasks"])
    assert facts["migration"]["result"] == "accepted by CSM" and facts["migration"]["csm_note"] == "row 17 is junk"
    assert "accepted by CSM" in h.store.get_tracking(rid)["handoff_summary"]


@pytest.mark.parametrize("script", [[LLMError("anthropic 529")], []])
def test_llm_failure_falls_back_to_static_template(ent, script):
    h, rid = ent
    h.llm.handoff = list(script)
    t = tracker(h)
    for p in PHASES:
        mark(h, phase=p)
    mark(h, "Full import")
    t.sync(rid)  # import check passes, then the same sync completes everything
    facts = t.handoff_facts(h.store.get_run(rid), h.store.get_tracking(rid)["tasks"])
    assert h.store.get_tracking(rid)["handoff_summary"] == static_handoff(facts)
    assert h.status(rid) == "HANDED_OFF"
    assert any(a["step"] == "draft_handoff" and a["status"] == "fallback" for a in h.store.list_audit(rid))


BAD_DRAFT = "Acme Corp (Enterprise) is live; migration passed. Dana Lee signs the $5,000 renewal on December 3."
GOOD_DRAFT = ("Acme Corp finished the Enterprise plan (30-day onboarding, dedicated CSM). All four phases are "
              "complete. Data migration check: passed. Support should watch early adoption.")


@pytest.mark.parametrize("script,expected", [([BAD_DRAFT, BAD_DRAFT], "static"), ([BAD_DRAFT, GOOD_DRAFT], GOOD_DRAFT)])
def test_drafts_failing_fact_checks_are_retried_once_then_fall_back(ent, script, expected):
    h, rid = ent
    h.llm.handoff = list(script)
    t = tracker(h)
    for p in PHASES:
        mark(h, phase=p)
    mark(h, "Full import")
    t.sync(rid)
    summary = h.store.get_tracking(rid)["handoff_summary"]
    facts = t.handoff_facts(h.store.get_run(rid), h.store.get_tracking(rid)["tasks"])
    assert summary == (static_handoff(facts) if expected == "static" else expected)
    assert "$5,000" not in summary and "Dana Lee" not in summary
    assert h.llm.calls["draft_handoff"] == 2
    [blocked, *_] = [a for a in h.store.list_audit(rid) if a["step"] == "check_handoff"]
    assert blocked["status"] == "blocked" and "price" in blocked["rationale"]


def test_handoff_note_failure_escalates_then_retries_without_a_second_llm_call(ent):
    h, rid = ent
    t = tracker(h)
    hand = task(h, "Handoff to support")
    hand["taskName"] = "Renamed by someone"
    for p in PHASES:
        mark(h, phase=p)
    mark(h, "Full import")
    out = t.sync(rid)
    assert out["actions"][-1] == "handoff note write failed: escalated"
    assert h.status(rid) == "COMPLETED" and rid in t.due_runs()
    [esc] = [e for e in h.store.list_escalations("open") if e["run_id"] == rid and e["agent"] == "tracking"]
    hand["taskName"] = "Handoff to support"
    out = t.sync(rid)  # no Rocketlane change, but the pending handoff is retried
    assert out["actions"] == ["handed off to support"] and h.status(rid) == "HANDED_OFF"
    assert h.llm.calls["draft_handoff"] == 1
    assert h.store.escalations[esc["key"]]["status"] == "resolved"


def test_growth_template_is_tracked_with_its_own_task_names(settings):
    h, rid = run(settings, calls=[GROWTH_CALL])
    t = tracker(h)
    t.sync(rid)
    mark(h, "Data import")
    assert t.sync(rid)["actions"] == ["'Data import' completed: migration check passed"]
    for p in PHASES:
        mark(h, phase=p)
    t.sync(rid)
    assert h.status(rid) == "HANDED_OFF" and "Growth" in h.store.get_tracking(rid)["handoff_summary"]


# ---------------- handoff fact check (pure) ----------------

FACTS = {"customer_name": "Acme Corp", "plan": "Enterprise", "duration_days": 30, "csm_model": "dedicated",
         "tasks_done": 16, "tasks_total": 16,
         "phases": [{"name": "Go-Live", "tasks": [{"name": "Handoff to support", "completed": True,
                                                    "due_date": "2026-12-22"}]}],
         "dates": ["2026-12-22"], "migration": {"result": "passed", "last_check_at": None, "failed_checks": [],
                                                 "csm_note": None}}
OK = ("Acme Corp completed the Enterprise plan, 30-day onboarding with a dedicated CSM. All four phases are "
      "complete. Data migration check result: passed. Handoff to support was due 2026-12-22.")


def test_check_handoff_accepts_a_faithful_summary():
    assert check_handoff(OK, FACTS) == []


@pytest.mark.parametrize("bad,problem", [
    (OK.replace("Acme Corp", "The customer"), "customer name"),
    (OK.replace("Enterprise", "premium"), "tier"),
    (OK.replace("passed", "looks fine"), "migration result"),
    (OK + " Go-live happened on 2026-12-01.", "dates not in the data"),
    (OK + " Renewal on March 3.", "date or day"),
    (OK + " Review it Monday.", "date or day"),
    (OK + " The plan costs $4,000 per month.", "price"),
    (OK + " Contact dana@acme.example.", "email"),
    (OK + " Their champion Dana Whitfield is happy.", "names people"),
    (OK + " It was a 45-day effort.", "duration"),
    (OK + " Later moved to the pooled team.", "CSM model"),
])
def test_check_handoff_blocks_invented_facts(bad, problem):
    assert any(problem in p for p in check_handoff(bad, FACTS)), check_handoff(bad, FACTS)


@pytest.mark.parametrize("result,note", [("passed", None), ("failed", None), ("not run", None),
                                         ("accepted by CSM", "row 17 is junk, see 2026-11-30")])
def test_static_handoff_always_passes_the_fact_check(result, note):
    f = {**FACTS, "migration": {**FACTS["migration"], "result": result, "csm_note": note,
                                "failed_checks": ["duplicates"] if result != "passed" else []}}
    assert check_handoff(static_handoff(f), f) == []


# ---------------- API + scheduler ----------------

def api(h, contacts=lambda: (rows(5), rows(5)), settings=None):
    c = Container(settings=settings or h.settings, store=h.store, runner=h.runner, start_poller=False,
                  registry=h.deps.registry, migration_contacts=contacts, llm=h.llm)
    return TestClient(create_app(c))


def test_sync_endpoint(settings, ent):
    h, rid = ent
    client = api(h)
    assert client.post("/runs/nope/sync").status_code == 404
    h2, no_project = run(settings, calls=[])
    assert api(h2).post(f"/runs/{no_project}/sync").status_code == 404
    mark(h, phase="Kickoff")
    r = client.post(f"/runs/{rid}/sync")
    assert r.status_code == 200 and r.json()["actions"] == ["Kickoff complete: progress posted"]
    tr = client.get(f"/runs/{rid}").json()["tracking"]
    assert tr["current_phase"] == "Data Migration" and tr["percent_done"] == 19  # 3/16
    assert tr["last_synced_at"] and tr["handoff_summary"] is None


def test_sync_endpoint_reports_rocketlane_failure(ent):
    h, rid = ent
    h.world.rocketlane.broken_projects.add(str(project(h)["projectId"]))
    assert api(h).post(f"/runs/{rid}/sync").status_code == 502


def test_run_detail_tracking_is_null_before_first_sync(ent):
    h, rid = ent
    assert api(h).get(f"/runs/{rid}").json()["tracking"] is None


def test_tick_syncs_on_its_interval_and_one_broken_run_does_not_stop_others(settings, ent):
    h, first = ent
    # A second, newer run whose project can't be read (list_runs is newest-first, so it is visited first).
    pid = h.world.rocketlane.create_project({"projectName": "Globex Onboarding (Enterprise, 30-day)",
                                             "customer": {"companyName": "Globex"}})
    h.world.rocketlane.broken_projects.add(pid)
    h.store.upsert_run("broken", status="COMPLETED", rocketlane_project_id=pid, plan_tier="enterprise",
                       customer_name="Globex")
    c = Container(settings=settings.model_copy(update={"tracking_interval_seconds": 600}), store=h.store,
                  runner=h.runner, start_poller=False, registry=h.deps.registry,
                  migration_contacts=lambda: (rows(5), rows(5)), llm=h.llm)
    app = create_app(c)

    def reads():
        return len([x for x in h.store.tool_calls if x["tool"] == "rocketlane_list_tasks"])

    t0 = time.time()
    app.state.tick(now=t0)
    assert h.store.get_tracking(first) is not None, "the broken newer run starved the older one"
    assert any(a["step"] == "sync_failed" for a in h.store.list_audit("broken"))
    n = reads()
    app.state.tick(now=t0 + 60)  # inside the interval: no Rocketlane reads
    assert reads() == n
    app.state.tick(now=t0 + 601)
    assert reads() > n


# ---------------- real Rocketlane client (respx) ----------------

@pytest.fixture
def rl():
    return RocketlaneClient(BASE, "test-key", sleep=lambda s: None)


@respx.mock
def test_list_tasks_normalizes_status_phase_and_due_date(rl):
    route = respx.get(f"{BASE}/tasks").mock(return_value=httpx.Response(200, json={"data": [
        {"taskId": 9, "taskName": "Full import", "status": {"value": 3, "label": "Completed"},
         "phase": {"phaseId": 4, "phaseName": "Data Migration"}, "dueDate": "2026-11-20"},
        {"taskId": 10, "taskName": "Orphan", "status": {"value": 1, "label": "To do"}}],
        "pagination": {"hasMore": False}}))
    assert rl.list_tasks("42") == [
        {"task_id": "9", "name": "Full import", "phase": "Data Migration", "status_value": 3,
         "status_label": "Completed", "completed": True, "due_date": "2026-11-20"},
        {"task_id": "10", "name": "Orphan", "phase": None, "status_value": 1, "status_label": "To do",
         "completed": False, "due_date": None}]
    assert route.calls[0].request.url.params["projectId.eq"] == "42"
    # Live API (2026-11-12): without includeFields=phase every task's phase is null, so no phase ever completes.
    assert route.calls[0].request.url.params["includeFields"] == "phase"


@respx.mock
def test_set_task_status_sends_only_the_status(rl):
    import json
    route = respx.put(f"{BASE}/tasks/9").mock(return_value=httpx.Response(200, json={"taskId": 9}))
    rl.set_task_status("9", 2)
    assert json.loads(route.calls[0].request.content) == {"status": {"value": 2}}


def test_handoff_eval_cases_are_coherent():
    """Offline: each eval case's facts are well formed (the static fallback passes the production check)."""
    import json
    from pathlib import Path
    cases = [json.loads(x) for x in (Path(__file__).parents[1] / "evals/datasets/handoff.jsonl").read_text()
             .splitlines() if x.strip()]
    assert len(cases) >= 6 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert check_handoff(static_handoff(c["facts"]), c["facts"]) == [], c["id"]


def test_handoff_uses_its_own_model_setting_defaulting_to_sonnet():
    """Handoff is one internal document per customer: Sonnet passed 6/6 handoff evals vs Haiku 5-6/6, at ~$0.01
    per customer (DECISIONS.md §2). Everything else stays on LLM_MODEL (Haiku)."""
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.llm_model == "claude-haiku-5-5"
    assert s.llm_handoff_model == "claude-sonnet-5-5"
    assert Settings(_env_file=None, llm_handoff_model="claude-haiku-5-5").llm_handoff_model == "claude-haiku-5-5"


def test_container_wires_the_handoff_model_into_the_tracking_llm():
    import inspect

    from app import container
    src = inspect.getsource(container.build_container)
    assert "llm_handoff_model" in src


# ---------------- Slack progress update (user request: the channel should show steps and progress) ----------------

def _tasks(done_by_phase: dict[str, tuple[int, int]]) -> list[dict]:
    out = []
    for phase, (done, total) in done_by_phase.items():
        out += [{"name": f"{phase} task {i}", "phase": phase, "completed": i < done} for i in range(total)]
    return out


def test_progress_update_shows_bar_counts_phase_checklist_and_next_phase():
    from app.services.tracking import progress_update
    tasks = _tasks({"Kickoff": (3, 3), "Data Migration": (1, 5), "Configuration": (0, 4), "Go-Live": (0, 4)})
    msg = progress_update("Kickoff", tasks)
    assert msg.splitlines()[0] == progress_message("Kickoff")
    assert "4 of 16 tasks done (25%)" in msg
    assert ":white_check_mark: Kickoff" in msg and ":hourglass_flowing_sand: Data Migration" in msg
    assert "Configuration" in msg and "Go-Live" in msg
    assert "Next up: *Data Migration* (1 of 5 tasks done)" in msg


def test_progress_update_on_last_phase_thanks_the_customer():
    from app.services.tracking import progress_update
    msg = progress_update("Go-Live", _tasks({p: (n, n) for p, n in zip(PHASES, (3, 5, 4, 4), strict=True)}))
    assert "16 of 16 tasks done (100%)" in msg and "Next up" not in msg and "Thank you" in msg


@pytest.mark.parametrize("phase", PHASES)
def test_progress_update_never_leaks_task_names_or_internal_words(phase):
    from app.services.tracking import progress_update
    tasks = _tasks({p: (2, 3) for p in PHASES}) + [
        {"name": "Internal: escalate Acme's failed duplicate export", "phase": "Data Migration", "completed": False}]
    msg = progress_update(phase, tasks).lower()
    assert "internal" not in msg and not any(w in msg for w in INTERNAL_WORDS)


def test_phase_completion_posts_the_full_progress_update(ent):
    h, rid = ent
    for t in project(h)["tasks"]:
        if (t.get("phase") or {}).get("phaseName") == "Kickoff":
            t["status"] = {"value": 3, "label": "Completed"}
    tracker(h).sync(rid)
    [msg] = slack_messages(h)
    assert msg.startswith(progress_message("Kickoff")) and "tasks done" in msg and "Next up: *Data Migration*" in msg


def test_periodic_sync_without_changes_adds_no_audit_rows(ent):
    """A sync every 60 s used to add a tool-call row to the activity feed each time (live, Initech run)."""
    h, rid = ent
    t = tracker(h)
    t.sync(rid)
    before = len([r for r in h.store.audit_rows if r["run_id"] == rid])
    t.sync(rid)
    t.sync(rid)
    assert len([r for r in h.store.audit_rows if r["run_id"] == rid]) == before


# ---------------- internal #cs-handoffs channel ----------------

def _with_handoff_channel(h, channel_id="CHANDOFF"):
    h.world.slack.channels[channel_id] = {"name": "cs-handoffs", "topic": "", "members": [], "shared_invites": [],
                                          "messages": []}
    h.settings = h.settings.model_copy(update={"slack_handoff_channel": channel_id})
    return h.world.slack.channels[channel_id]["messages"]


def test_handoff_summary_is_also_posted_to_the_internal_handoff_channel(ent):
    h, rid = ent
    internal = _with_handoff_channel(h)
    t = tracker(h)
    finish_all_but_go_live(h, t, rid)
    mark(h, phase="Go-Live")
    t.sync(rid)
    summary = h.store.get_tracking(rid)["handoff_summary"]
    [post] = internal
    assert "Acme Corp" in post and summary in post and "Enterprise" in post
    customer = h.world.slack.channels[h.store.get_run(rid)["slack_channel_id"]]["messages"]
    assert all(summary not in m for m in customer)  # never in the customer-shared channel
    t.sync(rid)
    assert len(internal) == 1  # once


def test_handoff_channel_equal_to_the_customer_channel_is_refused(ent):
    h, rid = ent
    customer_channel = h.store.get_run(rid)["slack_channel_id"]
    h.settings = h.settings.model_copy(update={"slack_handoff_channel": customer_channel})
    t = tracker(h)
    finish_all_but_go_live(h, t, rid)
    mark(h, phase="Go-Live")
    t.sync(rid)
    summary = h.store.get_tracking(rid)["handoff_summary"]
    assert all(summary not in m for m in slack_messages(h))
    assert h.status(rid) == "HANDED_OFF"  # the Rocketlane handoff still happened


def test_failed_internal_post_does_not_block_the_handoff(ent):
    h, rid = ent
    h.settings = h.settings.model_copy(update={"slack_handoff_channel": "CMISSING"})  # post will fail
    t = tracker(h)
    finish_all_but_go_live(h, t, rid)
    mark(h, phase="Go-Live")
    t.sync(rid)
    assert h.status(rid) == "HANDED_OFF"
    assert any(a["step"] == "handoff_slack" and a["status"] != "ok" for a in h.store.audit_rows if a["run_id"] == rid)


def test_failed_internal_post_is_retried_on_a_later_sync_exactly_once(ent):
    h, rid = ent
    internal = _with_handoff_channel(h)
    real_post = h.world.slack.post

    def down(channel_id, text):
        if channel_id == "CHANDOFF":
            raise PermanentToolError("channel_not_found")  # Slack outage for the internal channel only
        return real_post(channel_id, text)

    h.world.slack.post = down
    t = tracker(h)
    finish_all_but_go_live(h, t, rid)
    mark(h, phase="Go-Live")
    t.sync(rid)
    assert h.status(rid) == "HANDED_OFF" and internal == []
    assert rid in t.due_runs(), "a handed-off run with a pending internal post must still be synced"
    h.world.slack.post = real_post  # Slack is back
    t.sync(rid)
    assert len(internal) == 1 and h.store.get_tracking(rid)["handed_off_at"]
    t.sync(rid)
    assert len(internal) == 1 and rid not in t.due_runs()  # posted once, then no longer tracked



# ---------------- Agent 2 owns every customer-facing message ----------------

def test_phase_progress_is_posted_by_the_communication_agent(ent):
    h, rid = ent
    mark(h, phase="Kickoff")
    tracker(h).sync(rid)
    posts = [r for r in h.store.audit_rows if r["run_id"] == rid and r["step"] == "tool:slack_post_message"]
    assert posts and all(r["agent"] == "comms" for r in posts)


def test_internal_post_tool_refuses_any_customer_channel(ent):
    from app.tools.registry import ToolFailed
    h, rid = ent
    customer_channel = h.store.get_run(rid)["slack_channel_id"]
    with pytest.raises(ToolFailed):
        h.deps.registry.call("tracking", "slack_post_internal", {"channel_id": customer_channel, "text": "x"},
                             run_id=rid)


@pytest.mark.parametrize("text", ["Migration check failed: duplicates", "Escalated to the CSM",
                                  "Handoff summary: ...", "Your plan is $48k ARR", "We reopened the task"])
def test_customer_message_guard_blocks_internal_content(text):
    from app.guardrails.output import check_customer_message
    assert check_customer_message(text)


@pytest.mark.parametrize("phase", PHASES)
def test_customer_message_guard_allows_progress_updates(phase):
    from app.guardrails.output import check_customer_message
    from app.services.tracking import progress_update
    tasks = [{"name": f"t{i}", "phase": p, "completed": i % 2 == 0} for i, p in enumerate(PHASES * 3)]
    assert check_customer_message(progress_update(phase, tasks)) == []
