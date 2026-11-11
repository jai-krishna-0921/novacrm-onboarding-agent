"""PostgreSQL implementation of the Store protocol (SQLAlchemy Core). Schema is managed by Alembic."""

from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Integer,
    MetaData,
    Numeric,
    Sequence,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.services.store import RUN_FIELDS as SHARED_RUN_FIELDS
from app.services.store import now

metadata = MetaData()
J = JSON().with_variant(JSONB(), "postgresql")

run_seq = Sequence("onboarding_runs_seq", metadata=metadata)
runs = Table(
    "onboarding_runs", metadata,
    Column("id", String, primary_key=True),
    Column("seq", BigInteger, run_seq, server_default=run_seq.next_value(), unique=True, nullable=False),
    Column("status", String), Column("gmail_message_id", String, unique=True), Column("opportunity_id", String),
    Column("customer_key", String, index=True), Column("customer_name", String), Column("plan_tier", String),
    Column("rocketlane_project_id", String), Column("slack_channel_id", String), Column("error", Text),
    Column("created_at", String), Column("updated_at", String),
)
audit_log = Table(
    "audit_log", metadata, Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String, index=True), Column("ts", String), Column("agent", String), Column("step", String),
    Column("input", J), Column("output", J), Column("rationale", Text), Column("status", String),
)
tool_calls = Table(
    "tool_calls", metadata, Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String, index=True), Column("ts", String), Column("agent", String), Column("tool", String),
    Column("args", J), Column("result", J), Column("status", String), Column("attempt", Integer),
    Column("latency_ms", Integer), Column("idempotency_key", String),
)
llm_calls = Table(
    "llm_calls", metadata, Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String, index=True), Column("ts", String), Column("agent", String), Column("node", String),
    Column("model", String), Column("prompt_version", String), Column("input_tokens", Integer),
    Column("output_tokens", Integer), Column("latency_ms", Integer), Column("cost_usd", Numeric(12, 6)),
    Column("stop_reason", String),
)
idempotency_keys = Table("idempotency_keys", metadata, Column("key", String, primary_key=True),
                         Column("result", J), Column("created_at", String))
escalations = Table(
    "escalations", metadata, Column("key", String, primary_key=True), Column("run_id", String, index=True),
    Column("reason", Text), Column("kind", String), Column("agent", String), Column("status", String),
    Column("resolved_by", String), Column("resolution", J), Column("created_at", String),
)
kv = Table("kv", metadata, Column("key", String, primary_key=True), Column("value", Text))
ae_directory = Table("ae_directory", metadata, Column("email", String, primary_key=True), Column("name", String),
                     Column("phone", String), Column("active", Boolean, nullable=False, default=True))

RUN_FIELDS = {c.name for c in runs.columns} - {"id", "seq", "created_at", "updated_at"}
assert RUN_FIELDS == SHARED_RUN_FIELDS, f"run columns drifted from MemoryStore: {RUN_FIELDS ^ SHARED_RUN_FIELDS}"


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result]


