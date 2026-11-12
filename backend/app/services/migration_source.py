"""Per-customer migration source: the customer's own database (e.g. Mavis's old CRM), entered in the dashboard.

The connection is tested (a read of `contacts`) before it is saved; the URL is stored encrypted (Fernet, key derived
from SESSION_SECRET) and only a password-free display string is ever returned. The check reads it with SELECT only.
Runs without one use the shared demo source."""

import base64
import hashlib
import json
import re
from collections.abc import Callable

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.engine import URL

from app.services.migration_check import load_contacts
from app.services.store import Store, now

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}$")


class SourceIn(BaseModel):
    host: str = Field(max_length=253)
    port: int = Field(5432, ge=1, le=65535)
    database: str = Field(max_length=63)
    username: str = Field(max_length=63)
    password: str = Field(max_length=256)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        if not HOST_RE.match(v.strip()):
            raise ValueError("host: letters, digits, dots and hyphens only")
        return v.strip()

    @field_validator("database", "username")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v.strip()):
            raise ValueError("letters, digits, _ . - only")
        return v.strip()


def _key(run_id: str) -> str:
    return f"migration_source:{run_id}"


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"migration-source:{secret}".encode()).digest()))


def build_url(x: SourceIn) -> str:
    return URL.create("postgresql+psycopg", username=x.username, password=x.password, host=x.host, port=x.port,
                      database=x.database, query={"connect_timeout": "5"}).render_as_string(hide_password=False)


def display(x: SourceIn) -> str:
    return f"postgresql://{x.username}@{x.host}:{x.port}/{x.database}"


def save(store: Store, secret: str, run_id: str, x: SourceIn, by: str) -> dict:
    record = {"enc": _fernet(secret).encrypt(build_url(x).encode()).decode(), "display": display(x),
              "saved_at": now(), "by": by}
    store.set_kv(_key(run_id), json.dumps(record))
    return public(store, run_id)


def clear(store: Store, run_id: str) -> None:
    store.set_kv(_key(run_id), "")


def _record(store: Store, run_id: str) -> dict | None:
    raw = store.get_kv(_key(run_id))
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def public(store: Store, run_id: str) -> dict:
    r = _record(store, run_id)
    return ({"configured": True, "display": r["display"], "saved_at": r.get("saved_at"), "by": r.get("by")}
            if r else {"configured": False, "display": None, "saved_at": None, "by": None})


def url_for(store: Store, secret: str, run_id: str) -> str | None:
    r = _record(store, run_id)
    if not r or not secret:
        return None
    try:
        return _fernet(secret).decrypt(r["enc"].encode()).decode()
    except (InvalidToken, KeyError):
        return None


def contacts_for(store: Store, secret: str, run_id: str | None, default: Callable[[], tuple] | None):
    """The (source, destination) loader for a run: its own customer database as the source when one is saved,
    the shared demo source otherwise. The destination (what landed in NovaCRM) is unchanged."""
    url = url_for(store, secret, run_id) if run_id else None
    if not url or default is None:
        return default
    return lambda: (load_contacts(url), default()[1])
