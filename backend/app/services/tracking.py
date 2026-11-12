"""Agent 3 - Project tracking: a deterministic watcher over each run's Rocketlane project.

Each sync reads the tasks, diffs them against the stored snapshot and applies fixed rules (code decides):
  a. a known phase becomes fully completed -> one template progress line in the customer Slack channel
  b. the import task becomes completed     -> run the migration check (services/migration_check.py)
  c. "Migration verified" becomes completed without a passing check (and no CSM dismissal)
                                           -> reopen it, note why, open/refresh a "migration" escalation
  d. every Go-Live task completed          -> LLM-drafted, code-checked handoff summary on the
                                              "Handoff to support" task's private note; run handed off
  e. a person's change in Rocketlane that needs a look (a phase marked Completed while its tasks are open,
     a task or phase set to Blocked)    -> "tracking" escalation + one internal Slack post; it clears itself
                                           once fixed in Rocketlane. The customer is never told.
The LLM only drafts (d). Nothing internal (escalations, migration defects, the handoff) goes to Slack.
Overdue nudges are a native Rocketlane automation and are not duplicated here.
"""

import html
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.config import Settings, Tier, tier_rules
from app.guardrails.output import check_customer_message, check_handoff
from app.llm import versions as prompts
from app.llm.client import LLMError
from app.llm.schemas import HandoffDraft
from app.services.audit import audit
from app.services.customer_comms import post_customer_update
from app.services.migration_check import TASK_PREFIX as MIGRATION_VERIFIED
from app.services.migration_check import MigrationDataUnavailable, run_migration_check
from app.services.store import Store, now
from app.tools.registry import ToolFailed, ToolRegistry

AGENT = "tracking"
# CS-owned template structure (docs/ROCKETLANE_SETUP.md). Only these phases are ever announced to the customer.
PHASES = ["Kickoff", "Data Migration", "Configuration", "Go-Live"]
GO_LIVE = "Go-Live"
IMPORT_TASKS = ("full import", "data import")  # Enterprise / Growth import task names (exact, case-insensitive)
HANDOFF_TASK = "Handoff to support"
# Used to reopen when there is no earlier snapshot to restore from. UNVERIFIED default: "To do" is value 1 on
# this workspace (seen on live tasks), but Rocketlane does not document the status list.
DEFAULT_OPEN_STATUS = 1
ACCEPTED = {"passed", "accepted by CSM"}  # migration states that allow "Migration verified" and the handoff
SKIP_STATUSES = {"CANCELLED", "FAILED"}


def progress_message(phase: str) -> str:
    """Fixed, customer-safe headline. Only called with a name from PHASES."""
    PHASES.index(phase)
    return f":white_check_mark: The *{phase}* phase of your NovaCRM onboarding is complete."


COMPLETED_PHASE = {"completed", "done"}
STARTED = {"in progress", "in-progress", "started"}


def _label(x: str | None) -> str:
    return (x or "").strip().lower()


def _customer_safe(name: str) -> bool:
    """Template task names are CS-owned, but some are internal ("Internal sales-to-CS handoff review", "Handoff to
    support"). A name is only shown to the customer if the same check Agent 2 applies to every post accepts it."""
    return 0 < len(name) <= 70 and not check_customer_message(name)


def _due(t: dict) -> str:
    d = t.get("due_date") or ""
    try:
        return f" (due {date.fromisoformat(d[:10]).strftime('%-d %b')})" if d else ""
    except ValueError:
        return ""


def _names(ts: list[dict], with_due: bool = False, limit: int = 4) -> str:
    shown = [t["name"] + (_due(t) if with_due else "") for t in ts if _customer_safe(t["name"])]
    extra = len(shown) - limit
    return " \u00b7 ".join(shown[:limit]) + (f" and {extra} more" if extra > 0 else "")


def _phase_complete(tasks: list[dict], phase: str, statuses: dict[str, str]) -> bool:
    return _phase_done(tasks, phase) or _label(statuses.get(phase)) in COMPLETED_PHASE


def _phase_started(tasks: list[dict], phase: str, statuses: dict[str, str]) -> bool:
    mine = [t for t in tasks if t.get("phase") == phase]
    return _label(statuses.get(phase)) in STARTED or any(
        t["completed"] or _label(t.get("status_label")) in STARTED for t in mine)


def _top(tasks: list[dict], phase: str) -> list[dict]:
    return [t for t in tasks if t.get("phase") == phase and not t.get("parent")]


