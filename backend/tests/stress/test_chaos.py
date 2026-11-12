"""Chaos: provider faults at 10-30% (5xx, 429, timeout before send, timeout AFTER send), LLM outages/refusals, a
human pressing "retry" on every system/tier escalation, and (Postgres) workers killed mid-node followed by a
process restart over the same database + checkpointer. Every run must end in success or a clear escalation, and
no side effect may be duplicated."""

import time
from collections import Counter

import pytest
from envs import _sig, build_env, drive, pending_calls, stress_settings
from sqlalchemy import create_engine, text
from stress_harness import ErrorLog, UdsServer, check_invariants, make_deals, settle

from app.providers.fake import FakeWorld
from app.providers.real.retell import map_call_analyzed
from app.services.runner import MID_NODE

pytestmark = pytest.mark.stress
PG_ADMIN = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"


def _retry_round(env, client, retries: Counter, kinds=("system", "tier")) -> int:
    """A human presses retry on every open system/tier escalation (never set_tier: no tier is ever invented)."""
    n = 0
    for e in env.store.list_escalations("open"):
        if e.get("kind") in kinds and retries[e["run_id"]] < 3:
            r = client.post(f"/runs/{e['run_id']}/resolve", json={"action": "retry", "by": "chaos-human"})
            assert r.status_code in (200, 422), r.text  # 422: escalation already superseded
            if r.status_code == 200:
                retries[e["run_id"]] += 1
                n += 1
    return n


def _report(capsys, label, env, runs, retries, extra=""):
    with capsys.disabled():
        print(f"\n[{label}] runs {len(runs)} statuses {dict(Counter(r['status'] for r in runs))}; projects "
              f"{len(env.world.rocketlane.projects)}, channels {len(env.world.slack.channels)}, dials "
              f"{len(env.world.voice.calls)}; human retries {sum(retries.values())}; injected "
              f"{sum(env.providers.faults.values()) if hasattr(env.providers, 'faults') else 0} provider faults, "
              f"{env.llm.faults} LLM faults {extra}")


@pytest.mark.parametrize("rate", [0.1, 0.3])
def test_provider_and_llm_faults_end_in_success_or_clear_escalation(settings, rate, capsys):
    deals = make_deals(120, seed=int(rate * 100))
    env = build_env(stress_settings(settings), deals, fault_rate=rate, llm_fault=rate / 2, seed=int(rate * 1000))
    retries: Counter = Counter()
    with ErrorLog() as errs, UdsServer(env.app) as srv:
        client = srv.client()
        drive(env, srv)
        for _ in range(4):  # humans retry under continuing faults
            if not _retry_round(env, client, retries):
                break
            settle(lambda: _sig(env))
            drive(env, srv, push_emails=False)
        env.providers.calm()  # outage over: one more retry round must converge
        env.llm.fault_rate = 0
        _retry_round(env, client, retries, kinds=("system",))
        settle(lambda: _sig(env))
        drive(env, srv, push_emails=False)
    runs = env.store.list_runs(100_000)
    v = check_invariants(runs=runs, escalations=env.store.list_escalations(), audit_rows=env.store.audit_rows,
                         world=env.world, deals=deals, human_retries=retries)
    _report(capsys, f"chaos rate={rate}", env, runs, retries, f"faults by kind: {dict(env.providers.faults)}")
    with capsys.disabled():
        for x in v[:10]:
            print("   ", x)
    _dump(env, runs, v, env.store.audit_rows)
    assert v == []
    assert errs.summary() == []


def _dump(env, runs, violations, audit_rows):
    """STRESS_DUMP=<file>: write the audit trail of every run named (by id or customer) in a violation."""
    import os
    import re
    path = os.environ.get("STRESS_DUMP")
    if not path or not violations:
        return
    text_v = " ".join(violations)
    hit = [r for r in runs if r["id"] in text_v or (r.get("customer_name") and re.sub(r"[^a-z0-9]+", "-",
           r["customer_name"].lower()) in text_v)]
    with open(path, "w") as f:
        for r in hit[:4]:
            f.write(f"=== {r['id']} {r.get('customer_name')} {r['status']} channel={r.get('slack_channel_id')}\n")
            for a in audit_rows:
                if a["run_id"] == r["id"]:
                    f.write(f"{a['ts'][11:23]} {a['agent']}/{a['step']} [{a['status']}] {str(a['rationale'])[:220]}"
                            f" {str(a.get('output'))[:160] if a['step'].startswith('tool:slack') else ''}\n")


# ---------------------------------------------------------------- Postgres: kill mid-node + restart
class Killed(BaseException):
    """The worker process dies (not an Exception: nothing in the app may catch it)."""


class KillSwitch:
    """Provider proxy: the next call to `armed` performs its side effect, then the process 'dies'."""

    def __init__(self, inner):
        self._inner, self.armed = inner, None

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*a, **kw):
            out = attr(*a, **kw)
            if self.armed == name:
                self.armed = None
                raise Killed(name)
            return out
        return wrapped


class KillableWorld:
    def __init__(self, world):
        self.raw = world
        self.directory, self.gmail, self.voice = world.directory, world.gmail, world.voice
        self.rocketlane, self.slack = KillSwitch(world.rocketlane), KillSwitch(world.slack)


