"""Migration validation (plan §12): four checks, evidence on the "Migration verified" task, CSM escalation on
failure, and the task is never closed by the agent."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from test_workflow import ENTERPRISE_CALL, run

from app.config import Settings
from app.container import Container
from app.db.seed import demo_contacts
from app.graph.root import validate_resolution
from app.main import create_app
from app.providers.real.rocketlane import RocketlaneClient
from app.services.migration_check import evaluate, load_contacts, run_migration_check
from app.tools.registry import PermanentToolError, ToolNotAllowed

BASE = "https://api.rocketlane.com/api/1.0"


def rows(n: int) -> list[dict]:
    return [{"id": i, "name": f"Person {i}", "email": f"p{i}@acme.example"} for i in range(1, n + 1)]


def by_name(results):
    return {r.name: r for r in results}


# ---------------- the four checks (pure) ----------------

def test_clean_copy_passes_all_four_checks():
    results = evaluate(rows(10), rows(10))
    assert [r.name for r in results] == ["record_count", "primary_keys", "duplicates", "required_fields"]
    assert all(r.passed for r in results)
    assert by_name(results)["record_count"].details == {"expected": 10, "actual": 10, "sample_ids": []}


def test_seeded_demo_data_catches_both_planted_defects():
    source, dest = demo_contacts()
    assert len(source) == 50
    r = by_name(evaluate(source, dest))
    assert not r["duplicates"].passed and r["duplicates"].details["sample_ids"] == [23]
    assert not r["required_fields"].passed and r["required_fields"].details["sample_ids"] == [17]
    # The duplicate row also inflates the destination count; no source row is missing.
    assert not r["record_count"].passed and r["record_count"].details["actual"] == 51
    assert r["primary_keys"].passed


def test_missing_source_row_is_caught_even_when_counts_match():
    dest = rows(10)
    dest[4] = {**dest[3]}  # id 5 lost, id 4 duplicated: count still 10
    r = by_name(evaluate(rows(10), dest))
    assert r["record_count"].passed
    assert not r["primary_keys"].passed and r["primary_keys"].details == {"expected": 0, "actual": 1, "sample_ids": [5]}
    assert not r["duplicates"].passed


@pytest.mark.parametrize("bad", [{"name": "  "}, {"email": None}, {"email": ""}, {"name": None}])
def test_blank_or_whitespace_required_field_fails(bad):
    dest = rows(3)
    dest[1] = {**dest[1], **bad}
    r = by_name(evaluate(rows(3), dest))
    assert not r["required_fields"].passed and r["required_fields"].details["sample_ids"] == [2]


def test_sample_ids_are_capped():
    dest = [{**d, "email": ""} for d in rows(40)]
    d = by_name(evaluate(rows(40), dest))["required_fields"].details
    assert d["actual"] == 40 and len(d["sample_ids"]) == 10


def test_load_contacts_reads_a_real_sql_table(tmp_path):
    url = f"sqlite:///{tmp_path / 'c.db'}"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE contacts (id INTEGER, name TEXT, email TEXT)"))
        c.execute(text("INSERT INTO contacts VALUES (2, 'B', 'b@x'), (1, 'A', NULL)"))
    assert load_contacts(url) == [{"id": 1, "name": "A", "email": None}, {"id": 2, "name": "B", "email": "b@x"}]


# ---------------- end to end through the registry (fake Rocketlane) ----------------

@pytest.fixture
def completed(settings):
    h, rid = run(settings, calls=[ENTERPRISE_CALL])
    assert h.status(rid) == "COMPLETED"
    return h, rid


def check(h, rid, source, dest):
    return run_migration_check(h.store, h.deps.registry, lambda: (source, dest), rid)


def the_task(h):
    [project] = h.world.rocketlane.projects
    [task] = [t for t in project["tasks"] if t["taskName"].startswith("Migration verified")]
    return task


def migration_escalations(h, rid, status=None):
    return [e for e in h.store.list_escalations(status) if e["run_id"] == rid and e["kind"] == "migration"]


def test_all_pass_writes_evidence_note_and_leaves_task_open(completed):
    h, rid = completed
    out = check(h, rid, rows(5), rows(5))
    assert out["passed"] is True and out["task_note"] == "written" and out["escalation"] is None
    task = the_task(h)
    assert "PASSED" in task["taskPrivateNote"] and "record_count" in task["taskPrivateNote"]
    assert task["status"] == {"value": 1, "label": "To do"}  # untouched: a person closes it
    assert h.world.rocketlane.task_updates and all(set(u) == {"taskPrivateNote"}
                                                   for u in h.world.rocketlane.task_updates)
    stored = h.store.list_migration_checks(rid)
    assert [r["check_name"] for r in stored] == ["record_count", "primary_keys", "duplicates", "required_fields"]
    assert all(r["passed"] for r in stored) and len({r["batch_id"] for r in stored}) == 1
    assert migration_escalations(h, rid) == []


def test_failure_escalates_to_csm_writes_evidence_and_leaves_task_open(completed):
    h, rid = completed
    source, dest = demo_contacts()
    out = check(h, rid, source, dest)
    assert out["passed"] is False
    [esc] = migration_escalations(h, rid, "open")
    assert esc["agent"] == "migration" and "duplicates" in esc["reason"] and "required_fields" in esc["reason"]
    task = the_task(h)
    assert "FAILED" in task["taskPrivateNote"] and "17" in task["taskPrivateNote"]
    assert task["status"] == {"value": 1, "label": "To do"}
    assert h.status(rid) == "COMPLETED"  # onboarding run itself is not re-opened
    assert [r["passed"] for r in h.store.list_migration_checks(rid)] == [False, True, False, False]


def test_repeat_failures_keep_one_open_escalation_and_a_pass_resolves_it(completed):
    h, rid = completed
    source, dest = demo_contacts()
    check(h, rid, source, dest)
    check(h, rid, source, dest)
    assert len(migration_escalations(h, rid, "open")) == 1
    check(h, rid, rows(5), rows(5))
    assert migration_escalations(h, rid, "open") == []
    [esc] = migration_escalations(h, rid, "resolved")
    assert esc["resolved_by"] == "migration_check" and esc["resolution"]["action"] == "retry"
    assert the_task(h)["taskPrivateNote"].count("Migration check") == 3  # evidence appended, never overwritten


def test_passing_checks_without_the_task_still_escalate(completed):
    h, rid = completed
    [project] = h.world.rocketlane.projects
    project["tasks"] = []
    out = check(h, rid, rows(5), rows(5))
    assert out["passed"] is True and out["task_note"].startswith("not written")
    [esc] = migration_escalations(h, rid, "open")
    assert "Migration verified" in esc["reason"]


def test_set_tier_is_not_a_valid_resolution_for_a_migration_escalation():
    with pytest.raises(ValueError, match="not allowed"):
        validate_resolution({"action": "set_tier", "tier": "enterprise", "by": "priya"},
                            {"escalation_kind": "migration"})


def test_migration_agent_is_limited_to_task_tools(completed):
    h, rid = completed
    reg = h.deps.registry
    with pytest.raises(ToolNotAllowed):
        reg.call("migration", "rocketlane_create_project", {}, run_id=rid)
    for agent in ("intake", "comms"):
        with pytest.raises(ToolNotAllowed):
            reg.call(agent, "rocketlane_add_task_note", {"task_id": "1", "note_html": "x", "marker": "m"},
                     run_id=rid)


# ---------------- API ----------------

def api(h, contacts):
    c = Container(settings=h.settings, store=h.store, runner=h.runner, start_poller=False,
                  registry=h.deps.registry, migration_contacts=contacts)
    return TestClient(create_app(c))


def test_endpoint_404s_without_run_or_project(settings):
    h, rid = run(settings, calls=[])  # no answer x3 -> escalated, no project
    client = api(h, lambda: (rows(1), rows(1)))
    assert client.post("/runs/nope/migration-check").status_code == 404
    r = client.post(f"/runs/{rid}/migration-check")
    assert r.status_code == 404 and "project" in r.json()["detail"]


def test_endpoint_runs_checks_and_shows_them_on_run_detail(completed):
    h, rid = completed
    client = api(h, demo_contacts)
    r = client.post(f"/runs/{rid}/migration-check")
    assert r.status_code == 200 and r.json()["passed"] is False
    assert len(client.get(f"/runs/{rid}").json()["migration_checks"]) == 4


def test_endpoint_reports_unreachable_migration_databases(completed):
    h, rid = completed

    def down():
        from app.services.migration_check import MigrationDataUnavailable
        raise MigrationDataUnavailable("connection refused")

    r = api(h, down).post(f"/runs/{rid}/migration-check")
    assert r.status_code == 503 and h.store.list_migration_checks(rid) == []


def test_csm_can_dismiss_a_migration_escalation_with_a_reason(completed):
    """Only a passing re-run closed these before: a CSM who accepts a known gap (e.g. one test row) had no way
    to close it. Dismissal needs a reason, is audited, and leaves the Rocketlane task alone."""
    h, rid = completed
    client = api(h, demo_contacts)
    client.post(f"/runs/{rid}/migration-check")
    [esc] = migration_escalations(h, rid, "open")
    notes_before = len(h.world.rocketlane.task_updates)
    r = client.post(f"/runs/{rid}/migration-escalation/dismiss", json={"reason": "customer confirmed row 17 is junk",
                                                                      "by": "csm@novacrm.example"})
    assert r.status_code == 200
    assert migration_escalations(h, rid, "open") == []
    [closed] = migration_escalations(h, rid)
    assert closed["status"] == "dismissed" and closed["resolved_by"] == "csm@novacrm.example"
    assert closed["resolution"] == {"action": "dismiss", "note": "customer confirmed row 17 is junk"}
    assert any(a["step"] == "dismiss_migration_escalation" and "row 17" in a["rationale"]
               for a in h.store.audit_rows if a["run_id"] == rid)
    assert len(h.world.rocketlane.task_updates) == notes_before  # never touches the task


@pytest.mark.parametrize("body", [{}, {"reason": "  ", "by": "csm@x"}, {"reason": "ok"}])
def test_dismiss_requires_reason_and_who(completed, body):
    h, rid = completed
    client = api(h, demo_contacts)
    client.post(f"/runs/{rid}/migration-check")
    assert client.post(f"/runs/{rid}/migration-escalation/dismiss", json=body).status_code == 422
    assert len(migration_escalations(h, rid, "open")) == 1


def test_dismiss_without_open_migration_escalation_is_404(completed):
    h, rid = completed
    r = api(h, demo_contacts).post(f"/runs/{rid}/migration-escalation/dismiss",
                                   json={"reason": "x", "by": "csm@x"})
    assert r.status_code == 404

# ---------------- settings ----------------

def test_migration_urls_default_to_the_app_database_server():
    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:pw@db:5432/app")
    assert s.mig_source_url == "postgresql+psycopg://u:pw@db:5432/mig_source"
    assert s.mig_dest_url == "postgresql+psycopg://u:pw@db:5432/mig_dest"
    s2 = Settings(_env_file=None, mig_source_url="postgresql://u:pw@x/src")
    assert s2.mig_source_url == "postgresql+psycopg://u:pw@x/src"  # psycopg2 isn't installed


# ---------------- real Rocketlane client (respx) ----------------

@pytest.fixture
def rl():
    return RocketlaneClient(BASE, "test-key", sleep=lambda s: None)


def page(items):
    return {"data": items, "pagination": {"hasMore": False}}


@respx.mock
def test_find_task_filters_by_project_and_name(rl):
    route = respx.get(f"{BASE}/tasks").mock(return_value=httpx.Response(200, json=page([
        {"taskId": 9, "taskName": "Migration verified (validation checks passed)"},
        {"taskId": 8, "taskName": "Pre-check: Migration verified?"}])))
    assert rl.find_task("42", "Migration verified") == "9"
    p = route.calls[0].request.url.params
    assert p["projectId.eq"] == "42" and p["taskName.cn"] == "Migration verified"


@respx.mock
def test_find_task_refuses_to_guess_between_two_matches(rl):
    respx.get(f"{BASE}/tasks").mock(return_value=httpx.Response(200, json=page([
        {"taskId": 1, "taskName": "Migration verified"}, {"taskId": 2, "taskName": "migration verified"}])))
    with pytest.raises(PermanentToolError, match="2 tasks"):
        rl.find_task("42", "Migration verified")


@respx.mock
def test_append_note_keeps_existing_note_and_never_sends_status(rl):
    get = respx.get(f"{BASE}/tasks/9").mock(return_value=httpx.Response(
        200, json={"taskId": 9, "taskPrivateNote": "<p>human note</p>"}))
    put = respx.put(f"{BASE}/tasks/9").mock(return_value=httpx.Response(200, json={"taskId": 9}))
    rl.append_task_note("9", "<p>evidence b1</p>", marker="b1")
    assert get.calls[0].request.url.params["includeFields"] == "taskPrivateNote"
    body = httpx.Response(200, content=put.calls[0].request.content).json()
    assert body == {"taskPrivateNote": "<p>human note</p><p>evidence b1</p>"}


@respx.mock
def test_append_note_is_a_noop_when_the_batch_is_already_there(rl):
    respx.get(f"{BASE}/tasks/9").mock(return_value=httpx.Response(
        200, json={"taskId": 9, "taskPrivateNote": "<p>evidence b1</p>"}))
    put = respx.put(f"{BASE}/tasks/9")
    rl.append_task_note("9", "<p>evidence b1</p>", marker="b1")
    assert not put.called  # a retried (ambiguous) PUT never doubles the note


# ---------------- seed against local Postgres (skips when unreachable) ----------------

def test_seed_creates_databases_and_is_idempotent(socket_enabled):
    from app.db.seed import seed_migration_demo
    base = "postgresql+psycopg://postgres:postgres@localhost:5432"
    try:
        admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            for db in ("mig_test_src", "mig_test_dst"):
                c.exec_driver_sql(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {e}")
    src, dst = f"{base}/mig_test_src", f"{base}/mig_test_dst"
    seed_migration_demo(src, dst)
    seed_migration_demo(src, dst)  # second run: no new databases, no extra rows
    source, dest = load_contacts(src), load_contacts(dst)
    assert (len(source), len(dest)) == (50, 51)
    r = by_name(evaluate(source, dest))
    assert r["duplicates"].details["sample_ids"] == [23] and r["required_fields"].details["sample_ids"] == [17]


def test_identical_recheck_within_the_window_returns_the_first_batch(completed):
    """Double-click guard (API passes reuse_within=60): same data, seconds apart -> one batch, one note."""
    h, rid = completed
    t = [1000.0]
    first = run_migration_check(h.store, h.deps.registry, lambda: (rows(5), rows(5)), rid, reuse_within=60,
                                clock=lambda: t[0])
    t[0] += 5
    again = run_migration_check(h.store, h.deps.registry, lambda: (rows(5), rows(5)), rid, reuse_within=60,
                                clock=lambda: t[0])
    assert again == first and the_task(h)["taskPrivateNote"].count("Migration check") == 1
    changed = run_migration_check(h.store, h.deps.registry, lambda: (rows(5), rows(4)), rid, reuse_within=60,
                                  clock=lambda: t[0])                       # data changed: a real new check
    assert changed["batch_id"] != first["batch_id"]
    t[0] += 120
    later = run_migration_check(h.store, h.deps.registry, lambda: (rows(5), rows(4)), rid, reuse_within=60,
                                clock=lambda: t[0])                         # window passed: runs again
    assert later["batch_id"] != changed["batch_id"]
    assert the_task(h)["taskPrivateNote"].count("Migration check") == 3