def _status_line(tasks: list[dict], statuses: dict[str, str]) -> str:
    def icon(p: str) -> str:
        if _phase_complete(tasks, p, statuses):
            return ":white_check_mark:"
        return ":hourglass_flowing_sand:" if _phase_started(tasks, p, statuses) else ":white_circle:"
    return "   ".join(f"{icon(p)} {p}" for p in PHASES)


def progress_update(phase: str, tasks: list[dict], statuses: dict[str, str] | None = None) -> str:
    """Headline + what was done + overall progress bar + phase checklist + what's next (with due dates).
    The channel is shared with the customer: task names pass the customer-message check or are left out."""
    statuses = statuses or {}
    ours = [t for t in tasks if t.get("phase") in PHASES]
    done, total = sum(bool(t["completed"]) for t in ours), len(ours)
    pct = round(100 * done / total) if total else 0
    bar = "\u2593" * round(pct / 10) + "\u2591" * (10 - round(pct / 10))
    lines = [progress_message(phase)]
    if finished := _names([t for t in _top(tasks, phase) if t["completed"]]):
        lines.append(f"Done in this phase: {finished}")
    lines += [f"`{bar}` {done} of {total} tasks done ({pct}%)", _status_line(tasks, statuses)]
    i = PHASES.index(phase)
    if i + 1 < len(PHASES):
        nxt = PHASES[i + 1]
        upcoming = _names([t for t in _top(tasks, nxt) if not t["completed"]], with_due=True, limit=3)
        lines.append(f"Next up: *{nxt}*" + (f": {upcoming}" if upcoming else "."))
    else:
        lines.append("All phases are complete. Thank you for working through it with us!")
    return "\n".join(lines)


def phase_started(phase: str, tasks: list[dict]) -> str:
    """Once per phase, when it (or its first task) is under way: what the phase covers and what is moving."""
    PHASES.index(phase)
    top = _top(tasks, phase)
    lines = [f":arrow_forward: The *{phase}* phase of your NovaCRM onboarding has started."]
    if covers := _names(top, with_due=True):
        lines.append(f"In this phase: {covers}")
    if moving := _names([t for t in top if _label(t.get("status_label")) in STARTED]):
        lines.append(f"Under way now: {moving}")
    waiting = [t for t in top if not t["completed"] and t.get("depends_on") and _customer_safe(t["name"])
               and all(_customer_safe(d) for d in t["depends_on"])]
    if waiting:
        lines.append("Next in line: " + " \u00b7 ".join(
            f"{t['name']} (after {', '.join(t['depends_on'][:2])})" for t in waiting[:2]))
    subs = sum(1 for t in tasks if t.get("phase") == phase and t.get("parent"))
    if subs:
        lines.append(f"Broken down into {subs} smaller step{'s' if subs != 1 else ''} as well.")
    return "\n".join(lines)


def static_handoff(f: dict) -> str:
    """Deterministic fallback; must pass check_handoff (tested)."""
    m = f["migration"]
    failed = f" Failed checks: {', '.join(m['failed_checks'])}." if m["failed_checks"] else ""
    note = f" CSM note: {m['csm_note']}." if m.get("csm_note") else ""
    watch = {"passed": "No open migration issues at handoff.",
             "accepted by CSM": "Watch for data issues: the CSM accepted the migration without a clean check."
             }.get(m["result"], "Watch for data issues: the migration has no clean check.")
    done = [p["name"] for p in f["phases"] if p["tasks"] and all(t["completed"] for t in p["tasks"])]
    open_ = [p["name"] for p in f["phases"] if p["tasks"] and p["name"] not in done]
    phases = f"Phases complete: {', '.join(done) or 'none'}" + (f"; still open: {', '.join(open_)}" if open_ else "")
    return (f"{f['customer_name']} completed the {f['plan']} plan, {f['duration_days']}-day onboarding with a "
            f"{f['csm_model']} CSM. {phases} ({f['tasks_done']}/{f['tasks_total']} tasks completed). "
            f"Data migration check result: {m['result']}.{failed}{note} {watch}")


def handoff_user(facts: dict) -> str:
    """The LLM's user turn (shared with evals so they test what ships). Task data is untrusted."""
    return f"<project_data>\n{json.dumps(facts, ensure_ascii=False)}\n</project_data>"


def _phase_done(tasks: list[dict], phase: str) -> bool:
    mine = [t for t in tasks if t.get("phase") == phase]
    return bool(mine) and all(t["completed"] for t in mine)


def _load(raw: str | None) -> dict:
    try:
        v = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _find(tasks: list[dict], match: Callable[[str], bool]) -> dict | None:
    return next((t for t in tasks if match(t["name"].strip().lower())), None)


def _is_mv(name: str) -> bool:
    return name.startswith(MIGRATION_VERIFIED.lower())


