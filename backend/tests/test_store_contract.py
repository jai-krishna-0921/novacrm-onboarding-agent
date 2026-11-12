"""MemoryStore and PgStore must behave identically (PgStore runs against local Docker Postgres, else skips)."""

import pytest

from app.services.store import MemoryStore


@pytest.fixture(params=["memory", "pg"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    yield request.getfixturevalue("pg_store")  # conftest: shared test database, emptied per test


def test_idempotency_roundtrip(store):
    assert store.get_idem("k") is None
    store.put_idem("k", {"project_id": "42"})
    assert store.get_idem("k") == {"project_id": "42"}


def test_idempotency_reservation_is_hidden_until_completed(store):
    """A reserved (in-flight) key is not a result; completing it overwrites the marker; release drops it."""
    assert store.reserve_idem("k") is None                      # fresh reservation
    assert store.get_idem("k") is None                          # in flight is not a result
    assert "started_at" in store.reserve_idem("k")              # second reserve sees the in-flight marker
    store.put_idem("k", {"project_id": "42"})                   # completion replaces the marker
    assert store.get_idem("k") == {"project_id": "42"}
    assert store.reserve_idem("k") == {"result": {"project_id": "42"}}
    store.release_idem("k")                                     # never drops a completed result
    assert store.get_idem("k") == {"project_id": "42"}
    store.reserve_idem("k2")
    store.release_idem("k2")
    assert store.reserve_idem("k2") is None                     # released: free to reserve again
    assert "started_at" in store.peek_idem("k2") and store.peek_idem("nope") is None
    assert store.peek_idem("k") == {"result": {"project_id": "42"}}


def test_runs_upsert_and_lookup(store):
    store.upsert_run("r1", status="RECEIVED", gmail_message_id="gm-1")
    store.upsert_run("r1", status="AWAITING_CALL")
    run = store.get_run("r1")
    assert run["status"] == "AWAITING_CALL" and run["gmail_message_id"] == "gm-1" and run["created_at"]
    assert store.find_run_by_message("gm-1")["id"] == "r1"
    assert store.find_run_by_message("nope") is None


def test_find_active_run_needs_a_project_or_an_in_flight_call_and_skips_failed_and_self(store):
    store.upsert_run("a", status="PROJECT_CREATED", opportunity_id="006A", customer_key="globex",
                     rocketlane_project_id="7")
    store.upsert_run("b", status="FAILED", opportunity_id="006B", customer_key="initech", rocketlane_project_id="8")
    store.upsert_run("c", status="AWAITING_CALL", opportunity_id="006C", customer_key="umbrella")
    assert store.find_active_run(opportunity_id="006A", customer_key="x", exclude_run_id="z")["id"] == "a"
    assert store.find_active_run(opportunity_id=None, customer_key="globex", exclude_run_id="z")["id"] == "a"
    assert store.find_active_run(opportunity_id="006A", customer_key="globex", exclude_run_id="a") is None
    assert store.find_active_run(opportunity_id="006B", customer_key="initech", exclude_run_id="z") is None
    # In flight (on the phone, no project yet) is active too: a re-sent deal email must not phone the AE again.
    assert store.find_active_run(opportunity_id="006C", customer_key="x", exclude_run_id="z")["id"] == "c"
    store.upsert_run("d", status="NOT_A_DEAL", opportunity_id="006D", customer_key="hooli")
    store.upsert_run("e", status="ESCALATED", opportunity_id="006E", customer_key="pied")  # no project, not calling
    assert store.find_active_run(opportunity_id="006D", customer_key="hooli", exclude_run_id="z") is None
    assert store.find_active_run(opportunity_id="006E", customer_key="pied", exclude_run_id="z") is None


def test_state_queries_see_every_matching_run_however_old(store):
    store.upsert_run("old-wait", status="AWAITING_RETRY")
    store.upsert_run("old-proj", status="COMPLETED", rocketlane_project_id="1", slack_channel_id="C1")
    store.upsert_run("handed", status="HANDED_OFF", rocketlane_project_id="2")
    store.upsert_tracking("handed", handed_off_at="2026-11-12T00:00:00")
    store.upsert_run("dead", status="CANCELLED", rocketlane_project_id="3")
    store.upsert_run("nostatus", rocketlane_project_id="4")
    for i in range(30):
        store.upsert_run(f"f{i}", status="NOT_A_DEAL")
    assert [r["id"] for r in store.list_runs_by_status({"AWAITING_RETRY", "RECEIVED"})] == ["old-wait"]
    assert [r["id"] for r in store.list_trackable_runs({"CANCELLED", "FAILED"})] == ["old-proj", "nostatus"]
    assert store.find_run_by_channel("C1")["id"] == "old-proj" and store.find_run_by_channel("C9") is None
    assert store.get_run("f0")["slack_channel_id"] is None  # every column present, same shape in both stores


def test_audit_tool_and_llm_rows_are_kept_in_order(store):
    for i in range(3):
        store.add_audit(dict(run_id="r1", ts=f"2026-11-11T00:00:0{i}", agent="intake", step=f"s{i}",
                             input={"i": i}, output=None, rationale="why", status="ok"))
    store.add_tool_call(dict(run_id="r1", ts="t", agent="intake", tool="x", args={}, result={}, status="ok",
                             attempt=1, latency_ms=5, idempotency_key=None))
    store.add_llm_call(dict(run_id="r1", ts="t", agent="intake", node="n", model="m", prompt_version="p",
                            input_tokens=1, output_tokens=2, latency_ms=3, cost_usd=0.000001))
    assert [r["step"] for r in store.list_audit("r1")] == ["s0", "s1", "s2"]
    assert store.list_audit("r1")[0]["input"] == {"i": 0}
    assert len(store.list_tool_calls("r1")) == 1 and store.list_llm_calls("r1")[0]["output_tokens"] == 2


def test_escalations_upsert_and_list_open(store):
    store.upsert_escalation("r1:1", dict(run_id="r1", reason="no answer", kind="tier", agent="intake", status="open"))
    assert [e["key"] for e in store.list_escalations(status="open")] == ["r1:1"]
    store.upsert_escalation("r1:1", dict(status="resolved", resolved_by="priya", resolution={"action": "cancel"}))
    assert store.list_escalations(status="open") == []
    [e] = store.list_escalations()
    assert e["resolution"] == {"action": "cancel"} and e["reason"] == "no answer"


def test_kv_cursor(store):
    assert store.get_kv("gmail_history_id") is None
    store.set_kv("gmail_history_id", "123")
    store.set_kv("gmail_history_id", "456")
    assert store.get_kv("gmail_history_id") == "456"


def test_ae_directory(store):
    store.upsert_ae("Jai@NovaCRM.example", "Jai Krishna", "+919876543210", True)
    ae = store.lookup_ae("jai@novacrm.example")
    assert ae == {"email": "jai@novacrm.example", "name": "Jai Krishna", "phone": "+919876543210", "active": True}
    assert store.lookup_ae("ghost@x.example") is None


def test_migration_checks_roundtrip_per_run(store):
    for name, ok in (("record_count", True), ("duplicates", False)):
        store.add_migration_check(dict(run_id="r1", batch_id="b1", ts="t", check_name=name, passed=ok,
                                       details={"expected": 0, "actual": int(not ok), "sample_ids": [23]}))
    store.add_migration_check(dict(run_id="r2", batch_id="b2", ts="t", check_name="duplicates", passed=True,
                                   details={}))
    rows = store.list_migration_checks("r1")
    assert [(r["check_name"], r["passed"]) for r in rows] == [("record_count", True), ("duplicates", False)]
    assert rows[1]["details"]["sample_ids"] == [23] and rows[0]["batch_id"] == "b1"


def test_list_runs_newest_first(store):
    store.upsert_run("r1", status="COMPLETED")
    store.upsert_run("r2", status="RECEIVED")
    assert [r["id"] for r in store.list_runs()][:2] == ["r2", "r1"]


def test_tracking_upsert_merges_fields_and_rejects_unknown(store):
    assert store.get_tracking("r1") is None
    tasks = [{"task_id": "1", "name": "Kickoff call", "phase": "Kickoff", "completed": True}]
    store.upsert_tracking("r1", tasks=tasks, synced_at="2026-11-11T10:00:00")
    store.upsert_tracking("r1", handoff_summary="all done")
    t = store.get_tracking("r1")
    assert t["tasks"] == tasks and t["synced_at"] == "2026-11-11T10:00:00"
    assert t["handoff_summary"] == "all done" and t["handed_off_at"] is None
    store.upsert_tracking("r1", tasks=[], handed_off_at="2026-11-12T00:00:00")
    t = store.get_tracking("r1")
    assert t["tasks"] == [] and t["handoff_summary"] == "all done" and t["handed_off_at"]
    assert store.get_tracking("r2") is None
    with pytest.raises(ValueError, match="unknown tracking fields"):
        store.upsert_tracking("r1", status="x")


# ---------- observability queries (dashboard sidebar pages) ----------
def _llm(store, run_id, ts, *, model="claude-haiku-5-5", node="extract_fields", prompt="extract_v3", lat=100,
         tin=10, tout=5, cost=0.001, agent="intake"):
    store.add_llm_call(dict(run_id=run_id, ts=ts, agent=agent, node=node, model=model, prompt_version=prompt,
                            input_tokens=tin, output_tokens=tout, latency_ms=lat, cost_usd=cost, stop_reason="end_turn"))


def _tool(store, run_id, ts, *, tool="ae_lookup", status="ok", agent="intake", lat=50, args=None, result=None):
    store.add_tool_call(dict(run_id=run_id, ts=ts, agent=agent, tool=tool, args=args or {"email": "ae@x.example"},
                             result=result or {"phone": "*******3210"}, status=status, attempt=1, latency_ms=lat,
                             idempotency_key=f"k-{ts}"))


def test_page_llm_calls_is_newest_first_filtered_counted_and_named(store):
    store.upsert_run("r1", customer_name="Globex")
    for i in range(5):
        _llm(store, "r1", f"2026-11-1{i}T10:00:00+00:00", model="claude-haiku-5-5" if i % 2 else "fake",
             node="extract_fields" if i < 3 else "draft_welcome")
    _llm(store, "r2", "2026-11-15T10:00:00+00:00")  # run row missing: customer_name is None
    page = store.page_llm_calls(limit=2, offset=0)
    assert page["total"] == 6 and [r["ts"][:10] for r in page["rows"]] == ["2026-11-15", "2026-11-14"]
    assert page["rows"][0]["customer_name"] is None and page["rows"][1]["customer_name"] == "Globex"
    assert isinstance(page["rows"][0]["id"], int) and page["rows"][1]["cost_usd"] == 0.001
    assert [r["ts"][:10] for r in store.page_llm_calls(limit=2, offset=4)["rows"]] == ["2026-11-11", "2026-11-10"]
    assert store.page_llm_calls(limit=10, offset=0, model="fake")["total"] == 3
    assert store.page_llm_calls(limit=10, offset=0, node="draft_welcome", run_id="r1")["total"] == 2
    assert store.page_llm_calls(limit=10, offset=0, run_id="r2")["total"] == 1
    assert store.page_llm_calls(limit=10, offset=0, since="2026-11-13")["total"] == 3


def test_page_tool_calls_never_carries_payloads_and_detail_does(store):
    store.upsert_run("r1", customer_name="Globex")
    _tool(store, "r1", "2026-11-10T10:00:00+00:00")
    _tool(store, "r1", "2026-11-11T10:00:00+00:00", tool="slack_post_message", status="failed", agent="comms")
    _tool(store, "r2", "2026-11-12T10:00:00+00:00", result={"error": "boom"}, status="failed")
    page = store.page_tool_calls(limit=10, offset=0)
    assert page["total"] == 3 and [r["ts"][:10] for r in page["rows"]] == ["2026-11-12", "2026-11-11", "2026-11-10"]
    for r in page["rows"]:
        assert "args" not in r and "result" not in r
    assert page["rows"][1]["customer_name"] == "Globex"
    assert page["rows"][1]["idempotency_key"] == "k-2026-11-11T10:00:00+00:00"
    assert store.page_tool_calls(limit=10, offset=0, status="failed")["total"] == 2
    assert store.page_tool_calls(limit=10, offset=0, tool="ae_lookup", status="failed")["total"] == 1
    assert store.page_tool_calls(limit=10, offset=0, agent="comms")["total"] == 1
    assert store.page_tool_calls(limit=1, offset=1, run_id="r1")["rows"][0]["tool"] == "ae_lookup"
    one = store.get_tool_call(page["rows"][0]["id"])
    assert one["result"] == {"error": "boom"} and one["args"] == {"email": "ae@x.example"} and one["run_id"] == "r2"
    assert store.get_tool_call(987654) is None


def test_llm_stats_totals_groups_percentiles_and_since(store):
    for i, lat in enumerate([100, 200, 300, 400, 1000]):
        _llm(store, "r1", f"2026-11-1{i}T10:00:00+00:00", lat=lat, tin=10 * (i + 1), tout=i, cost=0.25)
    _llm(store, "r1", "2026-11-14T11:00:00+00:00", model="fake", node="draft_welcome", prompt="welcome_v1",
         lat=9000, tin=0, tout=0, cost=0.0, agent="comms")
    s = store.llm_stats()
    t = s["totals"]
    assert (t["calls"], t["input_tokens"], t["output_tokens"], t["cost_usd"]) == (6, 150, 10, 1.25)
    haiku = next(g for g in s["by_model"] if g["model"] == "claude-haiku-5-5")
    # percentile_cont semantics: p95 of [100,200,300,400,1000] = 400 + 0.8 * 600
    assert (haiku["calls"], haiku["avg_latency_ms"], haiku["p50_latency_ms"], haiku["p95_latency_ms"]) == (5, 400, 300, 880)
    assert [g["model"] for g in s["by_model"]] == ["claude-haiku-5-5", "fake"]  # most calls first
    assert {(g["agent"], g["node"]) for g in s["by_node"]} == {("intake", "extract_fields"), ("comms", "draft_welcome")}
    assert {g["prompt_version"]: g["calls"] for g in s["by_prompt"]} == {"extract_v3": 5, "welcome_v1": 1}
    assert [(d["day"], d["calls"]) for d in s["by_day"]] == [
        ("2026-11-10", 1), ("2026-11-11", 1), ("2026-11-12", 1), ("2026-11-13", 1), ("2026-11-14", 2)]
    assert s["latency_buckets"] == [4, 1, 0, 0, 1]  # <1s, 1-2s, 2-4s, 4-8s, 8s+
    since = store.llm_stats(since="2026-11-14")
    assert since["totals"]["calls"] == 2 and since["totals"]["p95_latency_ms"] == 8600


def test_llm_stats_empty(store):
    s = store.llm_stats()
    assert s["totals"]["calls"] == 0 and s["totals"]["cost_usd"] == 0 and s["totals"]["p95_latency_ms"] is None
    assert s["by_model"] == [] and s["by_day"] == [] and s["latency_buckets"] == [0, 0, 0, 0, 0]


def test_tool_stats_counts_failures_replays_and_blocks(store):
    for lat in (10, 20, 30, 40):
        _tool(store, "r1", f"2026-11-10T10:00:{lat}+00:00", lat=lat)
    _tool(store, "r1", "2026-11-10T10:01:00+00:00", status="failed", lat=200)
    _tool(store, "r1", "2026-11-10T10:02:00+00:00", tool="slack_post_message")
    for status in ("replayed", "replayed", "blocked", "ok"):
        store.add_audit(dict(run_id="r1", ts="2026-11-10T10:03:00+00:00", agent="intake", step="tool:ae_lookup",
                             input=None, output=None, rationale="x", status=status))
    store.add_audit(dict(run_id="r1", ts="2026-11-10T10:03:00+00:00", agent="comms", step="tool:gmail_reply",
                         input=None, output=None, rationale="x", status="blocked"))
    stats = {s["tool"]: s for s in store.tool_stats()}
    ae = stats["ae_lookup"]
    assert (ae["calls"], ae["ok"], ae["failed"], ae["replayed"], ae["blocked"]) == (5, 4, 1, 2, 1)
    assert ae["failure_rate"] == 0.2 and ae["avg_latency_ms"] == 60 and ae["p95_latency_ms"] == 168
    assert stats["slack_post_message"]["failure_rate"] == 0.0
    assert stats["gmail_reply"]["calls"] == 0 and stats["gmail_reply"]["blocked"] == 1
    assert [s["tool"] for s in store.tool_stats()][0] == "ae_lookup"  # most calls first
    assert store.tool_stats(since="2026-11-11") == []


def test_overview_stats(store):
    store.upsert_run("a", status="COMPLETED", customer_name="A")
    store.upsert_run("b", status="COMPLETED")
    store.upsert_run("c", status="ESCALATED")
    created = store.get_run("a")["created_at"]
    from datetime import datetime, timedelta
    base = datetime.fromisoformat(created)
    _tool(store, "a", (base + timedelta(seconds=90)).isoformat(), tool="rocketlane_create_project")
    _tool(store, "a", (base + timedelta(seconds=300)).isoformat(), tool="rocketlane_create_project")  # later: ignored
    _tool(store, "b", (base + timedelta(seconds=30)).isoformat(), tool="rocketlane_create_project", status="failed")
    for st in ("ok", "ok", "failed"):
        _tool(store, "a", created, tool="voice_start_call", status=st)
    for st in ("confirmed", "no_answer", "unclear", "unclear"):
        store.add_audit(dict(run_id="a", ts=created, agent="intake", step="decide_tier", input=None, output=None,
                             rationale="x", status=st))
    store.upsert_escalation("c:1", dict(run_id="c", reason="r", kind="tier", agent="intake", status="open"))
    store.upsert_escalation("c:0", dict(run_id="c", reason="r", kind="tier", agent="intake", status="resolved"))
    _llm(store, "a", created, cost=0.5)
    _llm(store, "b", created, cost=0.25)
    o = store.overview_stats()
    assert o["runs_total"] == 3 and o["runs_by_status"] == {"COMPLETED": 2, "ESCALATED": 1}
    assert o["runs_by_day"] == [{"day": created[:10], "runs": 3}]
    assert o["escalations_open"] == 1
    assert o["email_to_project"]["runs"] == 1 and 89 <= o["email_to_project"]["avg_seconds"] <= 91
    assert o["calls"] == {"placed": 2, "confirmed": 1, "unclear": 2, "no_answer": 1}
    assert o["llm_calls"] == 2 and o["llm_cost_usd"] == 0.75
