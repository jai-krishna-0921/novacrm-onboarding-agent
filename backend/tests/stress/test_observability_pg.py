"""Observability API over Postgres with 10k+ rows: aggregates vs a brute-force recomputation (and vs MemoryStore
fed the same rows), pagination stability, filter combos, latency, auth, payload hiding, phone masking, and hostile
query parameters."""

import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from app.container import Container
from app.main import create_app
from app.services.store import TOOL_ROW_FIELDS, MemoryStore, percentile

pytestmark = pytest.mark.stress
PG_ADMIN = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
URL = "postgresql+psycopg://postgres:postgres@localhost:5432/stress_obs"
N_RUNS, N_TOOLS, N_LLM, N_AUDIT = 300, 12_000, 10_000, 4_000
TOOLS = ["ae_lookup", "voice_start_call", "rocketlane_create_project", "rocketlane_find_project",
         "slack_create_channel", "slack_post_message", "rocketlane_list_tasks"]
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]
NODES = [("intake", "extract_fields"), ("intake", "interpret_call"), ("comms", "draft_welcome"),
         ("tracking", "draft_handoff")]
ORIGIN = {"Origin": "http://testserver"}


def _ts(rng, now):
    t = now - timedelta(days=rng.uniform(0, 20))
    if rng.random() < .05:
        t = t.replace(microsecond=0)  # isoformat drops ".000000": string comparison edge case
    return t.isoformat()


def _data(seed=1):
    rng, now = random.Random(seed), datetime.now(UTC)
    runs = [dict(id=f"run-{i:04d}", seq=i + 1, status=rng.choice(["COMPLETED", "ESCALATED", "NOT_A_DEAL", "HANDED_OFF"]),
                 customer_name=f"Cust {i}' OR '1'='1" if i == 7 else f"Cust {i}", created_at=_ts(rng, now))
            for i in range(N_RUNS)]
    for r in runs:
        r["updated_at"] = r["created_at"]
    tools = []
    for i in range(1, N_TOOLS + 1):
        tool = rng.choice(TOOLS)
        tools.append(dict(id=i, run_id=rng.choice(runs)["id"] if rng.random() > .02 else "orphan-run", ts=_ts(rng, now),
                          agent=rng.choice(["intake", "comms", "tracking"]), tool=tool,
                          args={"to_number": "+14155550123"} if tool == "voice_start_call" else {"x": i},
                          result={"phone": "+14155550199"} if i % 97 == 0 else {"ok": True},
                          status=rng.choice(["ok"] * 8 + ["failed"]), attempt=rng.randint(0, 3),
                          latency_ms=None if rng.random() < .03 else int(rng.expovariate(1 / 900)),
                          idempotency_key=None if rng.random() < .5 else f"k{i}"))
    llm = []
    for i in range(1, N_LLM + 1):
        agent, node = rng.choice(NODES)
        llm.append(dict(id=i, run_id=rng.choice(runs)["id"], ts=_ts(rng, now), agent=agent, node=node,
                        model=rng.choice(MODELS), prompt_version=rng.choice(["extract_v3", "welcome_v2", None]),
                        input_tokens=rng.randint(100, 4000), output_tokens=rng.randint(10, 800),
                        latency_ms=None if rng.random() < .02 else int(rng.expovariate(1 / 2500)),
                        cost_usd=Decimal(f"{rng.uniform(0, 0.02):.6f}"), stop_reason="end_turn"))
    audit = [dict(id=i, run_id=rng.choice(runs)["id"], ts=_ts(rng, now), agent="intake",
                  step=rng.choice([f"tool:{rng.choice(TOOLS)}", "decide_tier"]),
                  status=rng.choice(["replayed", "blocked", "ok", "confirmed", "unclear", "no_answer"]),
                  rationale="r", input=None, output=None) for i in range(1, N_AUDIT + 1)]
    esc = [dict(key=f"run-{i:04d}:1", run_id=f"run-{i:04d}", kind="tier", agent="intake",
                status=rng.choice(["open", "resolved"]), reason="x", created_at=_ts(rng, now)) for i in range(60)]
    return runs, tools, llm, audit, esc


@pytest.fixture(scope="module")
def stores():
    pytest.importorskip("psycopg")
    import socket as _s  # noqa: F401

    from pytest_socket import enable_socket
    enable_socket()
    try:
        admin = create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.exec_driver_sql("DROP DATABASE IF EXISTS stress_obs WITH (FORCE)")
            c.exec_driver_sql("CREATE DATABASE stress_obs")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable (make up): {e}")
    from app.db import store as db

    pg = db.PgStore(URL)
    pg.create_all()
    runs, tools, llm, audit, esc = _data()
    with pg.engine.begin() as c:
        c.execute(insert(db.runs), runs)
        c.execute(insert(db.tool_calls), tools)
        c.execute(insert(db.llm_calls), llm)
        c.execute(insert(db.audit_log), audit)
        c.execute(insert(db.escalations), esc)
    mem = MemoryStore()
    mem.runs = {r["id"]: dict(r) for r in runs}
    mem.tool_calls = [dict(t) for t in tools]
    mem.llm_calls = [{**x, "cost_usd": float(x["cost_usd"])} for x in llm]
    mem.audit_rows = [dict(a) for a in audit]
    mem.escalations = {e["key"]: dict(e) for e in esc}
    yield pg, mem, (runs, tools, llm, audit, esc)
    pg.engine.dispose()