def tracking_view(store: Store, run_id: str) -> dict | None:
    """Dashboard fields for GET /runs/{id}."""
    t = store.get_tracking(run_id)
    if not t:
        return None
    tasks = t.get("tasks") or []
    statuses = _load(store.get_kv(f"phases:{run_id}"))
    names = PHASES + [p for p in dict.fromkeys(x.get("phase") for x in tasks) if p and p not in PHASES]

    def phase(p: str) -> dict:
        mine = [x for x in tasks if x.get("phase") == p]
        return {"name": p, "done": sum(x["completed"] for x in mine), "total": len(mine),
                "in_progress": sum(not x["completed"] and _label(x.get("status_label")) in STARTED for x in mine),
                "blocked": sum(_label(x.get("status_label")) == "blocked" for x in mine),
                "status": statuses.get(p) or None,
                "complete": _phase_complete(tasks, p, statuses), "started": _phase_started(tasks, p, statuses)}

    phases = [p for p in map(phase, names) if p["total"]]
    done = sum(x["completed"] for x in tasks)
    return {"current_phase": next((p["name"] for p in phases if not p["complete"]), None),
            "percent_done": round(100 * done / len(tasks)) if tasks else 0, "tasks_done": done,
            "tasks_in_progress": sum(p["in_progress"] for p in phases), "tasks_total": len(tasks),
            "phases": phases, "last_synced_at": t.get("synced_at"),
            "tasks": [{k: x.get(k) for k in ("name", "phase", "status_label", "completed", "due_date", "parent",
                                              "depends_on")} for x in tasks],
            "handoff_summary": t.get("handoff_summary"), "handed_off_at": t.get("handed_off_at")}


