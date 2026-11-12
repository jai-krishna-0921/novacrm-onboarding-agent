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
    case,
    cast,
    create_engine,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.services.store import (
    AE_FIELDS,
    CALL_TOOL,
    CREATE_PROJECT_TOOL,
    DEAD_STATUSES,
    IN_FLIGHT,
    IN_FLIGHT_STATUSES,
    LATENCY_EDGES_MS,
    LLM_ROW_FIELDS,
    TOOL_ROW_FIELDS,
    TRACKING_FIELDS,
    _reservation,
    by_calls,
    email_to_project,
    llm_group,
    merge_tool_stats,
    now,
)
from app.services.store import RUN_FIELDS as SHARED_RUN_FIELDS

metadata = MetaData()
J = JSON().with_variant(JSONB(), "postgresql")

run_seq = Sequence("onboarding_runs_seq", metadata=metadata)
runs = Table(
    "onboarding_runs", metadata,
    Column("id", String, primary_key=True),
    Column("seq", BigInteger, run_seq, server_default=run_seq.next_value(), unique=True, nullable=False),
    Column("status", String, index=True), Column("gmail_message_id", String, unique=True),
    Column("opportunity_id", String, index=True),
    Column("customer_key", String, index=True), Column("customer_name", String), Column("plan_tier", String),
    Column("rocketlane_project_id", String), Column("slack_channel_id", String, index=True), Column("error", Text),
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
                     Column("phone", String), Column("active", Boolean, nullable=False, default=True),
                     Column("updated_at", String))
migration_checks = Table(
    "migration_checks", metadata, Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String, index=True), Column("batch_id", String), Column("ts", String),
    Column("check_name", String), Column("passed", Boolean, nullable=False), Column("details", J),
)

project_tracking = Table(
    "project_tracking", metadata, Column("run_id", String, primary_key=True), Column("tasks", J),
    Column("synced_at", String), Column("handoff_summary", Text), Column("handed_off_at", String),
    Column("handoff_slack", String),
)

RUN_FIELDS = {c.name for c in runs.columns} - {"id", "seq", "created_at", "updated_at"}
assert RUN_FIELDS == SHARED_RUN_FIELDS, f"run columns drifted from MemoryStore: {RUN_FIELDS ^ SHARED_RUN_FIELDS}"


