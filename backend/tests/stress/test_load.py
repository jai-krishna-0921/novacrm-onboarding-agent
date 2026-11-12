"""Load + concurrency through the real FastAPI app under uvicorn (unix socket, background tasks really in the
background): 200 deal emails pushed by racing Gmail pushes, then their Retell webhooks delivered concurrently, out
of order, duplicated, some lost (scheduler reconcile), some stale, some unsigned, interleaved with tick()."""

import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import pytest
from envs import PUSH_BODY, _sig, build_env, drive, pending_calls, stress_settings
from stress_harness import TOKEN, ErrorLog, UdsServer, check_invariants, make_deals, pct, settle, signed

pytestmark = pytest.mark.stress

def test_200_deal_emails_and_chaotic_webhooks_hold_every_invariant(settings, capsys):
    deals = make_deals(200, seed=7)
    env = build_env(stress_settings(settings), deals)
    t0 = time.time()
    with ErrorLog() as errs, UdsServer(env.app) as srv:
        lat, codes, metrics = drive(env, srv)
    runs = env.store.list_runs(100_000)
    violations = check_invariants(runs=runs, escalations=env.store.list_escalations(), audit_rows=env.store.audit_rows,
                                  world=env.world, deals=deals)
    kinds = Counter(d.kind for d in deals)
    with capsys.disabled():
        print(f"\n[load] {len(deals)} emails {dict(kinds)} -> {len(runs)} runs in {time.time() - t0:.1f}s; "
              f"statuses {dict(Counter(r['status'] for r in runs))}; projects {len(env.world.rocketlane.projects)}, "
              f"channels {len(env.world.slack.channels)}, dials {len(env.world.voice.calls)}; {dict(metrics)}; "
              f"codes {dict(codes)}; get_call {env.sim.get_call_hits}")
        for k, v in lat.items():
            print(f"[load] {k}: n={len(v)} p50={pct(v, .5):.1f}ms p95={pct(v, .95):.1f}ms max={max(v):.1f}ms")
    assert violations == []
    assert errs.summary() == []
    assert codes[("voice", 401)] > 0 and all(c in (200, 204, 401) for (_, c) in codes)
    # Client, server and graph share one process (GIL), so this bound is loose; the Gmail push bound is the
    # separate DEFECT test below.
    assert pct(lat["voice"], .95) < 500, "voice webhook must ack fast (work happens in the background)"
    assert metrics["lost"] > 0 and env.sim.get_call_hits > 0, "lost webhooks were exercised and reconciled"


def test_ae_is_phoned_once_per_opportunity_even_when_the_deal_email_is_resent(settings):
    """DEFECT (MEDIUM): a re-sent deal email for an opportunity whose first run is still on the phone (no project
    yet) places a second call to the AE. precheck_duplicate (graph/intake.py) only looks for runs that already have
    a project (store.find_active_run requires rocketlane_project_id), so in-flight runs for the same opportunity are
    invisible and the AE gets two calls for one deal. Measured over the 200-email mix."""
    deals = make_deals(200, seed=7)
    env = build_env(stress_settings(settings), deals)
    with UdsServer(env.app) as srv:
        drive(env, srv)
    runs = {r["id"]: r for r in env.store.list_runs(100_000)}
    dialled_runs_per_opp = defaultdict(set)
    for c in env.world.voice.calls:
        dialled_runs_per_opp[runs[c["run_id"]]["opportunity_id"]].add(c["run_id"])
    twice = {o: len(r) for o, r in dialled_runs_per_opp.items() if len(r) > 1}
    assert twice == {}, f"AE phoned from {sum(twice.values())} runs for {len(twice)} opportunities: {twice}"


def test_gmail_push_ack_is_not_starved_by_a_backlog_of_call_webhooks(settings, capsys):
    """DEFECT (MEDIUM): the Gmail push ack is only fast while the background is idle. Background tasks (sync_inbox,
    resume_call) are sync functions run in Starlette's 40-thread pool and block on the single app lock (main.py
    `lock`); the sync `def gmail_push` endpoint needs a thread from the same (FIFO) pool, so its ack waits behind
    the queued backlog. Measured: 60 queued call webhooks with 150 ms LLM latency -> Gmail push ack ~1.9 s (and
    ~0.8-1 s p95 in the 200-email load with zero-latency fakes). It grows with backlog x lock-hold time; with real
    LLM/Rocketlane/Slack latency (seconds per step) Pub/Sub's 10 s ack deadline is reachable and redeliveries add
    more queued work. The async voice webhook is not affected. Fix direction: make gmail_push `async def` and/or
    hand work to a dedicated worker queue instead of one pool thread per queued task."""
    deals = [d for d in make_deals(400, seed=3) if d.kind == "valid" and d.persona == "enterprise"][:60]
    env = build_env(stress_settings(settings), deals, llm_latency=0.15)
    env.store.set_kv("gmail_history_id", "1")
    env.inbox.add(*[d.email for d in deals])
    with UdsServer(env.app) as srv, ThreadPoolExecutor(64) as pool:
        client = srv.client()
        client.post(f"/webhooks/gmail?token={TOKEN}", json=PUSH_BODY)
        settle(lambda: _sig(env))
        pend = pending_calls(env)
        assert len(pend) == len(deals)

        def voice(cid):
            body, headers = signed({"event": "call_analyzed", "call": env.sim.call_object(cid)})
            return client.post("/webhooks/voice", content=body, headers=headers).status_code

        codes = list(pool.map(voice, [cid for _, cid in pend]))
        assert set(codes) == {204}
        timings = {}
        for path, call in (("gmail", lambda: client.post(f"/webhooks/gmail?token={TOKEN}", json=PUSH_BODY)),
                           ("healthz", lambda: client.get("/healthz"))):
            t = time.perf_counter()
            assert call().status_code == 200
            timings[path] = (time.perf_counter() - t) * 1000
        settle(lambda: _sig(env))
    with capsys.disabled():
        print(f"\n[latency] after 60 queued call webhooks: {({k: round(v) for k, v in timings.items()})} ms")
    assert timings["gmail"] < 500 and timings["healthz"] < 500, timings
