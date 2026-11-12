"""Add demo customers to a running deployment, at different onboarding stages, for the dashboard and the video.

    docker compose exec api python -m scripts.demo_seed          (on the server; real Rocketlane + Slack)

Each customer gets what Agents 1 and 2 would create, through the same tool registry: a Rocketlane project from
the tier's template and a Slack channel (topic, internal invites, welcome). Only the deal email and the AE phone
call are skipped, and the audit trail says so. Then the script acts as the CSM: it changes task statuses in
Rocketlane, and the live Agent 3 (every 15 s) reacts on its own: phase updates in Slack, the automatic migration
check, reopening "Migration verified", Blocked flags. Nothing is posted to Slack by the script after the welcome.

Re-running adds new customers (names get a short suffix if one already exists)."""

import sys
import time
import uuid
from datetime import date

from app.config import Settings, Tier, role_placeholders, tier_rules
from app.container import build_container
from app.graph.comms import static_welcome
from app.guardrails.output import channel_name
from app.providers.real.rocketlane import RocketlaneClient
from app.services.audit import audit
from app.services.keys import customer_key

COMPLETED, IN_PROGRESS, BLOCKED = 3, 2, 4
SEED = "demo seed (scripts/demo_seed.py): no deal email or AE call for this customer"

# name, tier, contact, steps; a step is (task-name prefixes, status) or "wait" (let Agent 3 sync in between)
CUSTOMERS = [
    ("Acme Retail", Tier.ENTERPRISE, "Maya Chen", "maya.chen@acmeretail.example", [
        (["Internal sales-to-CS", "Kickoff call", "Success plan"], COMPLETED), "wait",
        (["Source data export", "Field mapping", "Test import", "Full import"], COMPLETED), "wait",
        (["Migration verified"], COMPLETED)]),  # ticked without a passing check -> Agent 3 reopens it
    ("Bluebird Health", Tier.GROWTH, "Omar Haddad", "omar@bluebirdhealth.example", [
        (["Kickoff call", "Success plan"], COMPLETED), "wait",
        (["Data export"], IN_PROGRESS)]),
    ("Copperline Logistics", Tier.GROWTH, "Sara Lind", "sara.lind@copperline.example", [
        (["Kickoff call", "Success plan"], COMPLETED), "wait",
        (["Data export", "Data import"], COMPLETED)]),  # import done -> automatic migration check (fails on sample)
    ("Driftwood Media", Tier.ENTERPRISE, "Leo Brandt", "leo@driftwoodmedia.example", [
        (["Internal sales-to-CS"], COMPLETED), (["Kickoff call"], IN_PROGRESS), (["Success plan"], BLOCKED)]),
]


def main() -> int:
    s = Settings()
    k = build_container(s)
    rl, reg, store = RocketlaneClient(s.rocketlane_base_url, s.rocketlane_api_key), k.registry, k.store
    rules = tier_rules(s)
    existing = {r.get("customer_name") for r in store.list_runs(1000)}
    seeded = []
    for name, tier, contact, email, steps in CUSTOMERS:
        if name in existing:
            name = f"{name} {uuid.uuid4().hex[:4]}"
        rid, rule = str(uuid.uuid4()), rules[tier]
        ext = f"demo-{rid[:8]}"
        store.upsert_run(rid, status="RECEIVED", customer_name=name, customer_key=customer_key(name), opportunity_id=ext)
        audit(store, rid, "root", "start", rationale=SEED)
        audit(store, rid, "intake", "decide_tier", status="confirmed", output={"tier": tier.value},
              rationale=f"{SEED}; tier {tier.value} set by the seed")
        project_id = reg.call("intake", "rocketlane_create_project", dict(
            project_name=f"{name} Onboarding ({rule.label}, {rule.duration_days}-day)", customer_name=name,
            owner_email=s.rocketlane_owner_email, start_date=date.today().isoformat(), template_id=rule.template_id,
            member_emails=[e for e in (s.rocketlane_pm_email, rule.csm_email) if e], customer_contact_email=email,
            external_reference_id=ext, placeholders=role_placeholders(s.rocketlane_pm_email, rule.csm_email, email)),
            run_id=rid, idempotency_key=f"project:{rid}", rationale=f"demo seed: {rule.label} template").project_id
        store.upsert_run(rid, status="PROJECT_CREATED", plan_tier=tier.value, rocketlane_project_id=str(project_id))
        audit(store, rid, "intake", "create_project", rationale=f"created from {rule.label} template")
        cid = reg.call("comms", "slack_create_channel", {"name": channel_name(name, tier, rid)}, run_id=rid,
                       idempotency_key=f"channel:{rid}", rationale="tier-personalized channel name").channel_id
        store.upsert_run(rid, slack_channel_id=cid)
        reg.call("comms", "slack_set_topic", {"channel_id": cid, "topic": (
            f"{name} | {rule.label} | {rule.duration_days}-day onboarding | {rule.csm_model} CSM")}, run_id=rid,
            idempotency_key=f"topic:{rid}")
        internal = [u for u in s.slack_internal_member_ids.split(",") if u.strip()]
        if internal:
            reg.call("comms", "slack_invite_internal", {"channel_id": cid, "user_ids": internal}, run_id=rid,
                     idempotency_key=f"invite:{rid}")
        welcome = static_welcome({"customer_name": name, "contact_name": contact, "plan": rule.label,
                                  "duration_days": rule.duration_days, "csm_model": rule.csm_model})
        reg.call("comms", "slack_post_message", {"channel_id": cid, "text": welcome}, run_id=rid,
                 idempotency_key=f"welcome:{rid}")
        audit(store, rid, "comms", "setup_channel", rationale="channel ready")
        store.upsert_run(rid, status="COMPLETED")
        audit(store, rid, "root", "complete", rationale="project created and channel set up")
        print(f"{name}: project {project_id}, channel {cid}, run {rid[:8]}", flush=True)
        seeded.append((name, str(project_id), steps))

    time.sleep(5)  # Rocketlane search/list lags a fresh project
    for i in range(max(len(st) for *_, st in seeded)):  # one step per customer per round, Agent 3 syncs between
        for name, pid, steps in seeded:
            if i >= len(steps) or steps[i] == "wait":
                continue
            prefixes, status = steps[i]
            for t in rl.list_tasks(pid):
                if any(t["name"].lower().startswith(p.lower()) for p in prefixes):
                    rl.set_task_status(t["task_id"], status)
            print(f"  {name}: {', '.join(prefixes)} -> {status}", flush=True)
        time.sleep(30)  # > TRACKING_INTERVAL_SECONDS: Agent 3 picks the change up before the next step
    print("done; Agent 3 keeps tracking these projects every 15 s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