def _in_flight():
    return cast(idempotency_keys.c.result, JSONB).has_key(IN_FLIGHT)


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
    # An in-flight reservation is stored as {IN_FLIGHT: started_at} in the same JSONB column (no schema change).
    def get_idem(self, key: str) -> dict | None:
        with self.engine.connect() as c:
            v = c.execute(select(idempotency_keys.c.result).where(idempotency_keys.c.key == key)).scalar()
        return None if v is None or IN_FLIGHT in v else v

    def put_idem(self, key: str, value: dict) -> None:
        # First completed write wins; a completion replaces an in-flight reservation.
        stmt = pg_insert(idempotency_keys).values(key=key, result=value, created_at=now())
        self._exec(stmt.on_conflict_do_update(
            index_elements=["key"], set_={"result": stmt.excluded.result, "created_at": stmt.excluded.created_at},
            where=_in_flight()))

    def reserve_idem(self, key: str) -> dict | None:
        with self.engine.begin() as c:
            got = c.execute(pg_insert(idempotency_keys).values(key=key, result={IN_FLIGHT: now()}, created_at=now())
                            .on_conflict_do_nothing(index_elements=["key"]).returning(idempotency_keys.c.key)).first()
            if got:
                return None
            return _reservation(c.execute(select(idempotency_keys.c.result)
                                          .where(idempotency_keys.c.key == key)).scalar())

    def peek_idem(self, key: str) -> dict | None:
        with self.engine.connect() as c:
            return _reservation(c.execute(select(idempotency_keys.c.result)
                                          .where(idempotency_keys.c.key == key)).scalar())

    def release_idem(self, key: str) -> None:
        self._exec(delete(idempotency_keys).where(idempotency_keys.c.key == key,
                                                  _in_flight()))

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

    def add_migration_check(self, row: dict) -> None:
        self._exec(insert(migration_checks).values(**row))

    def list_migration_checks(self, run_id: str) -> list[dict]:
        return self._list(migration_checks, run_id)

    def latest_migration_check(self) -> dict | None:
        m = migration_checks.c
        with self.engine.connect() as c:
            last = c.execute(select(m.batch_id, m.ts, m.run_id).order_by(m.id.desc()).limit(1)).first()
            if last is None:
                return None
            passed = c.execute(select(func.bool_and(m.passed)).where(m.batch_id == last.batch_id)).scalar()
        return {"batch_id": last.batch_id, "ts": last.ts, "passed": bool(passed), "run_id": last.run_id}

    # ---------- observability: filtering, paging and aggregation run in SQL ----------
    def _page(self, table, fields: tuple, limit: int, offset: int, since: str | None, **eq: str | None) -> dict:
        where = [table.c[k] == v for k, v in eq.items() if v is not None]
        if since is not None:
            where.append(table.c.ts >= since)
        cols = [runs.c.customer_name if f == "customer_name" else table.c[f] for f in fields]
        q = (select(*cols).select_from(table.outerjoin(runs, runs.c.id == table.c.run_id)).where(*where)
             .order_by(table.c.id.desc()).limit(limit).offset(offset))
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(table).where(*where)).scalar_one()
            rows = _rows(c.execute(q))
        for r in rows:
            if r.get("cost_usd") is not None:
                r["cost_usd"] = float(r["cost_usd"])
        return {"total": total, "rows": rows}

    def page_llm_calls(self, *, limit, offset, model=None, node=None, run_id=None, since=None):
        return self._page(llm_calls, LLM_ROW_FIELDS, limit, offset, since, model=model, node=node, run_id=run_id)

    def page_tool_calls(self, *, limit, offset, tool=None, agent=None, status=None, run_id=None, since=None):
        return self._page(tool_calls, TOOL_ROW_FIELDS, limit, offset, since, tool=tool, agent=agent,
                          status=status, run_id=run_id)

    def get_tool_call(self, call_id: int) -> dict | None:
        q = (select(tool_calls, runs.c.customer_name)
             .select_from(tool_calls.outerjoin(runs, runs.c.id == tool_calls.c.run_id))
             .where(tool_calls.c.id == call_id))
        with self.engine.connect() as c:
            r = c.execute(q).first()
        return dict(r._mapping) if r else None

    def llm_stats(self, since: str | None = None) -> dict:
        t = llm_calls.c
        where = [t.ts >= since] if since is not None else []
        aggs = [func.count(), func.sum(t.input_tokens), func.sum(t.output_tokens), func.sum(t.cost_usd),
                func.avg(t.latency_ms), func.percentile_cont(.5).within_group(t.latency_ms),
                func.percentile_cont(.95).within_group(t.latency_ms)]
        day = func.substr(t.ts, 1, 10)
        bucket = case(*[(t.latency_ms < edge, i) for i, edge in enumerate(LATENCY_EDGES_MS)],
                      else_=len(LATENCY_EDGES_MS))
        with self.engine.connect() as c:
            def groups(*keys) -> list[tuple]:
                q = select(*keys, *aggs).where(*where)
                return list(c.execute(q.group_by(*keys) if keys else q))

            totals = llm_group(*groups()[0])
            by_model = [{"model": r[0], **llm_group(*r[1:])} for r in groups(t.model)]
            by_node = [{"agent": r[0], "node": r[1], **llm_group(*r[2:])} for r in groups(t.agent, t.node)]
            by_prompt = [{"prompt_version": r[0], **llm_group(*r[1:])} for r in groups(t.prompt_version)]
            by_day = sorted(({"day": r[0], **llm_group(*r[1:])} for r in groups(day)), key=lambda d: d["day"])
            hist = c.execute(select(bucket, func.count()).where(t.latency_ms.is_not(None), *where).group_by(bucket))
            buckets = [0] * (len(LATENCY_EDGES_MS) + 1)
            for b, n in hist:
                buckets[b] = n
        return {"totals": totals, "by_model": by_calls(by_model, "model"),
                "by_node": by_calls(by_node, "agent", "node"), "by_prompt": by_calls(by_prompt, "prompt_version"),
                "by_day": by_day, "latency_buckets": buckets}

    def tool_stats(self, since: str | None = None) -> list[dict]:
        t, a = tool_calls.c, audit_log.c
        ok, failed = (func.count().filter(t.status == s) for s in ("ok", "failed"))
        q = select(t.tool, func.count(), ok, failed, func.avg(t.latency_ms),
                   func.percentile_cont(.95).within_group(t.latency_ms))
        aq = (select(a.step, a.status, func.count())
              .where(a.step.like("tool:%"), a.status.in_(["replayed", "blocked"])))
        if since is not None:
            q, aq = q.where(t.ts >= since), aq.where(a.ts >= since)
        with self.engine.connect() as c:
            rows = [dict(tool=r[0], calls=r[1], ok=r[2], failed=r[3], avg=r[4], p95=r[5])
                    for r in c.execute(q.group_by(t.tool))]
            counts = {(r[0][5:], r[1]): r[2] for r in c.execute(aq.group_by(a.step, a.status))}
        return merge_tool_stats(rows, counts)

    def overview_stats(self) -> dict:
        t, a = tool_calls.c, audit_log.c
        day = func.substr(runs.c.created_at, 1, 10)
        first = (select(runs.c.created_at, func.min(t.ts))
                 .select_from(runs.join(tool_calls, tool_calls.c.run_id == runs.c.id))
                 .where(t.tool == CREATE_PROJECT_TOOL, t.status == "ok").group_by(runs.c.id, runs.c.created_at))
        with self.engine.connect() as c:
            by_status = {(s or "UNKNOWN"): n for s, n in c.execute(select(runs.c.status, func.count())
                                                                   .group_by(runs.c.status))}
            by_day = [{"day": d, "runs": n} for d, n in c.execute(select(day, func.count()).group_by(day)
                                                                  .order_by(day))]
            esc_open = c.execute(select(func.count()).select_from(escalations)
                                 .where(escalations.c.status == "open")).scalar_one()
            pairs = [(r[0], r[1]) for r in c.execute(first)]
            placed = c.execute(select(func.count()).select_from(tool_calls)
                               .where(t.tool == CALL_TOOL, t.status == "ok")).scalar_one()
            outcomes: dict[str, int] = dict(c.execute(select(a.status, func.count()).where(a.step == "decide_tier")
                                      .group_by(a.status)).all())
            n_llm, cost = c.execute(select(func.count(), func.sum(llm_calls.c.cost_usd))).one()
        return {
            "runs_total": sum(by_status.values()), "runs_by_status": by_status, "runs_by_day": by_day,
            "escalations_open": esc_open, "email_to_project": email_to_project(pairs),
            "calls": {"placed": placed, **{k: int(outcomes.get(k, 0)) for k in ("confirmed", "unclear", "no_answer")}},
            "llm_calls": n_llm, "llm_cost_usd": round(float(cost or 0), 6),
        }

    def activity_stats(self, since: str) -> dict:
        a, t = audit_log.c, tool_calls.c
        newest = select(a.agent, a.ts, a.step, a.rationale, a.status).where(
            a.id.in_(select(func.max(a.id)).group_by(a.agent)))
        with self.engine.connect() as c:
            last = {r.agent: {"ts": r.ts, "step": r.step, "rationale": r.rationale, "status": r.status}
                    for r in c.execute(newest)}
            steps = {(r[0], r[1], r[2]): r[3] for r in c.execute(
                select(a.agent, a.step, a.status, func.count()).group_by(a.agent, a.step, a.status))}
            tq = select(t.agent, t.tool, t.status, func.count()).group_by(t.agent, t.tool, t.status)
            tools = {(r[0], r[1], r[2]): r[3] for r in c.execute(tq)}
            tools_since = {(r[0], r[1], r[2]): r[3] for r in c.execute(tq.where(t.ts >= since))}
            runs_since = c.execute(select(func.count()).select_from(runs)
                                   .where(runs.c.created_at >= since)).scalar_one()
        return {"last": last, "steps": steps, "tools": tools, "tools_since": tools_since, "runs_since": runs_since}

    # project tracking (Agent 3)
    def get_tracking(self, run_id: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(project_tracking).where(project_tracking.c.run_id == run_id)).first()
        return dict(r._mapping) if r else None

    def upsert_tracking(self, run_id: str, **fields: Any) -> None:
        if unknown := set(fields) - TRACKING_FIELDS:
            raise ValueError(f"unknown tracking fields: {unknown}")
        stmt = pg_insert(project_tracking).values(run_id=run_id, **fields)
        self._exec(stmt.on_conflict_do_update(index_elements=["run_id"], set_=fields) if fields
                   else stmt.on_conflict_do_nothing(index_elements=["run_id"]))

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
        live = runs.c.status.is_(None) | runs.c.status.not_in(sorted(DEAD_STATUSES))
        busy = runs.c.rocketlane_project_id.is_not(None) | runs.c.status.in_(sorted(IN_FLIGHT_STATUSES))
        q = select(runs).where(match, runs.c.id != exclude_run_id, busy, live).limit(1)
        with self.engine.connect() as c:
            r = c.execute(q).first()
        return dict(r._mapping) if r else None

    def list_runs(self, limit: int = 100) -> list[dict]:
        with self.engine.connect() as c:
            return _rows(c.execute(select(runs).order_by(runs.c.seq.desc()).limit(limit)))

    def list_runs_by_status(self, statuses: set[str]) -> list[dict]:
        with self.engine.connect() as c:
            return _rows(c.execute(select(runs).where(runs.c.status.in_(sorted(statuses))).order_by(runs.c.seq)))

    def list_trackable_runs(self, exclude_statuses: set[str]) -> list[dict]:
        t = project_tracking.c
        live = runs.c.status.is_(None) | runs.c.status.not_in(sorted(exclude_statuses))
        q = (select(runs).outerjoin(project_tracking, t.run_id == runs.c.id)
             .where(runs.c.rocketlane_project_id.is_not(None), live,
                    t.handed_off_at.is_(None) | (t.handoff_slack == "pending")).order_by(runs.c.seq))
        with self.engine.connect() as c:
            return _rows(c.execute(q))

    def find_run_by_channel(self, channel_id: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(runs).where(runs.c.slack_channel_id == channel_id).limit(1)).first()
        return dict(r._mapping) if r else None

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
        e, ts = email.strip().lower(), now()
        self._exec(pg_insert(ae_directory).values(email=e, name=name, phone=phone, active=active, updated_at=ts)
                   .on_conflict_do_update(index_elements=["email"], set_={"name": name, "phone": phone,
                                                                         "active": active, "updated_at": ts}))

    def lookup_ae(self, email: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(ae_directory).where(ae_directory.c.email == email.strip().lower())).first()
        return dict(r._mapping) if r else None

    def add_ae(self, email: str, name: str, phone: str, active: bool = True) -> bool:
        stmt = (pg_insert(ae_directory).values(email=email.strip().lower(), name=name, phone=phone, active=active,
                                               updated_at=now())
                .on_conflict_do_nothing(index_elements=["email"]).returning(ae_directory.c.email))
        with self.engine.begin() as c:
            return c.execute(stmt).first() is not None

    def list_aes(self) -> list[dict]:
        with self.engine.connect() as c:
            return _rows(c.execute(select(ae_directory).order_by(func.lower(ae_directory.c.name), ae_directory.c.email)))

    def update_ae(self, email: str, **fields: Any) -> dict | None:
        if unknown := set(fields) - AE_FIELDS:
            raise ValueError(f"unknown AE fields: {unknown}")
        stmt = (ae_directory.update().where(ae_directory.c.email == email.strip().lower())
                .values(**fields, updated_at=now()).returning(*ae_directory.c))
        with self.engine.begin() as c:
            r = c.execute(stmt).first()
        return dict(r._mapping) if r else None

