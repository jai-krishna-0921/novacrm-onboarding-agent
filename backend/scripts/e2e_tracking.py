"""Live end-to-end check of Agent 3 (Project Tracking) against the REAL Rocketlane and Slack APIs.

    make e2e-tracking            (needs `make up migrate seed` for the local database)

What it does, acting like a CSM/PM:
  1. creates a throwaway Rocketlane project from the Growth template and two throwaway Slack channels
     (customer channel + internal handoff channel), linked to a test run in the LOCAL database;
  2. ticks tasks through the Rocketlane API phase by phase and runs Agent 3's real sync after each step;
  3. asserts what Agent 3 must do: progress post per phase, migration check on the import task, reopen of
     "Migration verified" ticked without a passing check, a passing check after the data is fixed, the handoff
     note in Rocketlane and the internal handoff post;
  4. deletes the project, archives the channels and restores the local migration demo data.

It never touches the production database, Initech, or the real #cs-handoffs channel. One Sonnet call (~$0.01) for
the handoff summary. Exit code 0 = every check passed.
"""

import sys
import time
import uuid
from datetime import date

from app.config import Settings, Tier, role_placeholders, tier_rules
from app.container import RealProviders
from app.db.seed import demo_contacts
from app.db.store import PgStore
from app.llm.client import LangChainLLM
from app.providers.fake import FakeWorld
from app.providers.real.rocketlane import RocketlaneClient
from app.providers.real.slack import SlackClient
from app.services.migration_check import load_contacts, run_migration_check
from app.services.migration_demo import repair, replace_contacts
from app.services.tracking import Tracker
from app.tools.definitions import RlCreateIn, build_registry, rocketlane_project_payload
from app.tools.registry import AmbiguousToolError

COMPLETED = 3
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{' - ' + detail if detail else ''}", flush=True)