class PgStore:
    def __init__(self, url: str):
        self.engine = create_engine(url, pool_pre_ping=True, future=True)

    def create_all(self) -> None:
        """Tests only; the app uses `alembic upgrade head`."""
        metadata.create_all(self.engine)

    def _exec(self, stmt) -> Any:
        with self.engine.begin() as c:
            return c.execute(stmt)

    # idempotency
    def get_idem(self, key: str) -> dict | None:
        with self.engine.connect() as c:
            return c.execute(select(idempotency_keys.c.result).where(idempotency_keys.c.key == key)).scalar()

    def put_idem(self, key: str, value: dict) -> None:
        self._exec(pg_insert(idempotency_keys).values(key=key, result=value, created_at=now())
                   .on_conflict_do_nothing(index_elements=["key"]))

    # append-only logs
    def add_audit(self, row: dict) -> None:
        self._exec(insert(audit_log).values(**row))

    def add_tool_call(self, row: dict) -> None:
        self._exec(insert(tool_calls).values(**row))

    def add_llm_call(self, row: dict) -> None:
        self._exec(insert(llm_calls).values(**{k: v for k, v in row.items() if k in llm_calls.c}))

    def _list(self, table, run_id: str) -> list[dict]:
        with self.engine.connect() as c:
            rows = _rows(c.execute(select(table).where(table.c.run_id == run_id).order_by(table.c.id)))
        for r in rows:
            r.pop("id", None)
            if "cost_usd" in r and r["cost_usd"] is not None:
                r["cost_usd"] = float(r["cost_usd"])
        return rows

    def list_audit(self, run_id: str) -> list[dict]:
        return self._list(audit_log, run_id)

    def list_tool_calls(self, run_id: str) -> list[dict]:
        return self._list(tool_calls, run_id)

    def list_llm_calls(self, run_id: str) -> list[dict]:
        return self._list(llm_calls, run_id)

    # runs
    def upsert_run(self, run_id: str, **fields: Any) -> None:
        unknown = set(fields) - RUN_FIELDS
        if unknown:
            raise ValueError(f"unknown run fields: {unknown}")
        ts = now()
        stmt = pg_insert(runs).values(id=run_id, created_at=ts, updated_at=ts, **fields)
        self._exec(stmt.on_conflict_do_update(index_elements=["id"], set_={**fields, "updated_at": ts}))

    def get_run(self, run_id: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.id == run_id)).first()
        return dict(r._mapping) if r else None

    def find_run_by_message(self, gmail_message_id: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.gmail_message_id == gmail_message_id)).first()
        return dict(r._mapping) if r else None

    def find_active_run(self, *, opportunity_id: str | None, customer_key: str,
                        exclude_run_id: str) -> dict | None:
        match = runs.c.customer_key == customer_key
        if opportunity_id:
            match = match | (runs.c.opportunity_id == opportunity_id)
        live = runs.c.status.is_(None) | runs.c.status.not_in(["FAILED", "CANCELLED"])
        q = select(runs).where(match, runs.c.id != exclude_run_id, runs.c.rocketlane_project_id.is_not(None),
                               live).limit(1)
        with self.engine.connect() as c:
            r = c.execute(q).first()
        return dict(r._mapping) if r else None

    def list_runs(self, limit: int = 100) -> list[dict]:
        with self.engine.connect() as c:
            return _rows(c.execute(select(runs).order_by(runs.c.seq.desc()).limit(limit)))

    # escalations
    def upsert_escalation(self, key: str, row: dict) -> None:
        stmt = pg_insert(escalations).values(key=key, created_at=now(), **row)
        self._exec(stmt.on_conflict_do_update(index_elements=["key"], set_=row))

    def list_escalations(self, status: str | None = None) -> list[dict]:
        q = select(escalations).order_by(escalations.c.created_at)
        if status:
            q = q.where(escalations.c.status == status)
        with self.engine.connect() as c:
            return _rows(c.execute(q))

    # kv + directory
    def get_kv(self, key: str) -> str | None:
        with self.engine.connect() as c:
            return c.execute(select(kv.c.value).where(kv.c.key == key)).scalar()

    def set_kv(self, key: str, value: str) -> None:
        self._exec(pg_insert(kv).values(key=key, value=value)
                   .on_conflict_do_update(index_elements=["key"], set_={"value": value}))

    def upsert_ae(self, email: str, name: str, phone: str, active: bool = True) -> None:
        e = email.strip().lower()
        self._exec(pg_insert(ae_directory).values(email=e, name=name, phone=phone, active=active)
                   .on_conflict_do_update(index_elements=["email"], set_={"name": name, "phone": phone,
                                                                         "active": active}))

    def lookup_ae(self, email: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(ae_directory).where(ae_directory.c.email == email.strip().lower())).first()
        return dict(r._mapping) if r else None

