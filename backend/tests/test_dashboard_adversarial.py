"""Adversarial review of the AE directory / migration demo APIs (tester review). xfail(strict) = known defect:
the test flips to XPASS (and fails the suite) once production code is fixed, so remove the marker then."""

import pytest
from fastapi.testclient import TestClient
from test_migration_demo_api import ORIGIN, FakeDb
from test_migration_demo_api import make as make_mig

from app.container import Container
from app.main import create_app
from app.services.migration_check import MigrationDataUnavailable
from app.sim import Harness

PHONE = "+15108228650"


def _api(settings):
    h = Harness(settings)
    return TestClient(create_app(Container(settings=settings, store=h.store, runner=h.runner, start_poller=False))), h.store


@pytest.mark.parametrize("body", [
    {"email": "maya@novacrm.example", "phone": PHONE},                                  # name missing
    {"email": "maya@novacrm.example", "name": "Maya", "phone": PHONE, "active": "maybe"},
    {"email": "maya@novacrm.example", "name": "Maya", "phone": PHONE + "0" * 60},      # phone > max_length
])
def test_schema_422_never_echoes_the_phone_and_is_a_string(settings, body):
    c, store = _api(settings)
    r = c.post("/aes", json=body)
    assert r.status_code == 422 and store.list_aes() == []
    assert "5108228650" not in r.text
    assert isinstance(r.json()["detail"], str)


def test_patch_schema_422_is_a_string(settings):
    c, store = _api(settings)
    store.add_ae("maya@novacrm.example", "Maya", PHONE)
    r = c.patch("/aes/maya@novacrm.example", json={"active": "nope", "phone": PHONE})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_a_write_that_happened_is_audited_even_if_the_reread_fails(settings):
    db = FakeDb()
    calls = {"n": 0}

    def flaky_load():
        calls["n"] += 1
        if calls["n"] >= 2:                         # first read ok, read after the write fails
            raise MigrationDataUnavailable("OperationalError: connection reset")
        return db.load()

    c, store, _ = make_mig(settings, db)
    c.app.state.container.migration_contacts = flaky_load
    r = c.post("/migration/demo/fix", headers=ORIGIN)
    assert db.writes == 1                           # the destination WAS rewritten
    assert r.status_code == 503
    assert [a["step"] for a in store.list_audit("migration_demo")] == ["demo_fix"]


def test_a_failed_write_leaves_no_partial_audit_claiming_success(settings):
    from sqlalchemy.exc import OperationalError
    db = FakeDb()
    c, store, _ = make_mig(settings, db)

    def boom(rows):
        raise OperationalError("INSERT", {}, Exception("down"))
    c.app.state.container.migration_write = boom
    r = c.post("/migration/demo/fix", headers=ORIGIN)
    assert r.status_code == 503 and "down" not in r.json()["detail"]
    assert not [a for a in store.list_audit("migration_demo") if a["output"].get("changed")]


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://u:pw@db:5432/MIG_DEST",
    "postgresql+psycopg://u:pw@db:5432/mig_dest_prod",
    "postgresql+psycopg://u:pw@db:5432/mig_source",
])
def test_refusal_is_exact_on_the_database_name(settings, url):
    c, store, db = make_mig(settings, mig_dest_url=url)
    assert c.post("/migration/demo/reset", headers=ORIGIN).status_code == 409 and db.writes == 0


def test_refusal_only_checks_the_database_name_not_the_host(settings):
    """Documents the guard's scope: any host is accepted if the database is called mig_dest (MIG_DEST_URL is
    operator-set config; the name check stops a mis-pointed real customer database, not a hostile operator)."""
    c, _, db = make_mig(settings, mig_dest_url="postgresql+psycopg://u:pw@prod-crm.customer.example:5432/mig_dest")
    assert c.post("/migration/demo/fix", headers=ORIGIN).status_code == 200 and db.writes == 1


def test_patch_with_unnormalised_path_email_and_noop_body_writes_nothing(settings):
    c, store = _api(settings)
    store.add_ae("maya@novacrm.example", "Maya", PHONE)
    r = c.patch("/aes/%20MAYA@novacrm.example%20", json={"phone": "+1 (510) 822-8650", "name": " Maya "})
    assert r.status_code == 200 and "5108228650" not in r.text
    assert store.list_audit("directory") == []      # normalised values equal the stored ones: no change, no audit


def test_create_response_and_history_never_contain_the_full_phone_in_any_format(settings):
    c, _ = _api(settings)
    r = c.post("/aes", json={"email": "m@x.example", "name": "M", "phone": "+1 (510) 822-8650"})
    assert r.status_code == 201
    for text in (r.text, c.get("/aes").text, c.get("/aes/history").text):
        assert "5108228650" not in text and "822-8650" not in text