@dataclass
class Tracker:
    settings: Settings
    store: Store
    registry: ToolRegistry
    llm: Any = None                                   # LLM; None -> static handoff
    migration_contacts: Callable[[], tuple] | None = None

    # ---------- which runs ----------
    def due_runs(self) -> list[str]:
        # Every live, not-handed-off project, however old (never "the newest N runs").
        ids = [run["id"] for run in self.store.list_trackable_runs(SKIP_STATUSES)]
        # Rule-e flags on a run that is no longer tracked (cancelled, ...) can't clear themselves: close them here.
        live = set(ids)
        for e in self.store.list_escalations("open"):
            if e.get("kind") == "tracking" and e["run_id"] not in live:
                self.store.upsert_escalation(e["key"], dict(status="resolved", resolved_by="tracking",
                                                          resolution={"action": "closed", "by": "tracking",
                                                                      "note": "run is no longer tracked"}))
                audit(self.store, e["run_id"], AGENT, "flag_cleared",
                      rationale=f"run is no longer tracked; closed: {e['reason']}")
        return ids

    # ---------- one sync ----------
    def sync(self, run_id: str) -> dict:
        """Caller guarantees the run exists and has a project. Raises ToolFailed if the tasks can't be read."""
        run = self.store.get_run(run_id) or {}
        prev = self.store.get_tracking(run_id)
        if prev and prev.get("handed_off_at"):
            if prev.get("handoff_slack") == "pending":  # the internal #cs-handoffs post failed: retry (idempotent)
                self._post_internal_handoff(run, prev.get("handoff_summary") or "")
            return {"run_id": run_id, "changed": False, "actions": [], "skipped": "already handed off"}
        listed = self.registry.call(AGENT, "rocketlane_list_tasks", {"project_id": run["rocketlane_project_id"]},
                                    run_id=run_id, rationale="periodic project sync", audit_success=False)
        tasks = sorted((t.model_dump() for t in listed.tasks), key=lambda t: t["task_id"])  # order-independent diff
        before = {t["task_id"]: t for t in (prev or {}).get("tasks") or []}
        changed = prev is None or sorted(map(_sig, tasks)) != sorted(map(_sig, prev.get("tasks") or []))
        became_done = [t for t in tasks if t["completed"] and not before.get(t["task_id"], {}).get("completed")]
        actions: list[str] = []
        # Phase statuses a person sets in Rocketlane's phase menu; on a failed read keep the last known ones.
        prev_statuses = _load(self.store.get_kv(f"phases:{run_id}"))
        phases = self._read_phases(run)
        statuses = {p.name: p.status_label for p in phases} if phases is not None else prev_statuses
        if phases is not None and statuses != prev_statuses:
            self.store.set_kv(f"phases:{run_id}", json.dumps(statuses))
            changed = True

        # b. import completed -> migration check (before c, so c sees a fresh result)
        if imp := _find(became_done, lambda n: n in IMPORT_TASKS):
            actions.append(self._auto_migration_check(run_id, imp))
        # c. "Migration verified" ticked without a passing check -> reopen
        if mv := _find(became_done, _is_mv):
            actions.append(self._guard_migration_verified(run_id, run["rocketlane_project_id"], mv,
                                                          before.get(mv["task_id"])))
        # a. phase completion (after c: a reopened task un-completes Data Migration): every task done, or the
        # phase marked Completed by a person in Rocketlane (CS owns the process). Data Migration marked Completed
        # without a passing migration check is never announced (rule e flags it instead).
        # State-based: a complete phase whose post never succeeded (no idempotency record) is retried each sync.
        for phase in PHASES:
            if not self._may_announce(run_id, tasks, phase, statuses):
                continue
            newly = not _phase_complete(list(before.values()), phase, prev_statuses)
            if newly or self._unposted(run, phase):
                actions.append(self._announce(run, phase, tasks, statuses))
        # a2. phase started (once): the phase or one of its tasks is under way
        for phase in PHASES:
            if (_phase_started(tasks, phase, statuses) and not _phase_complete(tasks, phase, statuses)
                    and self._unposted(run, phase, "start") and self._unposted(run, phase)):
                actions.append(self._announce_start(run, phase, tasks))

        # e. flags from the phase menu / Blocked status (after a, so a phase whose tasks just got done is announced)
        actions += self._reconcile_flags(run, tasks, phases)

        self.store.upsert_tracking(run_id, tasks=tasks, synced_at=now())
        # d. handoff: state-based so a failed note write is retried on the next sync
        if (a := self._maybe_handoff(run, tasks, changed)) is not None:
            actions.append(a)
        if changed or actions:
            audit(self.store, run_id, AGENT, "sync", rationale="; ".join(actions) or "tasks changed; no rule fired",
                  output={"tasks_done": sum(t["completed"] for t in tasks), "tasks_total": len(tasks),
                          "became_completed": [t["name"] for t in became_done]})
        return {"run_id": run_id, "changed": changed, "actions": actions, "tracking": tracking_view(self.store, run_id)}

    # ---------- rule a ----------
    def _read_phases(self, run: dict) -> list | None:
        try:
            return self.registry.call(AGENT, "rocketlane_list_phases", {"project_id": run["rocketlane_project_id"]},
                                      run_id=run["id"], rationale="read phase statuses set in Rocketlane",
                                      audit_success=False).phases
        except ToolFailed:
            return None

    def _may_announce(self, run_id: str, tasks: list[dict], phase: str, statuses: dict[str, str]) -> bool:
        if _phase_done(tasks, phase):
            return True
        if _label(statuses.get(phase)) not in COMPLETED_PHASE:
            return False
        return phase != "Data Migration" or self.migration_state(run_id)[0] in ACCEPTED

    def _unposted(self, run: dict, phase: str, kind: str = "progress") -> bool:
        return bool(run.get("slack_channel_id")) and self.store.get_idem(f"{run['id']}:{kind}:{phase}") is None

    def _announce_start(self, run: dict, phase: str, tasks: list[dict]) -> str:
        key = f"{run['id']}:start:{phase}"
        text = self.store.get_kv(f"text:{key}")
        if text is None:
            text = phase_started(phase, tasks)
            self.store.set_kv(f"text:{key}", text)
        try:
            post_customer_update(self.store, self.registry, run, text, key=key, requested_by="tracking",
                                 reason=f"{phase} is under way (once per phase)")
        except ToolFailed as e:
            audit(self.store, run["id"], AGENT, "phase_started", status="failed",
                  rationale=f"{phase} started but the Slack post failed: {e}")
            return f"{phase} started (Slack post failed)"
        return f"{phase} started: update posted"

    def _announce(self, run: dict, phase: str, tasks: list[dict], statuses: dict[str, str] | None = None) -> str:
        channel = run.get("slack_channel_id")
        if not channel:
            audit(self.store, run["id"], AGENT, "phase_complete", status="degraded",
                  rationale=f"{phase} complete; no Slack channel on this run, nothing posted")
            return f"{phase} complete (no Slack channel)"
        key = f"{run['id']}:progress:{phase}"
        # The text is fixed at the first attempt: a retry after a lost response must look for (and, only if it
        # is really missing, post) the same message, not one with newer task counts.
        text = self.store.get_kv(f"text:{key}")
        if text is None:
            text = progress_update(phase, tasks, statuses)
            self.store.set_kv(f"text:{key}", text)
        try:
            post_customer_update(self.store, self.registry, run, text, key=key, requested_by="tracking",
                                 reason=f"all {phase} tasks completed (once per phase)")
        except ToolFailed as e:
            audit(self.store, run["id"], AGENT, "phase_complete", status="failed",
                  rationale=f"{phase} complete but the Slack post failed: {e}")
            return f"{phase} complete (Slack post failed)"
        return f"{phase} complete: progress posted"

    # ---------- rule b ----------
    def _auto_migration_check(self, run_id: str, task: dict) -> str:
        try:
            if self.migration_contacts is None:
                raise MigrationDataUnavailable("migration databases not configured")
            out = run_migration_check(self.store, self.registry, self.migration_contacts, run_id)
        except MigrationDataUnavailable as e:
            self._migration_escalation(run_id, f"'{task['name']}' was completed but the automatic migration check "
                                               f"could not read the migration databases ({e}); run it again with "
                                               "POST /runs/{id}/migration-check")
            return f"'{task['name']}' completed: migration check could not run, escalated"
        return f"'{task['name']}' completed: migration check {'passed' if out['passed'] else 'FAILED'}"

    # ---------- rule c ----------
    def _guard_migration_verified(self, run_id: str, project_id: str, task: dict, before: dict | None) -> str:
        state, _ = self.migration_state(run_id)
        if state in ACCEPTED:
            audit(self.store, run_id, AGENT, "migration_verified_accepted",
                  rationale=f"'{task['name']}' completed; migration {state}")
            return f"'{task['name']}' completed and accepted (migration {state})"
        why = {"failed": "the latest migration check did not pass",
               "not run": "no migration check has run"}[state]
        restore = before["status_value"] if before and not before["completed"] and before.get("status_value") \
            else DEFAULT_OPEN_STATUS
        ref = f"reopen-{uuid.uuid4().hex[:8]}"
        try:
            self.registry.call(AGENT, "rocketlane_set_task_status", {"task_id": task["task_id"], "status_value": restore},
                               run_id=run_id, rationale=f"'{task['name']}' was completed but {why}; restore status "
                                                         f"{restore}")
        except ToolFailed as e:
            # Snapshot keeps it "not completed": the reopen is retried next sync and Data Migration isn't announced.
            task["completed"] = False
            self._migration_escalation(run_id, f"'{task['name']}' was completed but {why}, and reopening it failed "
                                               f"({e.reason}); reopen it by hand")
            return f"'{task['name']}' completed without verification: reopen FAILED, escalated"
        # Read back (the status PUT and DEFAULT_OPEN_STATUS are UNVERIFIED). If Rocketlane still shows it completed,
        # keep it completed in the snapshot so the next sync doesn't reopen again: one write per human tick.
        try:
            now_tasks = self.registry.call(AGENT, "rocketlane_list_tasks", {"project_id": project_id}, run_id=run_id,
                                           rationale="confirm the reopen took effect").tasks
            stuck = any(t.task_id == task["task_id"] and t.completed for t in now_tasks)
        except ToolFailed:
            stuck = False  # can't tell; assume it applied (the PUT succeeded)
        if stuck:
            self._migration_escalation(run_id, f"'{task['name']}' was completed but {why}; the agent's reopen did "
                                               "not take effect in Rocketlane. Handoff stays blocked; reopen it by hand")
            return f"'{task['name']}' completed without verification: reopen not applied, escalated"
        task.update(status_value=restore, completed=False, status_label="")  # snapshot reflects the reopen
        note = (f"<p><b>Reopened by the onboarding agent</b> ({now()}, ref {ref}): marked complete but {why}. "
                "Fix the data and re-run the migration check, or have the CSM dismiss the migration escalation "
                "with a reason, then close this task again.</p>")
        try:
            self.registry.call(AGENT, "rocketlane_add_task_note", {"task_id": task["task_id"], "note_html": note,
                                                                   "marker": ref},
                               run_id=run_id, idempotency_key=f"{run_id}:{ref}", rationale="explain the reopen")
        except ToolFailed as e:
            audit(self.store, run_id, AGENT, "reopen_note", status="failed", rationale=f"note not written: {e}")
        self._migration_escalation(run_id, f"'{task['name']}' was marked complete but {why}; the agent reopened it")
        return f"'{task['name']}' reopened: {why}"

    def migration_state(self, run_id: str) -> tuple[str, str | None]:
        """("passed" | "accepted by CSM" | "failed" | "not run", CSM note). A dismissal counts only while no
        migration escalation is open: a newer failure opens a new one and voids the old acceptance."""
        latest = self._latest_check(run_id)
        if latest == "passed":
            return latest, None
        esc = [e for e in self.store.list_escalations() if e["run_id"] == run_id and e.get("kind") == "migration"]
        dismissed = [e for e in esc if e["status"] == "dismissed"]
        if dismissed and not any(e["status"] == "open" for e in esc):
            return "accepted by CSM", (dismissed[-1].get("resolution") or {}).get("note")
        return latest, None

    def _latest_check(self, run_id: str) -> str:
        rows = self.store.list_migration_checks(run_id)
        if not rows:
            return "not run"
        batch = rows[-1]["batch_id"]
        return "passed" if all(r["passed"] for r in rows if r["batch_id"] == batch) else "failed"

    def _migration_escalation(self, run_id: str, reason: str) -> None:
        """One open migration escalation per run (same rule as run_migration_check): refresh, don't pile up."""
        open_esc = [e for e in self.store.list_escalations("open") if e["run_id"] == run_id
                    and e.get("kind") == "migration"]
        key = open_esc[0]["key"] if open_esc else f"{run_id}:migration:{uuid.uuid4().hex[:12]}"
        self.store.upsert_escalation(key, dict(run_id=run_id, kind="migration", agent=AGENT, status="open",
                                               reason=reason))
        audit(self.store, run_id, AGENT, "escalate", rationale=reason, status="escalated")

    # ---------- rule e ----------
    def _wanted_flags(self, run: dict, tasks: list[dict], phases: list | None) -> dict[str, str] | None:
        """flag id -> reason for everything that needs a look right now; None if the phases couldn't be read."""
        if phases is None:
            return None
        out: dict[str, str] = {}
        for ph in phases:
            label = ph.status_label.strip().lower()
            open_ = [t["name"] for t in tasks if t.get("phase") == ph.name and not t["completed"]]
            if (label in COMPLETED_PHASE and open_ and ph.name == "Data Migration"
                    and self.migration_state(run["id"])[0] not in ACCEPTED):
                out[f"phase-done:{ph.name}"] = (
                    f"The {ph.name} phase was marked Completed in Rocketlane but the migration check has not "
                    f"passed ({len(open_)} task(s) still open: {', '.join(open_[:5])}). Not announced to the "
                    "customer until the migration is verified.")
            elif label == "blocked":
                out[f"blocked-phase:{ph.name}"] = f"The {ph.name} phase is marked Blocked in Rocketlane."
        for t in tasks:
            if t.get("status_label", "").strip().lower() == "blocked":
                out[f"blocked-task:{t['task_id']}"] = (f"Task '{t['name']}' ({t.get('phase') or 'no phase'}) is "
                                                       "marked Blocked in Rocketlane.")
        return out

    def _reconcile_flags(self, run: dict, tasks: list[dict], phases: list | None) -> list[str]:
        rid = run["id"]
        wanted = self._wanted_flags(run, tasks, phases)
        if wanted is None:  # phase statuses unreadable this time: keep what's open, the task rules still ran
            return []
        prefix = f"{rid}:tracking:"
        # key = <prefix><flag id>:<occurrence>; compare on the flag id
        open_now = {e["key"][len(prefix):].rsplit(":", 1)[0]: e for e in self.store.list_escalations("open")
                    if e["run_id"] == rid and e.get("kind") == "tracking" and e["key"].startswith(prefix)}
        actions = []
        for flag, reason in wanted.items():
            if flag in open_now:
                continue
            # A new key per occurrence: one that was cleared and comes back is a new escalation and a new post.
            key = f"{prefix}{flag}:{uuid.uuid4().hex[:8]}"
            self.store.upsert_escalation(key, dict(run_id=rid, kind="tracking", agent=AGENT, status="open",
                                                   reason=reason))
            audit(self.store, rid, AGENT, "escalate", rationale=reason, status="escalated")
            self._post_internal(run, f":warning: *{run.get('customer_name')}*: {reason}", key)
            actions.append(f"flagged: {reason}")
        for base, e in open_now.items():
            if base not in wanted:
                self.store.upsert_escalation(e["key"], dict(status="resolved", resolved_by="rocketlane",
                                                          resolution={"action": "cleared", "by": "rocketlane"}))
                audit(self.store, rid, AGENT, "flag_cleared", rationale=f"fixed in Rocketlane: {e['reason']}")
                actions.append(f"cleared: {base}")
        return actions

    def _post_internal(self, run: dict, text: str, key: str) -> None:
        """Best effort, internal channel only (never the customer's); the escalation is the record."""
        channel = self.settings.slack_handoff_channel
        if not channel or channel == run.get("slack_channel_id") or self.store.find_run_by_channel(channel):
            audit(self.store, run["id"], AGENT, "flag_slack", status="skipped" if not channel else "blocked",
                  rationale="no internal channel configured (SLACK_HANDOFF_CHANNEL); flag is in Needs a human"
                  if not channel else "SLACK_HANDOFF_CHANNEL is a customer channel; flag not posted to Slack")
            return
        try:
            self.registry.call(AGENT, "slack_post_internal", {"channel_id": channel, "text": text}, run_id=run["id"],
                               idempotency_key=f"{key}:slack", rationale="tell the CS team (internal channel)")
        except ToolFailed as e:
            audit(self.store, run["id"], AGENT, "flag_slack", status="failed",
                  rationale=f"flag is in Needs a human; internal Slack post failed: {e.reason}")

    # ---------- rule d ----------
    def _maybe_handoff(self, run: dict, tasks: list[dict], changed: bool) -> str | None:
        go_live = [t for t in tasks if t.get("phase") == GO_LIVE]
        if not go_live or not all(t["completed"] for t in go_live):
            return None
        mv = _find(tasks, _is_mv)
        state, _ = self.migration_state(run["id"])
        open_esc = any(e["run_id"] == run["id"] and e.get("kind") == "migration"
                       for e in self.store.list_escalations("open"))
        if mv is None or not mv["completed"] or state not in ACCEPTED or open_esc:
            if changed:
                audit(self.store, run["id"], AGENT, "handoff", status="blocked",
                      rationale=f"Go-Live complete but migration is not verified ('{MIGRATION_VERIFIED}' "
                                f"{'completed' if mv and mv['completed'] else 'open'}, migration {state}, "
                                f"{'an' if open_esc else 'no'} open migration escalation); no handoff yet")
            return "handoff blocked: migration not verified" if changed else None
        # Every phase, not just Go-Live: a handoff stops tracking, so a phase still open now would never be announced.
        # (Phases completed in this same sync were already announced by rule a above.)
        if not all(t["completed"] for t in tasks if t.get("phase") in PHASES):
            return None
        # A handoff stops tracking, so an open rule-e flag (something Blocked, a phase status out of step) could
        # never clear afterwards. Hold the handoff until it is fixed in Rocketlane.
        flags = [e["reason"] for e in self.store.list_escalations("open")
                 if e["run_id"] == run["id"] and e.get("kind") == "tracking"]
        if flags:
            if changed:
                audit(self.store, run["id"], AGENT, "handoff", status="blocked",
                      rationale=f"Go-Live complete but {len(flags)} item(s) need a look first: {flags[0]}")
            return "handoff blocked: project needs a look" if changed else None
        return self._handoff(run, tasks)

    def _handoff(self, run: dict, tasks: list[dict]) -> str:
        rid = run["id"]
        summary = (self.store.get_tracking(rid) or {}).get("handoff_summary")
        if not summary:  # a retry after a failed note write reuses the stored draft (no second LLM call)
            summary = self._draft_handoff(run, tasks)
            self.store.upsert_tracking(rid, handoff_summary=summary)
        ref = f"handoff-{rid}"
        note = (f"<p><b>Handoff summary</b> (ref {ref})</p><p>{html.escape(summary).replace(chr(10), '<br>')}</p>")
        try:
            task_id = self.registry.call(AGENT, "rocketlane_find_task",
                                         {"project_id": run["rocketlane_project_id"], "name_prefix": HANDOFF_TASK},
                                         run_id=rid, rationale="locate the handoff task").task_id
            if not task_id:
                raise ToolFailed("rocketlane_find_task", f"no '{HANDOFF_TASK}' task in the project")
            self.registry.call(AGENT, "rocketlane_add_task_note", {"task_id": task_id, "note_html": note,
                                                                   "marker": ref},
                               run_id=rid, idempotency_key=f"{rid}:{ref}",
                               rationale="handoff summary to the support team (private note, never Slack)")
        except ToolFailed as e:
            key = f"{rid}:tracking:handoff"
            self.store.upsert_escalation(key, dict(run_id=rid, kind="system", agent=AGENT, status="open",
                                                   reason=f"Handoff summary drafted but not written to Rocketlane "
                                                          f"({e.reason}); retried on the next sync"))
            audit(self.store, rid, AGENT, "handoff", status="escalated", rationale=f"note write failed: {e}")
            return "handoff note write failed: escalated"
        self.store.upsert_tracking(rid, handed_off_at=now())
        for esc in self.store.list_escalations("open"):
            if esc["run_id"] == rid and esc.get("agent") == AGENT and esc.get("kind") == "system":
                self.store.upsert_escalation(esc["key"], dict(status="resolved", resolved_by=AGENT,
                                                            resolution={"action": "retry", "by": AGENT}))
        if (self.store.get_run(rid) or {}).get("status") == "COMPLETED":
            self.store.upsert_run(rid, status="HANDED_OFF")
        audit(self.store, rid, AGENT, "handoff", output={"summary": summary},
              rationale="Go-Live complete and migration verified; summary on the handoff task, run handed off")
        self._post_internal_handoff(run, summary)
        return "handed off to support"

    def _post_internal_handoff(self, run: dict, summary: str) -> None:
        """Best effort: also post the summary to the internal handoff channel so support sees it in Slack.
        Never the customer-shared channel; a failure here doesn't undo the handoff (the note is in Rocketlane)."""
        channel = self.settings.slack_handoff_channel
        if not channel:
            return
        rid = run["id"]
        # Any customer's channel, not just this run's: an internal summary must never reach a customer.
        if channel == run.get("slack_channel_id") or self.store.find_run_by_channel(channel):
            audit(self.store, rid, AGENT, "handoff_slack", status="blocked",
                  rationale="SLACK_HANDOFF_CHANNEL is a customer-shared onboarding channel; handoff not posted to Slack")
            self.store.upsert_tracking(rid, handoff_slack="blocked")
            return
        tier = (run.get("plan_tier") or "").capitalize()
        text = (f":package: *Handoff to support: {run.get('customer_name')}* ({tier})\n"
                f"Rocketlane project {run.get('rocketlane_project_id')}\n\n{summary}")
        try:
            self.registry.call(AGENT, "slack_post_internal", {"channel_id": channel, "text": text}, run_id=rid,
                               idempotency_key=f"{rid}:handoff-slack",
                               rationale="handoff summary to the internal support channel (not the customer channel)")
        except ToolFailed as e:
            audit(self.store, rid, AGENT, "handoff_slack", status="failed",
                  rationale=f"handoff is in Rocketlane; internal Slack post failed (retried next sync): {e.reason}")
            self.store.upsert_tracking(rid, handoff_slack="pending")
            return
        self.store.upsert_tracking(rid, handoff_slack="posted")

    def handoff_facts(self, run: dict, tasks: list[dict]) -> dict:
        rule = tier_rules(self.settings)[Tier(run["plan_tier"])]
        result, csm_note = self.migration_state(run["id"])
        rows = self.store.list_migration_checks(run["id"])
        last = [r for r in rows if r["batch_id"] == rows[-1]["batch_id"]] if rows else []
        phases = [{"name": p, "tasks": [{"name": t["name"], "completed": t["completed"], "due_date": t["due_date"]}
                                        for t in tasks if t.get("phase") == p]} for p in PHASES]
        return {"customer_name": run.get("customer_name") or "", "plan": rule.label,
                "duration_days": rule.duration_days, "csm_model": rule.csm_model,
                "tasks_done": sum(t["completed"] for t in tasks), "tasks_total": len(tasks), "phases": phases,
                "dates": sorted({t["due_date"] for t in tasks if t["due_date"]}),
                "migration": {"result": result, "last_check_at": last[0]["ts"] if last else None,
                              "failed_checks": [r["check_name"] for r in last if not r["passed"]],
                              "csm_note": csm_note}}

    def _draft_handoff(self, run: dict, tasks: list[dict]) -> str:
        facts = self.handoff_facts(run, tasks)
        problems: list[str] = ["no LLM configured"] if self.llm is None else []
        for attempt in (1, 2) if self.llm is not None else ():
            try:
                draft = self.llm.parse(run_id=run["id"], agent=AGENT, node="draft_handoff", prompt=prompts.HANDOFF,
                                       user=handoff_user(facts),
                                       schema=HandoffDraft).summary
            except LLMError as e:
                problems = [f"LLM unavailable: {e}"]
                break
            problems = check_handoff(draft, facts)
            if not problems:
                audit(self.store, run["id"], AGENT, "draft_handoff", output={"summary": draft},
                      rationale=f"LLM draft passed fact checks (attempt {attempt})")
                return draft
            audit(self.store, run["id"], AGENT, "check_handoff", status="blocked", output={"summary": draft},
                  rationale=f"draft rejected: {problems}")
        fallback = static_handoff(facts)
        audit(self.store, run["id"], AGENT, "draft_handoff", status="fallback", output={"summary": fallback},
              rationale=f"no acceptable LLM draft ({problems}); using static template")
        return fallback


def _sig(t: dict) -> tuple:
    return (t["task_id"], t.get("phase"), t.get("status_value"), t["completed"], t.get("due_date"))
