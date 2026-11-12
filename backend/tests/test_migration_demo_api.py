"""Migration demo data API: summary + paged rows (problems per row, computed by the checker's own logic), and the
demo fix/reset controls, which only ever touch the configured demo destination database."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.container import Container
from app.db.seed import demo_contacts
from app.main import create_app
from app.services.migration_check import MigrationDataUnavailable, Table, columns_of, load_contacts
from app.services.migration_demo import repair, replace_contacts
from app.sim import Harness

ORIGIN = {"Origin": "http://testserver"}
PLANTED = {"duplicates": [23], "missing_required": [17, 36, 44], "missing_in_dest": [12, 31, 47, 58],
           "not_in_source": []}
SRC_COLS, DST_COLS = (t.columns for t in demo_contacts())
PLANTED_COLS = {"source": SRC_COLS, "dest": DST_COLS, "missing": ["phone"], "extra": ["lifecycle_stage"]}
FIXED_COLS = {"source": SRC_COLS, "dest": DST_COLS + ["phone"], "missing": [], "extra": ["lifecycle_stage"]}
CLEAN = {"duplicates": [], "missing_required": [], "missing_in_dest": [], "not_in_source": []}


class FakeDb:
    """In-memory source/destination contacts with the loader + writer the container exposes."""

    def __init__(self):
        self.source, self.dest = demo_contacts()
        self.writes = 0

    def load(self):
        return (Table([dict(r) for r in self.source], self.source.columns),
                Table([dict(r) for r in self.dest], columns_of(self.dest)))

    def write(self, rows):
        self.writes += 1
        self.dest = Table([dict(r) for r in rows], columns_of(rows))


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
    assert r.json() == {"source_count": 60, "dest_count": 57, "problems": PLANTED, "columns": PLANTED_COLS,
                        "last_check": None}


def test_summary_last_check_is_the_newest_batch(api):
    c, store, _ = api
    for name, ok in (("record_count", True), ("duplicates", False)):
        store.add_migration_check(dict(run_id="r9", batch_id="b9", ts="2026-11-12T10:00:00+00:00",
                                       check_name=name, passed=ok, details={}))
    assert c.get("/migration/summary").json()["last_check"] == {
        "batch_id": "b9", "ts": "2026-11-12T10:00:00+00:00", "passed": False, "run_id": "r9"}


def test_missing_in_dest_and_missing_name_are_reported(settings):
    db = FakeDb()
    db.dest = Table([r for r in db.dest if r["id"] != 5], db.dest.columns)
    db.dest[0] = {**db.dest[0], "name": "  "}
    c, _, _ = make(settings, db)
    p = c.get("/migration/summary").json()["problems"]
    assert p == {"duplicates": [23], "missing_required": [1, 17, 36, 44], "missing_in_dest": [5, 12, 31, 47, 58],
                 "not_in_source": []}
    src = c.get("/migration/data", params={"side": "source", "only_problems": True}).json()
    assert src["total"] == 5
    assert src["rows"][0] == {**db.source[4], "created_on": db.source[4]["created_on"].isoformat(),
                              "problems": ["missing_in_dest"]}
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
    assert r["total"] == 57 and len(r["rows"]) == 10
    assert set(r["rows"][0]) == {*DST_COLS, "problems"} and r["rows"][0]["problems"] == []
    page = c.get("/migration/data", params={"side": "dest", "limit": 5, "offset": 14}).json()["rows"]
    assert [x["id"] for x in page] == [16, 17, 18, 19, 20]
    assert (page[1]["id"], page[1]["email"], page[1]["problems"]) == (17, None, ["missing_email"])


def test_only_problems_filters_rows_and_counts(api):
    c, _, _ = api
    r = c.get("/migration/data", params={"side": "dest", "only_problems": "true"}).json()
    assert r["total"] == 5
    assert [(x["id"], x["problems"]) for x in r["rows"]] == [(17, ["missing_email"]), (23, ["duplicate"]),
                                                             (23, ["duplicate"]), (36, ["missing_name"]),
                                                             (44, ["missing_email"])]
    src = c.get("/migration/data", params={"side": "source", "only_problems": "true"}).json()
    assert [x["id"] for x in src["rows"]] == [12, 31, 47, 58]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": 10**9 + 1},
                                    {"side": "both"}, {"limit": "ten"}])
def test_out_of_bounds_params_are_422(api, params):
    c, _, _ = api
    assert c.get("/migration/data", params={"side": "dest", **params}).status_code == 422


def test_offset_past_the_end_is_an_empty_page(api):
    c, _, _ = api
    assert c.get("/migration/data", params={"side": "dest", "offset": 10**9}).json() == {"total": 57, "rows": []}


# ---------------- fix / reset ----------------

def test_fix_repairs_the_destination_and_is_idempotent(api):
    c, store, db = api
    r = c.post("/migration/demo/fix", headers=ORIGIN)
    assert r.status_code == 200
    assert r.json() == {"source_count": 60, "dest_count": 60, "problems": CLEAN, "columns": FIXED_COLS,
                        "last_check": None}
    by_id = {x["id"]: x for x in db.dest}
    assert all(by_id[s["id"]][k] == v for s in db.source for k, v in s.items())  # phone column + rows from source
    assert by_id[17]["email"] == db.source[16]["email"] and by_id[12]["lifecycle_stage"] is None
    assert c.post("/migration/demo/fix", headers=ORIGIN).json()["problems"] == CLEAN
    assert db.writes == 1                                             # second click: nothing to write
    steps = [(a["step"], a["output"]["changed"]) for a in store.list_audit("migration_demo")]
    assert steps == [("demo_fix", True), ("demo_fix", False)]


def test_reset_restores_the_planted_defects_and_is_idempotent(api):
    c, store, db = api
    c.post("/migration/demo/fix", headers=ORIGIN)
    r = c.post("/migration/demo/reset", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["problems"] == PLANTED and r.json()["dest_count"] == 57
    assert r.json()["columns"] == PLANTED_COLS
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


def test_repair_adds_missing_columns_from_the_source_and_keeps_extra_ones():
    src = Table([{"id": 1, "name": "A", "email": "a@x", "phone": "+1"}], ["id", "name", "email", "phone"])
    dst = Table([{"id": 1, "name": "A", "email": "a@x", "stage": "lead"}], ["id", "name", "email", "stage"])
    fixed = repair(src, dst)
    assert fixed.columns == ["id", "name", "email", "stage", "phone"]
    assert fixed == [{"id": 1, "name": "A", "email": "a@x", "stage": "lead", "phone": "+1"}]


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
    fixed = c.post("/migration/demo/fix", headers=ORIGIN).json()
    assert fixed["problems"] == CLEAN and fixed["columns"]["missing"] == []
    dest = load_contacts(dst)
    assert len(dest) == 60 and "phone" in dest.columns
    assert c.post("/migration/demo/reset", headers=ORIGIN).json()["columns"] == PLANTED_COLS
    key = lambda r: (r["id"], r["email"] or "")  # noqa: E731
    assert sorted(load_contacts(dst), key=key) == sorted(demo_contacts()[1], key=key)


def test_replace_contacts_is_atomic(pg_demo):
    """A failing insert (bad row) rolls back the delete: the destination is never left half-written."""
    _, dst = pg_demo
    before = load_contacts(dst)
    with pytest.raises(Exception):  # noqa: B017 - any DB error
        replace_contacts(dst, [{"id": 1, "name": "A", "email": "a@x", "x": 1},
                               {"id": "not-an-int", "name": "B", "email": "b@x", "x": 2}])
    after = load_contacts(dst)
    assert after == before and after.columns == before.columns  # column changes rolled back too
