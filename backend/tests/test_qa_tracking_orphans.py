"""QA (pre-demo): rule e flags must never outlive tracking. A handed-off run is never synced again, so a
"tracking" escalation still open at handoff can neither clear itself (rule e) nor be closed by hand
(ALLOWED["tracking"] = set(), no dismiss endpoint): it sits in Needs a human / escalations_open forever."""

from test_tracking import finish_all_but_go_live, mark, project, tracker

from app.providers.fake import TODO


def open_flags(h, rid):
    return [e for e in h.store.list_escalations("open") if e["run_id"] == rid and e.get("kind") == "tracking"]


def test_blocked_go_live_phase_is_not_left_open_forever_by_the_handoff(ent):
    h, rid = ent
    t = tracker(h)
    finish_all_but_go_live(h, t, rid)
    h.world.rocketlane.set_phase_status(project(h)["projectId"], "Go-Live", "Blocked")
    mark(h, phase="Go-Live")
    t.sync(rid)
    handed_off = h.status(rid) == "HANDED_OFF"
    # Either the open flag holds the handoff, or the handoff closes it. Not both "handed off" and "open".
    assert not (handed_off and open_flags(h, rid)), (
        f"run HANDED_OFF with open tracking flag(s) that nothing can ever clear: "
        f"{[e['reason'] for e in open_flags(h, rid)]}")


def test_blocked_task_in_an_unknown_phase_is_not_left_open_forever_by_the_handoff(ent):
    h, rid = ent
    p = project(h)
    p["tasks"].append({"taskId": 999001, "taskName": "Security review", "phase": {"phaseName": "Internal"},
                       "status": {"value": 4, "label": "Blocked"}, "dueDate": None, "taskPrivateNote": ""})
    t = tracker(h)
    finish_all_but_go_live(h, t, rid)
    assert open_flags(h, rid), "precondition: the Blocked task is flagged"
    mark(h, phase="Go-Live")
    t.sync(rid)
    assert not (h.status(rid) == "HANDED_OFF" and open_flags(h, rid)), (
        "run HANDED_OFF while a Blocked-task flag is open; it is never synced again so the flag never clears")


def test_cancelled_run_does_not_keep_an_unclearable_tracking_flag(ent):
    h, rid = ent
    t = tracker(h)
    mark(h, "Kickoff call", status={"value": 4, "label": "Blocked"})
    t.sync(rid)
    assert open_flags(h, rid)
    h.store.upsert_run(rid, status="CANCELLED")
    mark(h, "Kickoff call", status=TODO)       # fixed in Rocketlane, but a cancelled run is never synced
    assert rid not in t.due_runs()
    assert open_flags(h, rid) == [], "CANCELLED run keeps an open tracking flag no endpoint can close"
