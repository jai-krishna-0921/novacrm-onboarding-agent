"""AE directory API (GET/POST /aes, PATCH /aes/{email}): validation, no full phone in responses, audit of every
change, login + same-origin required, and deactivation (no delete) blocks the intake sender check."""

import pytest
from fastapi.testclient import TestClient
from test_workflow import EMAIL, ENTERPRISE_CALL, GOOD

from app.config import normalize_e164
from app.container import Container, DbDirectory
from app.llm.schemas import DealFields
from app.main import create_app
from app.services.audit import mask_phone
from app.sim import FakeLLM, Harness

ORIGIN = {"Origin": "http://testserver"}
SHAPE = {"email", "name", "phone_masked", "active", "updated_at"}
MAYA = {"email": "maya@novacrm.example", "name": "Maya Rao", "phone": "+1 (510) 822-8650"}


def client(settings, **overrides):
    s = settings.model_copy(update=overrides)
    h = Harness(s)
    return TestClient(create_app(Container(settings=s, store=h.store, runner=h.runner, start_poller=False)),
                      follow_redirects=False), h.store


@pytest.fixture
def api(settings):
    return client(settings)


# ---------------- phone normaliser (shared with Settings) ----------------

@pytest.mark.parametrize("raw,e164", [("+91 97899 98350", "+919789998350"), ("+1 (510) 822-8650", "+15108228650"),
                                      (" +15108228650 ", "+15108228650"), ("+44.20.7946.0958", "+442079460958")])
def test_normaliser_accepts_dashboard_formatting(raw, e164):
    assert normalize_e164(raw) == e164


@pytest.mark.parametrize("raw", ["5108228650", "00 1 510 822 8650", "+1 510 CALL ME", "+1234567", "+1234567890123456",
                                 "++15108228650", "+1 510 822 8650 ext 2"])
def test_normaliser_rejects_non_e164(raw):
    with pytest.raises(ValueError):
        normalize_e164(raw)


def test_settings_still_use_the_normaliser():
    from app.config import Settings
    assert Settings(_env_file=None, demo_ae_phone="+91 97899 98350").demo_ae_phone == "+919789998350"
    with pytest.raises(ValueError):
        Settings(_env_file=None, retell_from_number="5108228650")


# ---------------- create / list ----------------

def test_create_then_list_masks_the_phone_and_sorts_by_name(api):
    c, store = api
    r = c.post("/aes", json={**MAYA, "email": "  Maya@NovaCRM.Example "})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == SHAPE and body["email"] == "maya@novacrm.example" and body["active"] is True
    assert body["phone_masked"].endswith("8650") and "510822" not in body["phone_masked"]
    assert store.lookup_ae("maya@novacrm.example")["phone"] == "+15108228650"   # stored E.164
    c.post("/aes", json={"email": "abe@novacrm.example", "name": "Abe", "phone": "+919789998350", "active": False})
    rows = c.get("/aes").json()
    assert [a["name"] for a in rows] == ["Abe", "Maya Rao"] and all(set(a) == SHAPE for a in rows)
    assert "+15108228650" not in c.get("/aes").text and "9789998350" not in c.get("/aes").text


def test_duplicate_email_is_409_and_leaves_the_row_alone(api):
    c, store = api
    assert c.post("/aes", json=MAYA).status_code == 201
    r = c.post("/aes", json={**MAYA, "email": "MAYA@novacrm.example", "name": "Impostor"})
    assert r.status_code == 409 and isinstance(r.json()["detail"], str)
    assert store.lookup_ae("maya@novacrm.example")["name"] == "Maya Rao"


@pytest.mark.parametrize("bad", [
    {"email": "not-an-email"}, {"email": "a@b"}, {"email": "a b@x.example"}, {"email": "@x.example"},
    {"email": ""}, {"name": "   "}, {"name": ""}, {"phone": "5108228650"}, {"phone": "+12"}, {"phone": ""},
    {"phone": "+1 510 abc 8650"},
])
def test_invalid_input_is_422_with_a_readable_detail_and_nothing_stored(api, bad):
    c, store = api
    r = c.post("/aes", json={**MAYA, **bad})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert store.list_aes() == [] and c.get("/aes").json() == []


# ---------------- update ----------------

def test_patch_updates_given_fields_only(api):
    c, store = api
    c.post("/aes", json=MAYA)
    r = c.patch("/aes/MAYA@novacrm.example", json={"phone": "+91 97899 98350"})
    assert r.status_code == 200 and set(r.json()) == SHAPE and r.json()["phone_masked"].endswith("8350")
    ae = store.lookup_ae("maya@novacrm.example")
    assert (ae["name"], ae["phone"], ae["active"]) == ("Maya Rao", "+919789998350", True)
    assert c.patch("/aes/maya@novacrm.example", json={"active": False, "name": " Maya R "}).json()["name"] == "Maya R"
    assert store.lookup_ae("maya@novacrm.example")["active"] is False


