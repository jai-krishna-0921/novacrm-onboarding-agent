"""Agent 3 at scale: 50 live projects, a "human" completing tasks in random order (including ticking "Migration
verified" too early and re-ticking it after a reopen), while tick() and POST /runs/{id}/sync race each other from
many threads. Run clean and with 20% provider faults (5xx / 429 / timeouts before and after send)."""

import random
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from envs import _sig, build_env, drive, stress_settings
from stress_harness import ChaosProviders, ErrorLog, UdsServer, make_deals, settle

from app.main import create_app
from app.providers.fake import COMPLETED
from app.services.tracking import PHASES
from app.tools.definitions import build_registry

pytestmark = pytest.mark.stress
HANDOFF_CH = "CHANDOFF"


def _is_mv(name: str) -> bool:
    return name.lower().startswith("migration verified")


@pytest.mark.parametrize("fault_rate", [0.0, 0.2])
def test_50_projects_random_task_order_with_racing_tick_and_sync(settings, fault_rate, capsys):
    deals = [d for d in make_deals(600, seed=5) if d.kind == "valid" and d.persona in ("enterprise", "growth")][:50]
    s = stress_settings(settings, slack_handoff_channel=HANDOFF_CH, tracking_interval_seconds=0)
    env = build_env(s, deals)
    env.world.slack.channels[HANDOFF_CH] = {"name": "cs-handoffs", "topic": "", "members": [], "shared_invites": [],
                                            "messages": []}
    rng = random.Random(42)
    human_mv_ticks: Counter = Counter()
    stats: Counter = Counter()
    with ErrorLog() as errs:
        with UdsServer(env.app) as srv:
            drive(env, srv, lost=0, dup=0, stale=0)  # onboard 50 customers
        runs = [r for r in env.store.list_runs(1000) if r["status"] == "COMPLETED"]
        assert len(runs) == 50
        if fault_rate:  # faults only for the tracking phase
            env.container.registry = build_registry(env.store, ChaosProviders(env.world, fault_rate, seed=9),
                                                    sleep=lambda _: None)
        # Fresh app over the same container (a restart): drive()'s reconcile tick ran with a future clock, which
        # parks that app instance's tracking timer (test artifact; production always ticks with the real clock).
        env.app = create_app(env.container)
        pid_of = {r["id"]: str(r["rocketlane_project_id"]) for r in runs}
        projects = {str(p["projectId"]): p for p in env.world.rocketlane.projects}
        done = threading.Event()

        def human():
            """Completes one random open task at a time; a reopened 'Migration verified' is open again, so it
            gets re-ticked like any other task."""
            while True:
                open_ = [(pid, t) for pid, p in projects.items() for t in p["tasks"]
                         if t["status"]["label"] != "Completed"]
                if not open_:
                    break
                pid, t = rng.choice(open_)
                t["status"] = dict(COMPLETED)
                if _is_mv(t["taskName"]):
                    human_mv_ticks[pid] += 1
                done.wait(0.002)
            done.set()

        with UdsServer(env.app) as srv, ThreadPoolExecutor(8) as pool:
            client = srv.client()

            def syncer():
                while not done.is_set():
                    stats[f"sync_{client.post(f'/runs/{rng.choice(list(pid_of))}/sync').status_code}"] += 1

            def ticker():
                while not done.is_set():
                    env.app.state.tick()
                    stats["ticks"] += 1

            futs = [pool.submit(human)] + [pool.submit(syncer) for _ in range(5)] + [pool.submit(ticker) for _ in range(2)]
            [f.result() for f in futs]
            for _ in range(6):  # quiesce: the scheduler keeps going after the humans stop
                env.app.state.tick()
                settle(lambda: _sig(env), quiet=0.2)
                if all((env.store.get_tracking(r) or {}).get("handed_off_at") for r in pid_of):
                    break
                for pid, p in projects.items():  # re-tick a reopened "Migration verified" (check has passed now)
                    for t in p["tasks"]:
                        if _is_mv(t["taskName"]) and t["status"]["label"] != "Completed":
                            t["status"] = dict(COMPLETED)
                            human_mv_ticks[pid] += 1

    violations = []
    handoff_posts = env.world.slack.channels[HANDOFF_CH]["messages"]
    open_esc = {e["run_id"] for e in env.store.list_escalations("open")}
    for rid, pid in pid_of.items():
        run = env.store.get_run(rid)
        cust = env.world.slack.channels[run["slack_channel_id"]]["messages"]
        heads = Counter(m.splitlines()[0] for m in cust[1:])
        violations += [f"{rid}: progress post repeated {n}x: {h}" for h, n in heads.items() if n > 1]
        if any(not m.startswith((":white_check_mark:", ":arrow_forward:")) for m in cust[1:]):  # done / started
            violations.append(f"{rid}: non-template message in the customer channel")
        snap = (env.store.get_tracking(rid) or {}).get("tasks") or []
        done_phases = {p for p in PHASES if all(t["completed"] for t in snap if t["phase"] == p)}
        posted = {p for p in PHASES if any(f"The *{p}* phase" in h and "complete" in h for h in heads)}
        if not fault_rate and posted != done_phases:
            violations.append(f"{rid}: phases complete in the last snapshot {sorted(done_phases)}, posted {sorted(posted)}")
        batches = {m["batch_id"] for m in env.store.list_migration_checks(rid)}
        if len(batches) > 1:
            violations.append(f"{rid}: {len(batches)} migration-check batches for one import completion")
        reopens = [c for c in env.store.tool_calls if c["run_id"] == rid and c["tool"] == "rocketlane_set_task_status"
                   and c["status"] == "ok"]
        if len(reopens) > human_mv_ticks[pid]:
            violations.append(f"{rid}: {len(reopens)} reopens for {human_mv_ticks[pid]} human ticks (flapping)")
        passed = sorted(m["ts"] for m in env.store.list_migration_checks(rid) if m["passed"])
        if passed and any(c["ts"] > passed[0] for c in reopens):
            violations.append(f"{rid}: reopened 'Migration verified' after a passing migration check")
        notes = next(t for t in projects[pid]["tasks"] if t["taskName"] == "Handoff to support")["taskPrivateNote"]
        n_notes = notes.count(f"ref handoff-{rid}")
        posts = [m for m in handoff_posts if f"Handoff to support: {run['customer_name']}*" in m]
        handed = (env.store.get_tracking(rid) or {}).get("handed_off_at")
        if n_notes > 1 or len(posts) > 1:
            violations.append(f"{rid}: handoff note x{n_notes}, #cs-handoffs x{len(posts)}")
        if not handed and rid not in open_esc:
            violations.append(f"{rid}: not handed off and no open escalation (status {run['status']})")
        if not fault_rate and (not handed or len(posts) != 1 or n_notes != 1):
            violations.append(f"{rid}: clean run not handed off exactly once")
        if handed and not posts and not any(a["step"] == "handoff_slack" for a in env.store.list_audit(rid)):
            violations.append(f"{rid}: handed off, no #cs-handoffs post and no audit of why")
    with capsys.disabled():
        handed_n = sum(bool((env.store.get_tracking(r) or {}).get("handed_off_at")) for r in pid_of)
        print(f"\n[tracking faults={fault_rate}] 50 projects: handed off {handed_n}, open escalations "
              f"{len(open_esc & set(pid_of))}, human MV ticks {sum(human_mv_ticks.values())}, reopens "
              f"{sum(c['tool'] == 'rocketlane_set_task_status' for c in env.store.tool_calls)}, {dict(stats)}, "
              f"#cs-handoffs posts {len(handoff_posts)}")
        for v in violations[:8]:
            print("   ", v)
    assert violations == []
    assert fault_rate or errs.summary() == [], errs.summary()