@pytest.fixture
def pg(socket_enabled):
    try:
        admin = create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.exec_driver_sql("DROP DATABASE IF EXISTS stress_chaos WITH (FORCE)")
            c.exec_driver_sql("CREATE DATABASE stress_chaos")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable (make up): {e}")
    yield "postgresql+psycopg://postgres:postgres@localhost:5432/stress_chaos"


def _pg_env(settings, deals, url, world, **kw):
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from app.db.store import PgStore

    store = PgStore(url)
    store.create_all()
    pool = ConnectionPool(url.replace("+psycopg", ""), max_size=10, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    cp = PostgresSaver(pool)
    cp.setup()
    env = build_env(settings, deals, store=store, checkpointer=cp, world=world, **kw)
    env.pool = pool
    return env


def _pg_rows(store, table):
    with store.engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(f"SELECT * FROM {table} ORDER BY id"))]


def test_workers_killed_mid_node_then_restart_over_postgres(settings, pg, capsys):
    """60 emails over Postgres + PostgresSaver with 15% provider faults. Eight runs are killed right after a side
    effect (project create, channel create, topic, welcome post) while processing their call result; the process
    then 'restarts' (new container, graph, registry, app over the same DB, checkpointer and external world),
    Retell redelivers the killed runs' webhooks, the scheduler escalates the stuck runs, humans retry them."""
    deals = make_deals(60, seed=21)
    s = stress_settings(settings, stuck_run_minutes=10)
    world = FakeWorld(aes=__import__("stress_harness").directory())
    killable = KillableWorld(world)
    env = _pg_env(s, deals, pg, killable, fault_rate=0.0)
    env.world = world  # assertions look at the raw fake systems
    retries: Counter = Counter()
    killed: dict[str, str] = {}
    with ErrorLog() as errs:
        with UdsServer(env.app) as srv:
            drive(env, srv, max_waves=0)  # all emails in; every runnable run now waits on its first call
        targets = [(rid, cid) for rid, cid in pending_calls(env)
                   if env.sim.persona_of_run(rid) in ("enterprise", "growth")][:8]
        assert len(targets) == 8
        plan = [("rocketlane", "create_project"), ("slack", "create_channel"), ("slack", "set_topic"),
                ("slack", "post")] * 2
        redeliver = []
        for (rid, cid), (prov, method) in zip(targets, plan, strict=True):
            getattr(killable, prov).armed = method
            payload = {"event": "call_analyzed", "call": env.sim.call_object(cid)}
            redeliver.append(payload)
            _, result = map_call_analyzed(payload)
            try:
                env.runner.resume_call(rid, result.model_dump())
                raise AssertionError("kill switch did not fire")
            except Killed:
                killed[rid] = method
        statuses = {rid: env.store.get_run(rid)["status"] for rid in killed}
        assert set(statuses.values()) <= MID_NODE, statuses
        env.pool.close()
        n_projects, n_channels = len(world.rocketlane.projects), len(world.slack.channels)

        # ---- restart: new process over the same Postgres + checkpointer + external world, 15% faults now ----
        env2 = _pg_env(s, deals, pg, world, fault_rate=0.15, llm_fault=0.05, seed=5)
        with UdsServer(env2.app) as srv:
            client = srv.client()
            from stress_harness import signed
            for p in redeliver:  # Retell retries the webhooks the dead worker never finished
                body, headers = signed(p)
                assert client.post("/webhooks/voice", content=body, headers=headers).status_code == 204
            settle(lambda: _sig(env2))
            assert (len(world.rocketlane.projects), len(world.slack.channels)) == (n_projects, n_channels), \
                "a redelivered webhook re-ran a killed run's side effects"
            env2.app.state.tick(now=time.time() + 11 * 60)  # stuck runs -> system escalation; lost calls reconcile
            settle(lambda: _sig(env2))
            stuck_esc = [e for e in env2.store.list_escalations("open") if ":stuck:" in e["key"]]
            assert {e["run_id"] for e in stuck_esc} == set(killed), "every killed run is escalated as stuck"
            drive(env2, srv, push_emails=False)
            for _ in range(4):
                if not _retry_round(env2, client, retries):
                    break
                settle(lambda: _sig(env2))
                drive(env2, srv, push_emails=False)
            env2.providers.calm()
            env2.llm.fault_rate = 0
            _retry_round(env2, client, retries, kinds=("system",))
            settle(lambda: _sig(env2))
            drive(env2, srv, push_emails=False)
    runs = env2.store.list_runs(100_000)
    v = check_invariants(runs=runs, escalations=env2.store.list_escalations(),
                         audit_rows=_pg_rows(env2.store, "audit_log"), world=world, deals=deals, human_retries=retries)
    env2.llm.faults += env.llm.faults
    _report(capsys, "pg restart", env2, runs, retries,
            f"killed {dict(Counter(killed.values()))}; killed runs now "
            f"{dict(Counter(env2.store.get_run(r)['status'] for r in killed))}")
    with capsys.disabled():
        for x in v[:10]:
            print("   ", x)
    _dump(env2, runs, v, _pg_rows(env2.store, "audit_log"))
    env2.pool.close()
    assert v == []
    assert all(env2.store.get_run(r)["status"] in ("COMPLETED", "ESCALATED") for r in killed)
    assert errs.summary() == []
