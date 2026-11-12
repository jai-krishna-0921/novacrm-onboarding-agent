"""GET /agents/status: per-agent status derived from heartbeats (kv) and audit/tool-call rows, plus cached
integration health. Reads only; never calls an external system."""

import time
from datetime import datetime

from app.config import ROCKETLANE_AUTOMATIONS, Settings
from app.services.health import iso
from app.services.store import Store

TICK_SECONDS = 5     # scheduler loop (main.tick_loop)
POLL_SECONDS = 20    # Gmail polling fallback (main.poll_loop)
STALE_FACTOR = 3     # a loop is stalled once its heartbeat is older than 3 intervals

HB_TICK, HB_SYNC, HB_PUSH, HB_TRACKING = "hb:scheduler_tick", "hb:inbox_sync", "hb:gmail_push", "hb:tracking_sync"
GMAIL_WATCH = "gmail_watch_expires_at"

INTAKE_BUSY = {"RECEIVED", "AWAITING_CALL", "AWAITING_RETRY", "TIER_CONFIRMED"}
COMMS_BUSY = {"PROJECT_CREATED"}
TRACKING_SKIP = {"CANCELLED", "FAILED"}  # same as tracking.SKIP_STATUSES

AGENTS = {
    "intake": ("Intake & Routing", "Reads deal emails, checks the sender, phones the AE to confirm the tier, creates "
                                   "the Rocketlane project from the tier template."),
    "comms": ("Communication", "Creates the customer Slack channel and posts the personalised welcome."),
    "tracking": ("Project Tracking", "Syncs Rocketlane tasks, posts phase progress, runs migration checks, reopens "
                                     "unverified tasks and writes the CSM handoff."),
}


def beat(store: Store, key: str, ts: float | None = None) -> None:
    store.set_kv(key, iso(time.time() if ts is None else ts))


def _ts(v: str | None) -> float | None:
    try:
        return datetime.fromisoformat(v).timestamp() if v else None
    except ValueError:
        return None


def stale(last: str | None, interval: float, now: float, started_at: float) -> bool:
    """Older than STALE_FACTOR intervals; a loop that never beat counts once the app is that old."""
    t = _ts(last)
    return now - (t if t is not None else started_at) > STALE_FACTOR * interval


def _next(last: str | None, interval: float) -> str | None:
    t = _ts(last)
    return iso(t + interval) if t is not None else None


def build_status(store: Store, s: Settings, integrations: list[dict], *, now: float, started_at: float,
                 inbox_enabled: bool) -> dict:
    kv = {k: store.get_kv(k) for k in (HB_TICK, HB_SYNC, HB_PUSH, HB_TRACKING, GMAIL_WATCH)}
    today = iso(now)[:10] + "T00:00:00+00:00"
    act = store.activity_stats(today)
    steps, tools, tools_today = act["steps"], act["tools"], act["tools_since"]

    def tool_n(counts: dict, tool: str, agent: str | None = None) -> int:
        return sum(n for (a, t, st), n in counts.items() if t == tool and st == "ok" and agent in (None, a))

    def step_n(agent: str, step: str, statuses: tuple = ("ok",)) -> int:
        return sum(steps.get((agent, step, st), 0) for st in statuses)

    bad = {i["name"] for i in integrations if i.get("checked_at") and not i["ok"]}
    scheduler_stalled = stale(kv[HB_TICK], TICK_SECONDS, now, started_at)
    trackable = store.list_trackable_runs(TRACKING_SKIP)

    def status(down: bool, degraded: bool, busy: bool) -> str:
        return "down" if down else "degraded" if degraded else "running" if busy else "idle"

    rows = {
        "intake": dict(
            status=status(inbox_enabled and stale(kv[HB_SYNC], POLL_SECONDS, now, started_at),
                          scheduler_stalled or bool(bad & {"gmail", "retell", "anthropic"}),
                          bool(store.list_runs_by_status(INTAKE_BUSY))),
            stats={"emails_today": act["runs_since"], "calls_today": tool_n(tools_today, "voice_start_call"),
                   "projects_today": tool_n(tools_today, "rocketlane_create_project")},
            next_run_at=_next(kv[HB_SYNC], POLL_SECONDS) if inbox_enabled else None),
        "comms": dict(
            status=status(False, bool(bad & {"slack", "anthropic"}), bool(store.list_runs_by_status(COMMS_BUSY))),
            stats={"channels_created": step_n("comms", "setup_channel", ("ok", "degraded"))},
            next_run_at=None),
        "tracking": dict(
            status=status(bool(trackable) and stale(kv[HB_TRACKING], s.tracking_interval_seconds, now, started_at),
                          "rocketlane" in bad, bool(trackable)),
            stats={"projects_tracked": len(trackable), "progress_posts": step_n("tracking", "phase_complete"),
                   "migration_checks": step_n("migration", "migration_check", ("ok", "escalated")),
                   "reopens": tool_n(tools, "rocketlane_set_task_status", "tracking"),
                   "handoffs": step_n("tracking", "handoff")},
            next_run_at=_next(kv[HB_TRACKING], s.tracking_interval_seconds)),
    }
    agents = []
    for aid, (name, description) in AGENTS.items():
        last = act["last"].get(aid)
        agents.append({"id": aid, "name": name, "description": description, **rows[aid],
                       "last_activity_at": last["ts"] if last else None,
                       "last_activity": f"{last['step']}: {last['rationale']}"[:300] if last else None})
    return {
        "agents": agents,
        "scheduler": {"last_tick_at": kv[HB_TICK], "interval_seconds": TICK_SECONDS,
                      "status": "stalled" if scheduler_stalled else "running"},
        "gmail": {"last_sync_at": kv[HB_SYNC], "last_push_at": kv[HB_PUSH], "watch_expires_at": kv[GMAIL_WATCH]},
        "integrations": integrations,
        "rocketlane_automations": [dict(a) for a in ROCKETLANE_AUTOMATIONS],
    }
