"""Builds an app container wired to fake (optionally faulty) providers over MemoryStore or Postgres."""

import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver
from stress_harness import (
    KEY,
    TOKEN,
    ChaosProviders,
    RetellSim,
    StressInbox,
    StressLLM,
    directory,
    seed_store_aes,
    settle,
    signed,
)

from app.container import Container
from app.graph.root import build_graph
from app.graph.state import Deps
from app.main import RECONCILE_AFTER, create_app
from app.providers.fake import FakeWorld
from app.services.runner import Runner
from app.services.store import MemoryStore
from app.tools.definitions import build_registry

CONTACTS = [{"id": i, "name": f"Contact {i}", "email": f"c{i}@cust.example"} for i in range(1, 21)]


def stress_settings(settings, **kw):
    return settings.model_copy(update={"gmail_push_token": TOKEN, "retell_api_key": KEY,
                                       "call_retry_delay_seconds": 0, **kw})


def build_env(settings, deals, *, store=None, checkpointer=None, world=None, fault_rate=0.0, llm_fault=0.0,
              llm_latency=0.0, seed=0, start_poller=False):
    store = store if store is not None else MemoryStore()
    seed_store_aes(store)
    world = world or FakeWorld(aes=directory())
    providers = ChaosProviders(world, fault_rate, seed) if fault_rate else world
    llm = StressLLM(deals, llm_fault, seed, llm_latency)
    llm.store = store
    registry = build_registry(store, providers, sleep=lambda s: None)
    deps = Deps(settings, store, registry, llm, sleep=lambda s: None)
    graph = build_graph(deps, checkpointer if checkpointer is not None else InMemorySaver())
    runner = Runner(graph, store)
    by_msg = {d.email.message_id: d for d in deals}

    def persona(rid: str) -> str:
        return by_msg[store.get_run(rid)["gmail_message_id"]].persona

    sim = RetellSim(world, persona)
    inbox = StressInbox()
    c = Container(settings=settings, store=store, runner=runner, inbox=inbox, start_poller=start_poller, voice=sim,
                  registry=registry, llm=llm, migration_contacts=lambda: (list(CONTACTS), list(CONTACTS)))
    app = create_app(c)
    return SimpleNamespace(store=store, world=world, providers=providers, llm=llm, registry=registry, runner=runner,
                           sim=sim, inbox=inbox, container=c, app=app, settings=settings, deals=deals)


def pending_calls(env) -> list[tuple[str, str]]:
    out = []
    for r in env.store.list_runs(100_000):
        if r.get("status") == "AWAITING_CALL":
            p = env.runner.pending(r["id"])
            if p and p.get("type") == "call":
                out.append((r["id"], p["provider_call_id"]))
    return out


PUSH_BODY = {"message": {"data": "e30=", "messageId": "1"}}


def _sig(env):
    if hasattr(env.store, "audit_rows"):
        return len(env.store.audit_rows), len(env.store.tool_calls), len(env.world.voice.calls)
    from sqlalchemy import text
    with env.store.engine.connect() as c:
        counts = tuple(c.execute(text("SELECT (SELECT count(*) FROM audit_log), (SELECT count(*) FROM tool_calls)")).one())
    return counts, len(env.world.voice.calls)


def drive(env, srv, *, n_threads=32, lost=.10, dup=.25, stale=.5, seed=11, max_waves=15, push_emails=True):
    """Push all emails, then deliver webhooks wave by wave until no run waits on a call. Returns metrics."""
    rng = random.Random(seed)
    lat, codes, metrics = defaultdict(list), Counter(), Counter()
    client = srv.client()

    def timed(kind, fn):
        t = time.perf_counter()
        r = fn()
        lat[kind].append((time.perf_counter() - t) * 1000)
        codes[(kind, r.status_code)] += 1
        return r

    def push():
        return timed("gmail", lambda: client.post(f"/webhooks/gmail?token={TOKEN}", json=PUSH_BODY))

    def voice(payload, key=None):
        body, headers = signed(payload) if key is None else signed(payload, key)
        return timed("voice", lambda: client.post("/webhooks/voice", content=body, headers=headers))

    def ticker(stop_at):
        while time.time() < stop_at:
            env.app.state.tick()  # real "now": nothing due yet, but contends for the lock with the webhooks
            metrics["ticks"] += 1
            time.sleep(0.01)

    if push_emails:
        env.store.set_kv("gmail_history_id", "1")
    with ThreadPoolExecutor(n_threads) as pool:
        emails = [d.email for d in env.deals] if push_emails else []
        for b in range(0, len(emails), 20):
            env.inbox.add(*emails[b:b + 20])
            wait([pool.submit(push) for _ in range(8)] + [pool.submit(ticker, time.time() + 0.05)])
        settle(lambda: _sig(env))
        for _ in range(max_waves):
            pend = pending_calls(env)
            if not pend:
                break
            metrics["waves"] += 1
            batch = []
            for rid, cid in pend:
                if rng.random() < lost:
                    metrics["lost"] += 1
                    continue
                obj = env.sim.call_object(cid)
                batch.append(("ok", {"event": "call_analyzed", "call": obj}))
                if rng.random() < dup:
                    metrics["duplicated"] += 1
                    batch.append(("ok", {"event": "call_analyzed", "call": obj}))
                if rng.random() < .1:
                    batch.append(("ok", {"event": "call_ended", "call": obj}))  # acked, ignored
                if rng.random() < .05:
                    batch.append(("forged", {"event": "call_analyzed", "call": obj}))
                older = [c for c in env.sim.calls_of(rid) if c != cid]
                if older and rng.random() < stale:
                    metrics["stale"] += 1
                    batch.append(("ok", {"event": "call_analyzed", "call": env.sim.call_object(older[0])}))
            rng.shuffle(batch)
            futs = [pool.submit(voice, p, "wrong-key" if k == "forged" else None) for k, p in batch]
            futs += [pool.submit(push) for _ in range(6)] + [pool.submit(ticker, time.time() + 0.2)]
            wait(futs)
            settle(lambda: _sig(env))
            env.app.state.tick(now=time.time() + RECONCILE_AFTER + 5)  # lost webhooks: ask "Retell"
            settle(lambda: _sig(env))
    return lat, codes, metrics


