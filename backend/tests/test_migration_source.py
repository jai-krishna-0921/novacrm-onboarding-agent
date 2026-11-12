"""Per-customer migration source: a customer's own database entered in the dashboard. Tested before it is saved,
stored encrypted, the password never returned; the run's checks then read that database."""

import json

import pytest
from sqlalchemy import create_engine
from test_tracking import api
from test_workflow import GROWTH_CALL, run

from app.services import migration_source

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def env(settings):
    s = settings.model_copy(update={"session_secret": "test-secret"})
    h, rid = run(s, calls=[GROWTH_CALL])
    return api(h, settings=s), h, rid


def body(**kw):
    return {"host": "db.mavis.example", "port": 5432, "database": "mavis_crm", "username": "mavis_ro",
            "password": "s3cret-pw", **kw}


def test_unreachable_database_is_refused_and_nothing_is_saved(env):
    c, h, rid = env
    r = c.put(f"/runs/{rid}/migration-source", json=body(host="127.0.0.1", port=1), headers=ORIGIN)
    assert r.status_code == 400 and "s3cret-pw" not in r.text
    assert c.get(f"/runs/{rid}/migration-source").json()["configured"] is False


@pytest.mark.parametrize("bad", [{"host": "a b"}, {"host": "x;rm"}, {"database": "x/y"}, {"port": 0},
                                 {"username": "u@h"}])
def test_malformed_fields_are_422(env, bad):
    c, _, rid = env
    assert c.put(f"/runs/{rid}/migration-source", json=body(**bad), headers=ORIGIN).status_code == 422


def test_saved_source_is_encrypted_and_never_returned(env, monkeypatch):
    c, h, rid = env
    monkeypatch.setattr("app.main.load_contacts", lambda url: [{"id": 1, "name": "A", "email": "a@x"}])
    r = c.put(f"/runs/{rid}/migration-source", json=body(), headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert r.json()["display"] == "postgresql://mavis_ro@db.mavis.example:5432/mavis_crm" and r.json()["rows"] == 1
    raw = h.store.get_kv(f"migration_source:{rid}")
    assert "s3cret-pw" not in raw and "s3cret-pw" not in c.get(f"/runs/{rid}/migration-source").text
    assert "s3cret-pw" in migration_source.url_for(h.store, "test-secret", rid)
    assert migration_source.url_for(h.store, "other-secret", rid) is None       # wrong key: unreadable
    assert not any("s3cret-pw" in json.dumps(a) for a in h.store.list_audit(rid))
    assert c.delete(f"/runs/{rid}/migration-source", headers=ORIGIN).json()["configured"] is False


def test_run_checks_read_the_customers_own_database(settings, socket_enabled):
    """End to end against local Postgres: a customer database whose contacts match the destination -> all pass."""
    from conftest import worker_db

    from app.db.seed import demo_contacts
    from app.services.migration_demo import repair, replace_contacts
    base = "postgresql+psycopg://postgres:postgres@localhost:5432"
    name = worker_db("mig_customer")
    try:
        admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as cx:
            cx.exec_driver_sql(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            cx.exec_driver_sql(f"CREATE DATABASE {name}")
        admin.dispose()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {e}")
    clean = repair(*demo_contacts())               # a valid migration: the customer's data all landed in NovaCRM
    with create_engine(f"{base}/{name}").begin() as cx:
        cx.exec_driver_sql("CREATE TABLE contacts (id integer)")
    replace_contacts(f"{base}/{name}", clean)
    s = settings.model_copy(update={"session_secret": "test-secret"})
    h, rid = run(s, calls=[GROWTH_CALL])
    c = api(h, contacts=lambda: (clean, clean), settings=s)
    r = c.put(f"/runs/{rid}/migration-source", json=body(host="localhost", database=name, username="postgres",
                                                          password="postgres"), headers=ORIGIN)
    assert r.status_code == 200, r.text
    out = c.post(f"/runs/{rid}/migration-check", headers=ORIGIN).json()
    assert out["passed"] is True
    assert c.get("/migration/summary", params={"run_id": rid}).json()["source_count"] == len(clean)