def main() -> int:
    s = Settings()
    stamp = time.strftime("%m%d%H%M%S")
    rl = RocketlaneClient(s.rocketlane_base_url, s.rocketlane_api_key, timeout=90)  # project create can take 20 s+
    slack = SlackClient(s.slack_bot_token)
    store = PgStore(s.database_url)  # local docker Postgres (DATABASE_URL default), never production
    if "localhost" not in s.database_url and "127.0.0.1" not in s.database_url:
        print("refusing: DATABASE_URL is not local")
        return 2
    fake = FakeWorld()  # directory/gmail/voice are never used by tracking; Rocketlane and Slack are real
    registry = build_registry(store, RealProviders(fake.directory, fake.gmail, fake.voice, rl, slack))
    rule = tier_rules(s)[Tier.GROWTH]
    project_id = customer_ch = handoff_ch = None
    try:
        print("1. setup", flush=True)
        customer = f"E2E Tracking {stamp}"
        payload = rocketlane_project_payload(RlCreateIn(
            project_name=f"{customer} (E2E test, Growth)", customer_name=customer,
            owner_email=s.rocketlane_owner_email, start_date=date.today().isoformat(), template_id=rule.template_id,
            member_emails=[e for e in (s.rocketlane_pm_email, rule.csm_email) if e],
            customer_contact_email=f"e2e-{stamp}@example.com", external_reference_id=f"e2e-{stamp}",
            placeholders=role_placeholders(s.rocketlane_pm_email, rule.csm_email, f"e2e-{stamp}@example.com")))
        try:
            project_id = rl.create_project(payload)
        except AmbiguousToolError:  # same reconcile as the agent: the create may have happened, look it up
            for _ in range(10):
                time.sleep(3)
                if project_id := rl.find_project(customer, f"e2e-{stamp}"):
                    break
            if not project_id:
                raise
        customer_ch = slack.create_channel(f"e2e-tracking-{stamp}")
        handoff_ch = slack.create_channel(f"e2e-handoffs-{stamp}")
        run_id = str(uuid.uuid4())
        store.upsert_run(run_id, status="COMPLETED", customer_name=customer, customer_key=f"e2e{stamp}",
                         plan_tier="growth", rocketlane_project_id=project_id, slack_channel_id=customer_ch,
                         opportunity_id=f"e2e-{stamp}")
        print(f"   project {project_id}, channels {customer_ch} / {handoff_ch}, run {run_id[:8]}", flush=True)

        settings = s.model_copy(update={"slack_handoff_channel": handoff_ch})
        contacts = lambda: (load_contacts(s.mig_source_url), load_contacts(s.mig_dest_url))  # noqa: E731
        tracker = Tracker(settings, store, registry, LangChainLLM(store, s.llm_handoff_model, s.anthropic_api_key),
                          contacts)
        tasks = {t["name"]: t for t in rl.list_tasks(project_id)}
        by_phase: dict[str, list[dict]] = {}
        for t in tasks.values():
            by_phase.setdefault(t["phase"], []).append(t)
        check("project has the Growth template's 4 phases", set(by_phase) >= {"Kickoff", "Data Migration",
                                                                              "Configuration", "Go-Live"},
              f"{len(tasks)} tasks")
        tracker.sync(run_id)  # baseline snapshot

        def tick(*names: str) -> None:
            for n in names:
                rl.set_task_status(tasks[n]["task_id"], COMPLETED)
            time.sleep(2)  # Rocketlane search is eventually consistent


        print("2. Kickoff done -> progress post", flush=True)
        tick(*[t["name"] for t in by_phase["Kickoff"]])
        out = tracker.sync(run_id)
        check("Kickoff phase announced in Slack", any("Kickoff complete" in a for a in out["actions"]), str(out["actions"]))

        print("3. Data import done -> automatic migration check", flush=True)
        imp = next(n for n in tasks if n.lower().startswith(("data import", "full import")))
        tick(imp, *[t["name"] for t in by_phase["Data Migration"] if t["name"].lower().startswith("data export")])
        out = tracker.sync(run_id)
        checks = store.list_migration_checks(run_id)
        check("migration check ran on import completion", bool(checks), f"{len(checks)} check rows")
        check("demo data fails the check (planted defects)", checks and not all(c["passed"] for c in checks))
        mv = next(n for n in tasks if n.startswith("Migration verified"))
        note = rl._request("GET", f"/tasks/{tasks[mv]['task_id']}", params={"includeFields": "taskPrivateNote"})
        check("evidence note written on 'Migration verified'", "Migration check" in (note.get("taskPrivateNote") or ""))

        print("4. 'Migration verified' ticked without a passing check -> reopened", flush=True)
        tick(mv)
        tracker.sync(run_id)
        time.sleep(2)
        status = rl._request("GET", f"/tasks/{tasks[mv]['task_id']}").get("status", {})
        check("'Migration verified' reopened by Agent 3", status.get("value") != COMPLETED, str(status))
        esc = [e for e in store.list_escalations("open") if e["run_id"] == run_id and e.get("kind") == "migration"]
        check("migration escalation open for the CSM", bool(esc))

        print("5. data fixed -> check passes -> verified sticks", flush=True)
        src, dest = demo_contacts()
        replace_contacts(s.mig_dest_url, repair(src, dest))  # the CSM's fix: missing column, rows and blanks
        res = run_migration_check(store, registry, contacts, run_id)
        check("migration check passes after the fix", res["passed"] is True)
        tick(mv)
        tracker.sync(run_id)
        time.sleep(2)
        status = rl._request("GET", f"/tasks/{tasks[mv]['task_id']}").get("status", {})
        check("'Migration verified' stays completed", status.get("value") == COMPLETED, str(status))

        print("6. remaining tasks done -> phases announced, handoff", flush=True)
        rest = [t["name"] for t in tasks.values() if t["name"] not in (mv,) and
                rl._request("GET", f"/tasks/{t['task_id']}").get("status", {}).get("value") != COMPLETED]
        tick(*rest)
        out = tracker.sync(run_id)
        if not any("handed off" in a for a in out["actions"]):
            time.sleep(3)
            out = tracker.sync(run_id)
        tr = store.get_tracking(run_id) or {}
        check("handoff happened (run handed off)", bool(tr.get("handed_off_at")), str(out["actions"]))
        hnote = rl._request("GET", f"/tasks/{tasks['Handoff to support']['task_id']}",
                            params={"includeFields": "taskPrivateNote"})
        check("handoff summary on 'Handoff to support'", "Handoff summary" in (hnote.get("taskPrivateNote") or ""))
        check("handoff posted to the internal channel", tr.get("handoff_slack") == "posted", str(tr.get("handoff_slack")))
        summary = tr.get("handoff_summary") or ""
        print(f"   handoff summary: {summary[:300]}", flush=True)
    except Exception as e:  # report, then clean up
        check("unexpected error", False, f"{type(e).__name__}: {e}")
    finally:
        print("7. cleanup", flush=True)
        try:
            if project_id:
                rl._request("DELETE", f"/projects/{project_id}", write=True)
            for ch in (customer_ch, handoff_ch):
                if ch:
                    slack.c.conversations_archive(channel=ch)
            replace_contacts(s.mig_dest_url, demo_contacts()[1])
            print("   project deleted, channels archived, demo data restored", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"   cleanup problem: {e}", flush=True)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
