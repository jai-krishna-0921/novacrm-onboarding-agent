"""Migration validation (plan §12): compare source and destination `contacts`, store the evidence, put it on the
project's "Migration verified" task, and escalate to the CSM when anything is off. Code decides; no LLM.

The task is never closed here. A person closes it after reading the evidence.
"""

import html
import json
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.services.audit import audit
from app.services.store import Store, now
from app.tools.registry import ToolFailed, ToolRegistry

AGENT = "migration"
TASK_PREFIX = "Migration verified"  # Enterprise: "Migration verified (validation checks passed)"; Growth: same prefix
SAMPLE = 10

Contacts = list[dict]  # rows with id, name, email


class MigrationDataUnavailable(Exception):
    """Source or destination database could not be read."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    details: dict  # expected, actual, sample_ids


def load_contacts(url: str) -> Contacts:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return [dict(r._mapping) for r in c.execute(text("SELECT id, name, email FROM contacts ORDER BY id"))]
    except SQLAlchemyError as e:
        raise MigrationDataUnavailable(f"{type(e).__name__}: {str(e).splitlines()[0]}") from e
    finally:
        eng.dispose()


def _result(name: str, expected: int, actual: int, ids: list, ok: bool = True) -> CheckResult:
    return CheckResult(name, ok and expected == actual,
                       {"expected": expected, "actual": actual, "sample_ids": ids[:SAMPLE]})


def _ids(ids) -> list:
    """The destination has no PK, so ids can be NULL or mixed types; sort without crashing (NULLs last)."""
    return sorted(ids, key=lambda i: (i is None, str(i)))


def evaluate(source: Contacts, dest: Contacts) -> list[CheckResult]:
    dest_ids = {r["id"] for r in dest}
    missing = _ids({r["id"] for r in source} - dest_ids)
    dupes = _ids(i for i, n in Counter(r["id"] for r in dest).items() if n > 1)
    blank = _ids({r["id"] for r in dest if not (r.get("name") or "").strip() or not (r.get("email") or "").strip()})
    return [
        # An empty source means the migration never ran or the URL is wrong: never "verified".
        _result("record_count", len(source), len(dest), [], ok=len(source) > 0),
        _result("primary_keys", 0, len(missing), missing),
        _result("duplicates", 0, len(dupes), dupes),
        _result("required_fields", 0, len(blank), blank),
    ]


def _note(results: list[CheckResult], batch_id: str, ts: str) -> str:
    verdict = "PASSED" if all(r.passed for r in results) else "FAILED"
    def line(r: CheckResult) -> str:
        ids = f", ids {html.escape(str(r.details['sample_ids']))}" if r.details["sample_ids"] else ""
        return (f"<li>{r.name}: {'pass' if r.passed else 'FAIL'} "
                f"(expected {r.details['expected']}, actual {r.details['actual']}{ids})</li>")

    items = "".join(line(r) for r in results)
    return (f"<p><b>Migration check {verdict}</b> ({ts}, batch {batch_id})</p><ul>{items}</ul>"
            "<p>Automated evidence only. Close this task after reviewing it.</p>")


def run_migration_check(store: Store, registry: ToolRegistry, contacts: Callable[[], tuple[Contacts, Contacts]],
                        run_id: str, reuse_within: float = 0, clock: Callable[[], float] = time.time) -> dict:
    """Caller guarantees the run exists and has a Rocketlane project.

    reuse_within: if a check with identical results finished that many seconds ago, return it instead of writing
    a second batch and evidence note (a double-clicked button). Not reused once its escalation was closed."""
    run = store.get_run(run_id) or {}
    project_id = run["rocketlane_project_id"]
    source, dest = contacts()  # raises MigrationDataUnavailable before anything is recorded
    results = evaluate(source, dest)
    fingerprint = json.dumps([r.__dict__ for r in results], sort_keys=True, default=str)
    last_key = f"migration_last:{run_id}"
    if reuse_within and (last := store.get_kv(last_key)):
        last = json.loads(last)
        esc = last["out"]["escalation"]
        still_open = not esc or any(e["key"] == esc for e in store.list_escalations("open"))
        if last["fingerprint"] == fingerprint and clock() - last["at"] < reuse_within and still_open:
            audit(store, run_id, AGENT, "migration_check", status="ignored", output={"batch_id": last["out"]["batch_id"]},
                  rationale=f"identical check finished {int(clock() - last['at'])}s ago; returned it instead of "
                            "running a second batch")
            return last["out"]
    batch_id, ts = uuid.uuid4().hex[:12], now()
    for r in results:
        store.add_migration_check(dict(run_id=run_id, batch_id=batch_id, ts=ts, check_name=r.name,
                                       passed=r.passed, details=r.details))
    passed = all(r.passed for r in results)

    # Evidence goes on the task in both cases; a failure to write it is itself a reason to involve a human.
    task_note = "written"
    try:
        task_id = registry.call(AGENT, "rocketlane_find_task", {"project_id": project_id, "name_prefix": TASK_PREFIX},
                                run_id=run_id, rationale="locate the task that receives the evidence").task_id
        if task_id:
            registry.call(AGENT, "rocketlane_add_task_note",
                          {"task_id": task_id, "note_html": _note(results, batch_id, ts), "marker": batch_id},
                          run_id=run_id, idempotency_key=f"{run_id}:migration-note:{batch_id}",
                          rationale=f"attach migration check evidence (batch {batch_id}); task stays open")
        else:
            task_note = f"not written: no '{TASK_PREFIX}' task in project {project_id}"
    except ToolFailed as e:
        task_note = f"not written: {e}"

    problems = [f"{r.name} (expected {r.details['expected']}, actual {r.details['actual']}, "
                f"ids {r.details['sample_ids']})" for r in results if not r.passed]
    if task_note != "written":
        problems.append(f"evidence {task_note}")
    open_esc = [x for x in store.list_escalations("open") if x["run_id"] == run_id and x.get("kind") == AGENT]

    escalation = None
    if problems:
        # One open migration escalation per run: a repeat failure refreshes its reason instead of piling up.
        escalation = open_esc[0]["key"] if open_esc else f"{run_id}:migration:{batch_id}"
        store.upsert_escalation(escalation, dict(
            run_id=run_id, kind=AGENT, agent=AGENT, status="open",
            reason=f"Migration check batch {batch_id} needs the CSM: " + "; ".join(problems)))
    else:
        for esc in open_esc:
            store.upsert_escalation(esc["key"], dict(status="resolved", resolved_by="migration_check",
                                                   resolution={"action": "retry", "by": "migration_check",
                                                               "batch_id": batch_id}))
    audit(store, run_id, AGENT, "migration_check",
          output={"batch_id": batch_id, "passed": passed, "results": [r.__dict__ for r in results],
                  "task_note": task_note, "escalation": escalation},
          rationale=("all four checks passed; evidence attached, task left open for a person to close" if not problems
                     else "escalated to CSM: " + "; ".join(problems)),
          status="ok" if not problems else "escalated")
    out = {"batch_id": batch_id, "passed": passed, "results": [r.__dict__ for r in results],
           "task_note": task_note, "escalation": escalation}
    if reuse_within:  # only button clicks dedupe against each other; scheduled syncs always record a batch
        store.set_kv(last_key, json.dumps({"at": clock(), "fingerprint": fingerprint, "out": out}, default=str))
    return out
