"""Observability API for the dashboard sidebar pages: login required, paged + filtered, aggregates computed in the
store, and tool-call payloads (customer data) only on the per-row detail request."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.main import create_app

ENDPOINTS = ["/metrics/llm", "/metrics/tools", "/metrics/overview", "/llm-calls", "/tool-calls", "/tool-calls/1"]


def _client(settings, ent=None, **overrides):
    from app.sim import Harness

    s = settings.model_copy(update=overrides)
    h = ent or Harness(s)
    return TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False)),
                      follow_redirects=False), h.store


@pytest.fixture
def api(settings, ent):
    h, rid = ent
    c, store = _client(settings, h)
    return c, store, rid


def _days_ago(n: int, hour: int = 10) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_observability_endpoint_requires_login(settings, path):
    c, _ = _client(settings, auth_disabled=False, admin_username="admin", admin_password="pw",
                   session_secret="s")
    r = c.get(path)
    assert r.status_code in (401, 303, 307), (path, r.status_code)
    assert "rows" not in r.text and "totals" not in r.text


def test_tool_call_list_never_returns_inputs_or_outputs(api):
    c, _, rid = api
    body = c.get("/tool-calls", params={"limit": 200}).json()
    assert body["total"] > 0 and body["rows"]
    for row in body["rows"]:
        assert "args" not in row and "result" not in row
    assert "phone" not in c.get("/tool-calls", params={"limit": 200}).text


def test_tool_call_detail_has_payload_with_phones_masked(api):
    c, store, rid = api
    lookup = c.get("/tool-calls", params={"tool": "ae_lookup", "run_id": rid}).json()["rows"][0]
    d = c.get(f"/tool-calls/{lookup['id']}").json()
    assert d["args"] and d["result"]["phone"].startswith("*") and d["result"]["phone"][-4:].isdigit()
    # defence in depth: even a row written without redaction is masked on the way out
    store.add_tool_call(dict(run_id=rid, ts=_days_ago(0), agent="intake", tool="ae_lookup",
                             args={"to_number": "+15551234567"}, result={"phone": "+15551234567"}, status="ok",
                             attempt=1, latency_ms=3, idempotency_key=None))
    newest = c.get("/tool-calls", params={"limit": 1}).json()["rows"][0]
    raw = c.get(f"/tool-calls/{newest['id']}").text
    assert "+15551234567" not in raw and "4567" in raw
    assert c.get("/tool-calls/99999").status_code == 404


def test_tool_calls_paginate_and_filter(api):
    c, _, rid = api
    total = c.get("/tool-calls").json()["total"]
    assert total >= 4
    p1 = c.get("/tool-calls", params={"limit": 2, "offset": 0}).json()
    p2 = c.get("/tool-calls", params={"limit": 2, "offset": 2}).json()
    assert p1["total"] == total and len(p1["rows"]) == 2 and p1["limit"] == 2 and p2["offset"] == 2
    assert not {r["id"] for r in p1["rows"]} & {r["id"] for r in p2["rows"]}
    assert p1["rows"][0]["id"] > p1["rows"][1]["id"] > p2["rows"][0]["id"]  # newest first
    only = c.get("/tool-calls", params={"tool": "slack_create_channel", "agent": "comms", "status": "ok"}).json()
    assert only["total"] >= 1 and {r["tool"] for r in only["rows"]} == {"slack_create_channel"}
    assert c.get("/tool-calls", params={"status": "failed"}).json()["total"] == 0
    assert c.get("/tool-calls", params={"run_id": "nope"}).json() == {"total": 0, "limit": 25, "offset": 0, "rows": []}
    row = c.get("/tool-calls", params={"run_id": rid}).json()["rows"][0]
    assert {"ts", "run_id", "customer_name", "agent", "tool", "status", "attempt", "latency_ms",
            "idempotency_key"} <= set(row)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"since": "yesterday"}])
def test_paging_and_since_are_validated(api, params):
    c, _, _ = api
    for path in ("/tool-calls", "/llm-calls"):
        assert c.get(path, params=params).status_code == 422, (path, params)


def test_llm_calls_paginate_filter_and_carry_run_customer(api):
    c, store, rid = api
    for i in range(6):
        store.add_llm_call(dict(run_id=rid, ts=_days_ago(0), agent="intake", node="extract_fields",
                                model="claude-haiku-5-5", prompt_version="extract_v3", input_tokens=100,
                                output_tokens=20, latency_ms=900 + i, cost_usd=0.00002, stop_reason="end_turn"))
    all_ = c.get("/llm-calls").json()
    assert all_["total"] >= 6
    haiku = c.get("/llm-calls", params={"model": "claude-haiku-5-5", "limit": 5}).json()
    assert haiku["total"] == 6 and len(haiku["rows"]) == 5
    r = haiku["rows"][0]
    assert r["latency_ms"] == 905 and r["customer_name"] and r["stop_reason"] == "end_turn" and r["cost_usd"] == 0.00002
    assert c.get("/llm-calls", params={"node": "extract_fields", "run_id": rid}).json()["total"] >= 6
    assert c.get("/llm-calls", params={"node": "nope"}).json()["total"] == 0


def test_llm_metrics_aggregate_percentiles_and_fill_the_daily_series(settings):
    c, store = _client(settings)
    for lat, day in ((100, 0), (200, 0), (300, 1), (400, 1), (1000, 3)):
        store.add_llm_call(dict(run_id="r", ts=_days_ago(day), agent="intake", node="extract_fields",
                                model="claude-haiku-5-5", prompt_version="extract_v3", input_tokens=1000,
                                output_tokens=100, latency_ms=lat, cost_usd=0.00015, stop_reason="end_turn"))
    store.add_llm_call(dict(run_id="r", ts=_days_ago(40), agent="comms", node="draft_welcome", model="fake",
                            prompt_version="welcome_v1", input_tokens=0, output_tokens=0, latency_ms=0, cost_usd=0))
    m = c.get("/metrics/llm", params={"days": 7}).json()
    t = m["totals"]
    assert t["calls"] == 6 and t["input_tokens"] == 5000 and t["cost_usd"] == 0.00075
    haiku = next(g for g in m["by_model"] if g["model"] == "claude-haiku-5-5")
    assert (haiku["p50_latency_ms"], haiku["p95_latency_ms"], haiku["avg_latency_ms"]) == (300, 880, 400)
    assert {g["prompt_version"] for g in m["by_prompt"]} == {"extract_v3", "welcome_v1"}
    assert {(g["agent"], g["node"]) for g in m["by_node"]} == {("intake", "extract_fields"), ("comms", "draft_welcome")}
    series = m["series"]
    assert len(series) == 7 and series[-1]["day"] == datetime.now(UTC).date().isoformat()
    assert [d["calls"] for d in series][-4:] == [1, 0, 2, 2]  # missing day filled with zero; 40-day-old row outside
    assert sum(m["latency_buckets"]) == 6 and m["latency_bucket_labels"][0] == "< 1 s"
    assert m["prices_estimated"] is True and m["prices_usd_per_mtok"]["claude-haiku-5-5"] == [0.1, 0.5]
    recent = c.get("/metrics/llm", params={"since": _days_ago(2)}).json()
    assert recent["totals"]["calls"] == 4
    assert c.get("/metrics/llm", params={"days": 0}).status_code == 422


def test_tool_metrics_failure_rate_and_p95(settings):
    c, store = _client(settings)
    for i, (status, lat) in enumerate((("ok", 10), ("ok", 20), ("ok", 30), ("failed", 40))):
        store.add_tool_call(dict(run_id="r", ts=_days_ago(0), agent="intake", tool="voice_start_call", args={},
                                 result={}, status=status, attempt=i + 1, latency_ms=lat, idempotency_key=None))
    store.add_audit(dict(run_id="r", ts=_days_ago(0), agent="intake", step="tool:voice_start_call", input=None,
                         output=None, rationale="x", status="replayed"))
    [v] = c.get("/metrics/tools").json()["tools"]
    assert (v["tool"], v["calls"], v["ok"], v["failed"], v["replayed"]) == ("voice_start_call", 4, 3, 1, 1)
    assert v["failure_rate"] == 0.25 and v["p95_latency_ms"] == 39 and v["avg_latency_ms"] == 25


def test_overview_reflects_a_completed_run(api):
    c, _, rid = api
    o = c.get("/metrics/overview").json()
    assert o["runs_total"] == 1 and o["runs_by_status"] == {"COMPLETED": 1}
    assert o["calls"]["placed"] == 1 and o["calls"]["confirmed"] == 1 and o["calls"]["unclear"] == 0
    assert o["email_to_project"]["runs"] == 1 and o["email_to_project"]["avg_seconds"] >= 0
    assert o["escalations_open"] == 0 and o["llm_calls"] >= 1 and "llm_cost_usd" in o
