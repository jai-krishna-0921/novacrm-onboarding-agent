"""MemoryStore and PgStore must behave identically (PgStore runs against local Docker Postgres, else skips)."""

import pytest

from app.services.store import MemoryStore

PG_ADMIN = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
PG_TEST = "postgresql+psycopg://postgres:postgres@localhost:5432/app_test"


@pytest.fixture(params=["memory", "pg"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    request.getfixturevalue("socket_enabled")  # pytest-socket: this DB test may use localhost
    sqlalchemy = pytest.importorskip("sqlalchemy")
    try:
        admin = sqlalchemy.create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.exec_driver_sql("DROP DATABASE IF EXISTS app_test WITH (FORCE)")
            c.exec_driver_sql("CREATE DATABASE app_test")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {e}")
    from app.db.store import PgStore
    s = PgStore(PG_TEST)
    s.create_all()
    yield s
    s.engine.dispose()


def test_idempotency_roundtrip(store):
    assert store.get_idem("k") is None
    store.put_idem("k", {"project_id": "42"})
    assert store.get_idem("k") == {"project_id": "42"}


def test_runs_upsert_and_lookup(store):
    store.upsert_run("r1", status="RECEIVED", gmail_message_id="gm-1")
    store.upsert_run("r1", status="AWAITING_CALL")
    run = store.get_run("r1")
    assert run["status"] == "AWAITING_CALL" and run["gmail_message_id"] == "gm-1" and run["created_at"]
    assert store.find_run_by_message("gm-1")["id"] == "r1"
    assert store.find_run_by_message("nope") is None


def test_find_active_run_needs_a_project_and_skips_failed_and_self(store):
    store.upsert_run("a", status="PROJECT_CREATED", opportunity_id="006A", customer_key="globex",
                     rocketlane_project_id="7")
    store.upsert_run("b", status="FAILED", opportunity_id="006B", customer_key="initech", rocketlane_project_id="8")
    store.upsert_run("c", status="AWAITING_CALL", opportunity_id="006C", customer_key="umbrella")
    assert store.find_active_run(opportunity_id="006A", customer_key="x", exclude_run_id="z")["id"] == "a"
    assert store.find_active_run(opportunity_id=None, customer_key="globex", exclude_run_id="z")["id"] == "a"
    assert store.find_active_run(opportunity_id="006A", customer_key="globex", exclude_run_id="a") is None
    assert store.find_active_run(opportunity_id="006B", customer_key="initech", exclude_run_id="z") is None
    assert store.find_active_run(opportunity_id="006C", customer_key="umbrella", exclude_run_id="z") is None


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


def test_list_runs_newest_first(store):
    store.upsert_run("r1", status="COMPLETED")
    store.upsert_run("r2", status="RECEIVED")
    assert [r["id"] for r in store.list_runs()][:2] == ["r2", "r1"]