def test_patch_unknown_is_404_and_invalid_is_422(api):
    c, store = api
    assert c.patch("/aes/ghost@x.example", json={"name": "X"}).status_code == 404
    c.post("/aes", json=MAYA)
    for bad in ({"name": "  "}, {"phone": "12345"}):
        r = c.patch("/aes/maya@novacrm.example", json=bad)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert store.lookup_ae("maya@novacrm.example")["phone"] == "+15108228650"


def test_there_is_no_delete(api):
    c, _ = api
    c.post("/aes", json=MAYA)
    assert c.delete("/aes/maya@novacrm.example").status_code == 405


# ---------------- audit ----------------

def test_every_change_is_audited_with_who_what_when_and_a_masked_phone(settings):
    c, store = client(settings, auth_disabled=False, admin_username="admin", admin_password="pw",
                      session_secret="s")
    assert c.post("/auth/login", json={"username": "admin", "password": "pw"}, headers=ORIGIN).status_code == 200
    c.post("/aes", json=MAYA, headers=ORIGIN)
    c.patch("/aes/maya@novacrm.example", json={"phone": "+919789998350", "active": False}, headers=ORIGIN)
    c.patch("/aes/maya@novacrm.example", json={"active": False}, headers=ORIGIN)   # no change: nothing to audit
    rows = store.list_audit("directory")
    assert [r["step"] for r in rows] == ["ae_create", "ae_update"]
    assert all(r["input"]["by"] == "admin" and r["ts"] and r["rationale"] for r in rows)
    upd = rows[1]["output"]
    assert upd["email"] == "maya@novacrm.example"
    assert upd["before"] == {"phone": mask_phone("+15108228650"), "active": True}
    assert upd["after"] == {"phone": mask_phone("+919789998350"), "active": False}
    text = str(rows)
    assert "5108228650" not in text and "9789998350" not in text
    hist = c.get("/aes/history").json()
    assert [h["step"] for h in hist] == ["ae_update", "ae_create"]   # newest first
    assert "9789998350" not in str(hist)


# ---------------- login + same-origin (auth middleware) ----------------

@pytest.mark.parametrize("method,path", [("get", "/aes"), ("post", "/aes"), ("patch", "/aes/maya@novacrm.example"),
                                         ("get", "/aes/history")])
def test_directory_requires_login(settings, method, path):
    c, store = client(settings, auth_disabled=False, admin_username="admin", admin_password="pw", session_secret="s")
    r = getattr(c, method)(path, **({"json": MAYA} if method != "get" else {}), headers=ORIGIN)
    assert r.status_code == 401 and store.list_aes() == []


@pytest.mark.parametrize("method,path", [("post", "/aes"), ("patch", "/aes/maya@novacrm.example")])
def test_directory_writes_need_same_origin_even_when_signed_in(settings, method, path):
    c, store = client(settings, auth_disabled=False, admin_username="admin", admin_password="pw", session_secret="s")
    c.post("/auth/login", json={"username": "admin", "password": "pw"}, headers=ORIGIN)
    store.add_ae("maya@novacrm.example", "Maya Rao", "+15108228650")
    r = getattr(c, method)(path, json={**MAYA, "name": "Evil"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and store.lookup_ae("maya@novacrm.example")["name"] == "Maya Rao"


# ---------------- deactivation blocks intake (real-mode directory = the store) ----------------

def _with_db_directory(settings, active: bool):
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.directory = DbDirectory(h.store)        # what build_container wires in real mode
    h.store.upsert_ae("jai@novacrm.example", "Jai Krishna", "+919876543210", active)
    h.world.voice.script = [ENTERPRISE_CALL]
    return h, h.receive(EMAIL)


def test_deactivated_ae_fails_the_sender_check(settings):
    h, rid = _with_db_directory(settings, active=False)
    assert h.status(rid) == "ESCALATED"
    [esc] = [e for e in h.store.list_escalations("open") if e["run_id"] == rid]
    assert "not an active AE" in esc["reason"]
    assert not [r for r in h.store.audit_rows if r["run_id"] == rid and r["step"] == "extract_fields"]


def test_active_ae_from_the_db_directory_passes_the_sender_check(settings):
    h, rid = _with_db_directory(settings, active=True)
    assert any(r["run_id"] == rid and r["step"] == "check_sender" and r["status"] == "ok" for r in h.store.audit_rows)


def test_startup_seed_never_overwrites_dashboard_edits(settings):
    from app.db.seed import seed_ae
    from app.services.store import MemoryStore
    s = settings.model_copy(update={"demo_ae_email": "Jai@NovaCRM.example", "demo_ae_name": "Jai",
                                    "demo_ae_phone": "+919876543210"})
    store = MemoryStore()
    assert "added" in seed_ae(store, s)
    store.update_ae("jai@novacrm.example", phone="+919789998350", active=False)   # edited in the UI
    assert "left unchanged" in seed_ae(store, s)                                   # restart
    ae = store.lookup_ae("jai@novacrm.example")
    assert (ae["phone"], ae["active"]) == ("+919789998350", False)
