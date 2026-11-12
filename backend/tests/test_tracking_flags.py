"""Agent 3 watches what people do in the Rocketlane UI beyond ticking tasks (verified live 2026-11-12):
- a phase marked Completed while its tasks are still open (Rocketlane allows it and leaves the tasks open) is
  flagged to the team, never announced to the customer;
- a task or phase set to Blocked is flagged to the team;
both clear on their own once fixed in Rocketlane. Nothing about either reaches the customer channel."""

import pytest
from test_tracking import INTERNAL_WORDS, mark, project, tracker
from test_workflow import GROWTH_CALL, run

from app.graph.root import validate_resolution

BLOCKED = {"value": 4, "label": "Blocked"}


@pytest.fixture
def growth(settings):
    s = settings.model_copy(update={"slack_handoff_channel": "CINTERNAL"})
    h, rid = run(s, calls=[GROWTH_CALL])
    h.world.slack.channels["CINTERNAL"] = {"name": "cs-handoffs", "messages": [], "members": set()}
    assert h.status(rid) == "COMPLETED"
    return h, rid


def headlines(h):
    """First line of each post in the customer channel (the welcome excluded)."""
    [ch] = [c for cid, c in h.world.slack.channels.items() if cid != "CINTERNAL"]
    return [m.splitlines()[0] for m in ch["messages"][1:]]


def flags(h, rid, status="open"):
    return [e for e in h.store.list_escalations(status) if e["run_id"] == rid and e.get("kind") == "tracking"]


def internal(h):
    return h.world.slack.channels["CINTERNAL"]["messages"]


def set_phase(h, phase, label):
    h.world.rocketlane.set_phase_status(project(h)["projectId"], phase, label)


def test_phase_marked_completed_with_open_tasks_is_flagged_not_announced(growth):
    h, rid = growth
    t = tracker(h)
    t.sync(rid)
    set_phase(h, "Kickoff", "Completed")
    out = t.sync(rid)
    assert headlines(h) == []                                   # the customer is not told Kickoff is done
    [f] = flags(h, rid)
    assert "Kickoff" in f["reason"] and "still open" in f["reason"]
    assert any("Kickoff" in m for m in internal(h))             # the team hears about it
    assert any("Kickoff" in a for a in out["actions"])
    t.sync(rid)
    assert len(flags(h, rid)) == 1 and len(internal(h)) == 1    # once, not every sync


def test_flag_clears_when_the_tasks_get_done_and_the_phase_is_announced_once(growth):
    h, rid = growth
    t = tracker(h)
    t.sync(rid)
    set_phase(h, "Kickoff", "Completed")
    t.sync(rid)
    mark(h, phase="Kickoff")
    t.sync(rid)
    assert flags(h, rid) == [] and flags(h, rid, "resolved")
    assert headlines(h) == [":white_check_mark: The *Kickoff* phase of your NovaCRM onboarding is complete."]


def test_flag_clears_when_the_phase_is_reopened(growth):
    h, rid = growth
    t = tracker(h)
    set_phase(h, "Kickoff", "Completed")
    t.sync(rid)
    set_phase(h, "Kickoff", "To do")
    t.sync(rid)
    assert flags(h, rid) == [] and headlines(h) == []


def test_phase_marked_completed_after_its_tasks_is_fine(growth):
    h, rid = growth
    t = tracker(h)
    mark(h, phase="Kickoff")
    set_phase(h, "Kickoff", "Completed")
    t.sync(rid)
    assert flags(h, rid, None) == [] and len(headlines(h)) == 1


@pytest.mark.parametrize("what", ["task", "phase"])
def test_blocked_task_or_phase_is_flagged_and_clears_when_unblocked(growth, what):
    h, rid = growth
    t = tracker(h)
    t.sync(rid)
    if what == "task":
        mark(h, "Data export", status=BLOCKED)
    else:
        set_phase(h, "Configuration", "Blocked")
    t.sync(rid)
    [f] = flags(h, rid)
    assert "blocked" in f["reason"].lower() and ("Data export" in f["reason"] or "Configuration" in f["reason"])
    assert len(internal(h)) == 1
    if what == "task":
        mark(h, "Data export", status={"value": 2, "label": "In progress"})
    else:
        set_phase(h, "Configuration", "In Progress")
    t.sync(rid)
    assert flags(h, rid) == []


def test_reflag_after_clearing_posts_again(growth):
    h, rid = growth
    t = tracker(h)
    for _ in range(2):
        mark(h, "Data export", status=BLOCKED)
        t.sync(rid)
        mark(h, "Data export", status={"value": 1, "label": "To do"})
        t.sync(rid)
    assert len(internal(h)) == 2 and len(flags(h, rid, "resolved")) == 2


def test_nothing_about_flags_reaches_the_customer_channel(growth):
    h, rid = growth
    t = tracker(h)
    set_phase(h, "Kickoff", "Completed")
    mark(h, "Data export", status=BLOCKED)
    set_phase(h, "Go-Live", "Blocked")
    t.sync(rid)
    channel = h.store.get_run(rid)["slack_channel_id"]
    customer = h.world.slack.channels[channel]["messages"][1:]
    assert customer == []
    assert not any(w in m.lower() for m in customer for w in INTERNAL_WORDS)
    assert len(flags(h, rid)) == 3


def test_phase_read_failure_does_not_break_the_task_rules(growth):
    h, rid = growth
    t = tracker(h)
    h.world.rocketlane.phases_down = True
    mark(h, phase="Kickoff")
    out = t.sync(rid)
    assert len(headlines(h)) == 1 and flags(h, rid) == []
    assert any("phase" in a.lower() for a in out["actions"]) or out["changed"]


def test_flags_are_never_resolved_from_the_dashboard():
    """They clear only when fixed in Rocketlane: no action can close one by hand, and none touches the graph."""
    with pytest.raises(ValueError):
        validate_resolution({"action": "retry"}, {"escalation_kind": "tracking"})


def test_handoff_waits_for_an_open_flag_and_goes_ahead_once_it_is_fixed(growth):
    h, rid = growth
    t = tracker(h)
    from app.services.tracking import PHASES
    for p in PHASES[:-1]:
        mark(h, phase=p)
    t.sync(rid)
    set_phase(h, "Go-Live", "Blocked")
    mark(h, phase="Go-Live")
    t.sync(rid)
    assert h.status(rid) != "HANDED_OFF" and flags(h, rid)
    set_phase(h, "Go-Live", "Completed")
    t.sync(rid)
    assert h.status(rid) == "HANDED_OFF" and flags(h, rid) == []
