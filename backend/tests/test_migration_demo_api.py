"""Migration demo data API: summary + paged rows (problems per row, computed by the checker's own logic), and the
demo fix/reset controls, which only ever touch the configured demo destination database."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.container import Container
from app.db.seed import demo_contacts
from app.main import create_app
from app.services.migration_check import MigrationDataUnavailable, load_contacts
from app.services.migration_demo import repair, replace_contacts
from app.sim import Harness

ORIGIN = {"Origin": "http://testserver"}
PLANTED = {"duplicates": [23], "missing_required": [17], "missing_in_dest": []}
CLEAN = {"duplicates": [], "missing_required": [], "missing_in_dest": []}


class FakeDb:
    """In-memory source/destination contacts with the loader + writer the container exposes."""

    def __init__(self):
        self.source, self.dest = demo_contacts()
        self.writes = 0

    def load(self):
        return [dict(r) for r in self.source], [dict(r) for r in self.dest]

    def write(self, rows):
        self.writes += 1
        self.dest = [dict(r) for r in rows]


def make(settings, db=None, **overrides):
    s = settings.model_copy(update=overrides)
    h = Harness(s)
    db = db or FakeDb()
    c = TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False,
                                        migration_contacts=db.load, migration_write=db.write)),
                   follow_redirects=False)
    return c, h.store, db


@pytest.fixture
def api(settings):
    return make(settings)


# ---------------- summary ----------------

def test_summary_reports_the_planted_defects(api):
    c, store, _ = api
    r = c.get("/migration/summary")
    assert r.status_code == 200
    assert r.json() == {"source_count": 50, "dest_count": 51, "problems": PLANTED, "last_check": None}


def test_summary_last_check_is_the_newest_batch(api):
    c, store, _ = api
    for name, ok in (("record_count", True), ("duplicates", False)):
        store.add_migration_check(dict(run_id="r9", batch_id="b9", ts="2026-11-12T10:00:00+00:00",
                                       check_name=name, passed=ok, details={}))
    assert c.get("/migration/summary").json()["last_check"] == {
        "batch_id": "b9", "ts": "2026-11-12T10:00:00+00:00", "passed": False, "run_id": "r9"}


def test_missing_in_dest_and_missing_name_are_reported(settings):
    db = FakeDb()
    db.dest = [r for r in db.dest if r["id"] != 5]
    db.dest[0] = {**db.dest[0], "name": "  "}
    c, _, _ = make(settings, db)
    p = c.get("/migration/summary").json()["problems"]
    assert p == {"duplicates": [23], "missing_required": [1, 17], "missing_in_dest": [5]}
    src = c.get("/migration/data", params={"side": "source", "only_problems": True}).json()
    assert src == {"total": 1, "rows": [{**db.source[4], "problems": ["missing_in_dest"]}]}
    [first] = c.get("/migration/data", params={"side": "dest", "limit": 1}).json()["rows"]
    assert first["id"] == 1 and first["problems"] == ["missing_name"]


def test_unreachable_databases_are_503(settings):
    def down():
        raise MigrationDataUnavailable("OperationalError: connection refused")
    s = settings
    h = Harness(s)
    c = TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False,
                                        migration_contacts=down)))
    for path in ("/migration/summary", "/migration/data"):
        assert c.get(path).status_code == 503
    assert c.post("/migration/demo/fix", headers=ORIGIN).status_code == 503


# ---------------- rows ----------------

def test_dest_rows_are_paged_with_per_row_problems(api):
    c, _, _ = api
    r = c.get("/migration/data", params={"side": "dest"}).json()
    assert r["total"] == 51 and len(r["rows"]) == 10
    assert set(r["rows"][0]) == {"id", "name", "email", "problems"} and r["rows"][0]["problems"] == []
    page = c.get("/migration/data", params={"side": "dest", "limit": 5, "offset": 15}).json()["rows"]
    assert [x["id"] for x in page] == [16, 17, 18, 19, 20]
    assert (page[1]["id"], page[1]["email"], page[1]["problems"]) == (17, None, ["missing_email"])


def test_only_problems_filters_rows_and_counts(api):
    c, _, _ = api
    r = c.get("/migration/data", params={"side": "dest", "only_problems": "true"}).json()
    assert r["total"] == 3
    assert [(x["id"], x["problems"]) for x in r["rows"]] == [(17, ["missing_email"]), (23, ["duplicate"]),
                                                             (23, ["duplicate"])]
    assert c.get("/migration/data", params={"side": "source", "only_problems": "true"}).json() == {
        "total": 0, "rows": []}


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": 10**9 + 1},
                                    {"side": "both"}, {"limit": "ten"}])
def test_out_of_bounds_params_are_422(api, params):
    c, _, _ = api
    assert c.get("/migration/data", params={"side": "dest", **params}).status_code == 422


def test_offset_past_the_end_is_an_empty_page(api):
    c, _, _ = api
    assert c.get("/migration/data", params={"side": "dest", "offset": 10**9}).json() == {"total": 51, "rows": []}


# ---------------- fix / reset ----------------

def test_fix_repairs_the_destination_and_is_idempotent(api):
    c, store, db = api
    r = c.post("/migration/demo/fix", headers=ORIGIN)
    assert r.status_code == 200
    assert r.json() == {"source_count": 50, "dest_count": 50, "problems": CLEAN, "last_check": None}
    assert sorted(db.dest, key=lambda x: x["id"]) == db.source       # email 17 filled from the source row
    assert c.post("/migration/demo/fix", headers=ORIGIN).json()["problems"] == CLEAN
    assert db.writes == 1                                             # second click: nothing to write
    steps = [(a["step"], a["output"]["changed"]) for a in store.list_audit("migration_demo")]
    assert steps == [("demo_fix", True), ("demo_fix", False)]


def test_reset_restores_the_planted_defects_and_is_idempotent(api):
    c, store, db = api
    c.post("/migration/demo/fix", headers=ORIGIN)
    r = c.post("/migration/demo/reset", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["problems"] == PLANTED and r.json()["dest_count"] == 51
    assert c.post("/migration/demo/reset", headers=ORIGIN).json()["problems"] == PLANTED
    assert db.writes == 2
    rows = store.list_audit("migration_demo")
    assert [a["step"] for a in rows] == ["demo_fix", "demo_reset", "demo_reset"]
    assert all(a["input"]["by"] for a in rows) and rows[1]["output"]["after"] == PLANTED


@pytest.mark.parametrize("overrides", [
    {"migration_demo_controls": False},
    {"mig_dest_url": "postgresql+psycopg://u:pw@db:5432/customer_contacts"},
])
@pytest.mark.parametrize("path", ["/migration/demo/fix", "/migration/demo/reset"])
def test_demo_controls_refuse_outside_the_demo_destination(settings, overrides, path):
    c, store, db = make(settings, **overrides)
    before = list(db.dest)
    r = c.post(path, headers=ORIGIN)
    assert r.status_code == 409 and isinstance(r.json()["detail"], str)
    assert db.dest == before and db.writes == 0 and store.list_audit("migration_demo") == []
    assert c.get("/migration/summary").status_code == 200        # reading stays allowed


# ---------------- pure repair ----------------

def test_repair_dedupes_fills_blanks_from_source_and_adds_missing_rows():
    src = [{"id": i, "name": f"N{i}", "email": f"e{i}@x"} for i in (1, 2, 3)]
    dst = [{"id": 1, "name": "Kept", "email": " "}, {"id": 1, "name": "Kept", "email": " "},
           {"id": 2, "name": None, "email": "dest-wins@x"}]
    assert repair(src, dst) == [{"id": 1, "name": "Kept", "email": "e1@x"},
                                {"id": 2, "name": "N2", "email": "dest-wins@x"},
                                {"id": 3, "name": "N3", "email": "e3@x"}]
    assert repair(src, repair(src, dst)) == repair(src, dst)


# ---------------- login + same-origin ----------------

@pytest.mark.parametrize("method,path", [("get", "/migration/summary"), ("get", "/migration/data"),
                                         ("post", "/migration/demo/fix"), ("post", "/migration/demo/reset")])
def test_migration_endpoints_require_login(settings, method, path):
    c, _, db = make(settings, auth_disabled=False, admin_username="admin", admin_password="pw", session_secret="s")
    r = getattr(c, method)(path, headers=ORIGIN)
    assert r.status_code == 401 and "rows" not in r.text and db.writes == 0


@pytest.mark.parametrize("path", ["/migration/demo/fix", "/migration/demo/reset"])
def test_demo_controls_need_same_origin(settings, path):
    c, _, db = make(settings, auth_disabled=False, admin_username="admin", admin_password="pw", session_secret="s")
    c.post("/auth/login", json={"username": "admin", "password": "pw"}, headers=ORIGIN)
    assert c.post(path, headers={"Origin": "https://evil.example"}).status_code == 403 and db.writes == 0
    r = c.post(path, headers=ORIGIN)
    assert r.status_code == 200
    assert {a["input"]["by"] for a in c.app.state.container.store.list_audit("migration_demo")} == {"admin"}


# ---------------- SQL path against local Postgres (skips when unreachable) ----------------

@pytest.fixture
def pg_demo(socket_enabled):
    from conftest import worker_db

    from app.db.seed import seed_migration_demo
    base = "postgresql+psycopg://postgres:postgres@localhost:5432"
    names = (worker_db("mig_demo_src"), worker_db("mig_demo_dst"))
    try:
        admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            for name in names:
                conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.dispose()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {e}")
    src, dst = (f"{base}/{n}" for n in names)
    seed_migration_demo(src, dst)
    return src, dst


def test_fix_and_reset_round_trip_through_postgres(settings, pg_demo):
    """The endpoint's guard reads the configured MIG_DEST_URL (default: database mig_dest); the loader/writer here
    point at throwaway per-worker databases so the test never touches the real demo data."""
    src, dst = pg_demo
    h = Harness(settings)
    c = TestClient(create_app(Container(
        settings=settings, store=h.store, runner=h.runner, start_poller=False,
        migration_contacts=lambda: (load_contacts(src), load_contacts(dst)),
        migration_write=lambda rows: replace_contacts(dst, rows))))
    assert c.get("/migration/summary").json()["problems"] == PLANTED
    assert c.post("/migration/demo/fix", headers=ORIGIN).json()["problems"] == CLEAN
    assert load_contacts(dst) == load_contacts(src)
    assert c.post("/migration/demo/reset", headers=ORIGIN).json()["problems"] == PLANTED
    assert sorted(load_contacts(dst), key=lambda r: (r["id"], r["email"] or "")) == \
        sorted(demo_contacts()[1], key=lambda r: (r["id"], r["email"] or ""))


def test_replace_contacts_is_atomic(pg_demo):
    """A failing insert (bad row) rolls back the delete: the destination is never left half-written."""
    _, dst = pg_demo
    before = load_contacts(dst)
    with pytest.raises(Exception):  # noqa: B017 - any DB error
        replace_contacts(dst, [{"id": 1, "name": "A", "email": "a@x"}, {"id": "not-an-int", "name": "B",
                                                                         "email": "b@x"}])
    assert load_contacts(dst) == before