def _client(settings, store, auth=False):
    s = settings.model_copy(update={"auth_disabled": not auth, "admin_password": "pw-123456" if auth else "",
                                    "session_secret": "x"})
    return TestClient(create_app(Container(settings=s, store=store, runner=None, start_poller=False)),
                      follow_redirects=False)


def _close(a, b, path=""):
    """Equal up to float rounding (6 dp) and +-1 ms on rounded latency averages."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), f"{path}: keys {a.keys() ^ b.keys()}"
        for k in a:
            _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _close(x, y, f"{path}[{i}]")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        tol = 1 if "latency" in path else 2e-6
        assert abs(a - b) <= tol, f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


SINCE = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()


@pytest.mark.parametrize("path", ["/metrics/overview", "/metrics/llm", f"/metrics/llm?since={SINCE}", "/metrics/tools",
                                  f"/metrics/tools?since={SINCE}T06:30:00%2B05:30"])
def test_aggregates_match_memory_store_on_the_same_rows(settings, stores, path):
    pg, mem, _ = stores
    a, b = _client(settings, pg).get(path), _client(settings, mem).get(path)
    assert a.status_code == b.status_code == 200
    _close(a.json(), b.json(), path)


def test_aggregates_match_a_brute_force_recomputation(settings, stores):
    pg, _, (runs, tools, llm, audit, esc) = stores
    c = _client(settings, pg)
    ov = c.get("/metrics/overview").json()
    assert ov["runs_total"] == N_RUNS and ov["llm_calls"] == N_LLM
    assert ov["escalations_open"] == sum(e["status"] == "open" for e in esc)
    assert ov["calls"]["placed"] == sum(t["tool"] == "voice_start_call" and t["status"] == "ok" for t in tools)
    assert abs(ov["llm_cost_usd"] - float(sum(x["cost_usd"] for x in llm))) < 1e-6
    for k in ("confirmed", "unclear", "no_answer"):
        assert ov["calls"][k] == sum(a["step"] == "decide_tier" and a["status"] == k for a in audit)
    st = {t["tool"]: t for t in c.get("/metrics/tools").json()["tools"]}
    for tool in TOOLS:
        rows = [t for t in tools if t["tool"] == tool]
        lat = [t["latency_ms"] for t in rows if t["latency_ms"] is not None]
        assert st[tool]["calls"] == len(rows)
        assert st[tool]["failed"] == sum(t["status"] == "failed" for t in rows)
        assert st[tool]["failure_rate"] == round(st[tool]["failed"] / len(rows), 4)
        assert abs(st[tool]["p95_latency_ms"] - percentile(lat, .95)) <= 1
        assert st[tool]["replayed"] == sum(a["step"] == f"tool:{tool}" and a["status"] == "replayed" for a in audit)
    m = c.get("/metrics/llm").json()
    lat = [x["latency_ms"] for x in llm if x["latency_ms"] is not None]
    assert m["totals"]["calls"] == N_LLM and abs(m["totals"]["p95_latency_ms"] - percentile(lat, .95)) <= 1
    assert sum(m["latency_buckets"]) == len(lat)
    assert sum(d["calls"] for d in m["by_day"]) == N_LLM


@pytest.mark.parametrize("path,flt", [
    ("/tool-calls", {}), ("/tool-calls", {"tool": "voice_start_call"}), ("/tool-calls", {"status": "failed"}),
    ("/tool-calls", {"tool": "slack_post_message", "agent": "comms", "status": "ok"}),
    ("/tool-calls", {"since": SINCE, "status": "ok"}), ("/tool-calls", {"run_id": "run-0042"}),
    ("/llm-calls", {}), ("/llm-calls", {"model": "claude-haiku-5-5", "node": "draft_welcome"}),
    ("/llm-calls", {"since": SINCE, "model": "claude-sonnet-5-5"}),
])
def test_pagination_walk_has_no_dupes_or_gaps_and_matches_memory(settings, stores, path, flt):
    pg, mem, (runs, tools, llm, *_) = stores
    rows_src = tools if path == "/tool-calls" else llm
    want = [r["id"] for r in rows_src if all(
        (str(r["ts"]) >= _norm(v)) if k == "since" else r.get(k) == v for k, v in flt.items())]
    c, cm = _client(settings, pg), _client(settings, mem)
    got, offset, total = [], 0, None
    while True:
        page = c.get(path, params={**flt, "limit": 200, "offset": offset}).json()
        total = page["total"] if total is None else total
        assert page["total"] == total
        mpage = cm.get(path, params={**flt, "limit": 200, "offset": offset}).json()
        assert [r["id"] for r in page["rows"]] == [r["id"] for r in mpage["rows"]]
        _close(page["rows"], mpage["rows"], path)
        got += [r["id"] for r in page["rows"]]
        if len(page["rows"]) < 200:
            break
        offset += 200
    assert len(got) == len(set(got)) == total == len(want)
    assert got == sorted(want, reverse=True)


def _norm(raw):
    d = datetime.fromisoformat(raw)
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def test_list_rows_never_carry_payloads_or_phones_and_detail_masks_them(settings, stores):
    pg, *_ = stores
    c = _client(settings, pg)
    body = c.get("/tool-calls", params={"tool": "voice_start_call", "limit": 200}).text
    assert "+1415555" not in body
    rows = c.get("/tool-calls", params={"limit": 200}).json()["rows"]
    assert all(tuple(r) == TOOL_ROW_FIELDS for r in rows)
    voice = c.get("/tool-calls", params={"tool": "voice_start_call", "limit": 1}).json()["rows"][0]
    d = c.get(f"/tool-calls/{voice['id']}").json()
    assert d["args"]["to_number"].endswith("0123") and "+1415555" not in str(d)
    leaky = c.get("/tool-calls/97").json()  # a writer that skipped redaction: masked again on read
    assert "+1415555" not in str(leaky)


def test_response_times_with_10k_rows(settings, stores, capsys):
    pg, *_ = stores
    c = _client(settings, pg)
    paths = ["/metrics/overview", "/metrics/llm", "/metrics/tools", "/tool-calls?limit=200&offset=11800",
             "/llm-calls?limit=200&model=claude-haiku-5-5", "/tool-calls/5000", f"/tool-calls?since={SINCE}&status=failed"]
    times = {}
    for p in paths:
        c.get(p)  # warm
        t = time.perf_counter()
        for _ in range(5):
            assert c.get(p).status_code == 200
        times[p] = (time.perf_counter() - t) / 5 * 1000
    with capsys.disabled():
        print("\n[obs latency, PG 12k tool / 10k llm rows] " + ", ".join(f"{k}={v:.0f}ms" for k, v in times.items()))
    assert max(times.values()) < 500, times


@pytest.mark.parametrize("path", ["/metrics/overview", "/metrics/llm", "/metrics/tools", "/llm-calls", "/tool-calls",
                                  "/tool-calls/1", "/runs", "/escalations"])
def test_every_observability_endpoint_needs_login_with_real_data(settings, stores, path):
    pg, *_ = stores
    r = _client(settings, pg, auth=True).get(path)
    assert r.status_code == 401 and "Cust" not in r.text


@pytest.mark.parametrize("params", [{"tool": "x' OR '1'='1"}, {"status": "ok'; DROP TABLE tool_calls; --"},
                                    {"run_id": "run-0007' OR '1'='1"}, {"agent": "%"}, {"tool": "_"}])
def test_filters_are_bound_parameters_not_sql(settings, stores, params):
    pg, *_ = stores
    r = _client(settings, pg).get("/tool-calls", params=params)
    assert r.status_code == 200 and r.json()["total"] == 0
    assert _client(settings, pg).get("/tool-calls").json()["total"] == N_TOOLS  # table still there


@pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"offset": -1}, {"since": "yesterday"},
                                    {"since": "2026-13-01"}, {"limit": "1e9"}])
def test_hostile_paging_params_are_rejected(settings, stores, params):
    pg, *_ = stores
    assert _client(settings, pg).get("/tool-calls", params=params).status_code == 422


@pytest.mark.parametrize("path", ["/tool-calls?offset=99999999999999999999", "/llm-calls?offset=9223372036854775808",
                                  "/tool-calls/99999999999999999999", "/metrics/llm?days=9999999999999999999999"])
def test_huge_integers_are_a_client_error_not_a_500(settings, stores, path):
    """DEFECT (LOW): `offset` has no upper bound and `/tool-calls/{call_id}` accepts any int, so values beyond
    BIGINT reach Postgres and raise (NumericValueOutOfRange) -> HTTP 500 with a stack trace in the log. MemoryStore
    hides it. Fix: bound offset (e.g. le=10**9) and call_id (le=2**63-1) in main.py."""
    pg, *_ = stores
    r = _client(settings, pg).get(path)
    assert r.status_code in (200, 404, 422), f"{path} -> {r.status_code}"
