"""Persistence for runs, audit, tool/LLM calls, idempotency and escalations.

MemoryStore backs tests and fake-mode simulations; PgStore (app/db) implements the same methods.
"""

from datetime import UTC, datetime
from typing import Any, Protocol

RUN_FIELDS = {"status", "gmail_message_id", "opportunity_id", "customer_key", "customer_name", "plan_tier",
              "rocketlane_project_id", "slack_channel_id", "error"}
# Past validation, no project yet, still progressing: a second run for the same deal must not phone the AE again.
IN_FLIGHT_STATUSES = {"AWAITING_CALL", "AWAITING_RETRY", "TIER_CONFIRMED", "PROJECT_CREATED"}
DEAD_STATUSES = {"FAILED", "CANCELLED"}
# Agent 3 (project tracking): latest task snapshot plus the handoff record, one row per run.
TRACKING_FIELDS = {"tasks", "synced_at", "handoff_summary", "handed_off_at", "handoff_slack"}


def now() -> str:
    return datetime.now(UTC).isoformat()


# ---------- observability helpers (shared so MemoryStore and PgStore answer identically) ----------
LATENCY_EDGES_MS = (1000, 2000, 4000, 8000)  # buckets: <1s, 1-2s, 2-4s, 4-8s, 8s+
LLM_ROW_FIELDS = ("id", "run_id", "customer_name", "ts", "agent", "node", "model", "prompt_version", "input_tokens",
                  "output_tokens", "latency_ms", "cost_usd", "stop_reason")
# List rows never carry args/result (they can hold customer data); GET /tool-calls/{id} does.
TOOL_ROW_FIELDS = ("id", "run_id", "customer_name", "ts", "agent", "tool", "status", "attempt", "latency_ms",
                   "idempotency_key")
CREATE_PROJECT_TOOL, CALL_TOOL = "rocketlane_create_project", "voice_start_call"


def percentile(values: list, p: float) -> float | None:
    """Postgres percentile_cont semantics (linear interpolation between closest ranks)."""
    if not values:
        return None
    s = sorted(values)
    pos = p * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def ms(v: Any) -> int | None:
    return None if v is None else int(float(v) + 0.5)


def latency_bucket(v: int) -> int:
    return next((i for i, edge in enumerate(LATENCY_EDGES_MS) if v < edge), len(LATENCY_EDGES_MS))


def llm_group(calls: int, tin: Any, tout: Any, cost: Any, avg: Any, p50: Any, p95: Any) -> dict:
    return dict(calls=int(calls), input_tokens=int(tin or 0), output_tokens=int(tout or 0),
                cost_usd=round(float(cost or 0), 6), avg_latency_ms=ms(avg), p50_latency_ms=ms(p50),
                p95_latency_ms=ms(p95))


def _llm_group_of(rows: list[dict]) -> dict:
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    return llm_group(len(rows), sum(r.get("input_tokens") or 0 for r in rows),
                     sum(r.get("output_tokens") or 0 for r in rows), sum(float(r.get("cost_usd") or 0) for r in rows),
                     sum(lat) / len(lat) if lat else None, percentile(lat, .5), percentile(lat, .95))


def by_calls(groups: list[dict], *keys: str) -> list[dict]:
    return sorted(groups, key=lambda g: (-g["calls"], *(str(g[k] or "") for k in keys)))


def tool_stat(tool: str, calls: int, ok: int, failed: int, replayed: int, blocked: int, avg: Any, p95: Any) -> dict:
    return dict(tool=tool, calls=int(calls), ok=int(ok), failed=int(failed), replayed=int(replayed),
                blocked=int(blocked), failure_rate=round(failed / calls, 4) if calls else 0.0,
                avg_latency_ms=ms(avg), p95_latency_ms=ms(p95))


def merge_tool_stats(rows: list[dict], audit_counts: dict[tuple[str, str], int]) -> list[dict]:
    """rows: per-tool aggregates from tool_calls; audit_counts: (tool, status) -> n for replayed/blocked audit rows
    (those never reach tool_calls: a replay returns the stored result, a block never runs the tool)."""
    by = {r["tool"]: r for r in rows}
    for (tool, _), _n in audit_counts.items():
        by.setdefault(tool, dict(tool=tool, calls=0, ok=0, failed=0, avg=None, p95=None))
    out = [tool_stat(t, r["calls"], r["ok"], r["failed"], audit_counts.get((t, "replayed"), 0),
                     audit_counts.get((t, "blocked"), 0), r["avg"], r["p95"]) for t, r in by.items()]
    return by_calls(out, "tool")


def email_to_project(pairs: list[tuple[str, str]]) -> dict:
    """pairs: (run created_at, first successful create-project ts) -> average seconds over runs with a project."""
    secs = []
    for created, made in pairs:
        try:
            secs.append((datetime.fromisoformat(made) - datetime.fromisoformat(created)).total_seconds())
        except (TypeError, ValueError):
            continue
    return {"runs": len(secs), "avg_seconds": round(sum(secs) / len(secs), 1) if secs else None}


IN_FLIGHT = "__in_flight__"  # idempotency value while the side effect is being attempted (holds the start time)


def _reservation(value: dict | None) -> dict | None:
    """reserve_idem's answer for an existing value: in-flight marker or completed result."""
    if value is None:
        return None
    return {"started_at": value[IN_FLIGHT]} if IN_FLIGHT in value else {"result": value}


class Store(Protocol):
    def get_idem(self, key: str) -> dict | None: ...
    def put_idem(self, key: str, value: dict) -> None: ...
    def reserve_idem(self, key: str) -> dict | None: ...
    def release_idem(self, key: str) -> None: ...
    def peek_idem(self, key: str) -> dict | None: ...
    def add_audit(self, row: dict) -> None: ...
    def add_tool_call(self, row: dict) -> None: ...
    def add_llm_call(self, row: dict) -> None: ...
    def upsert_run(self, run_id: str, **fields: Any) -> None: ...
    def get_run(self, run_id: str) -> dict | None: ...
    def find_run_by_message(self, gmail_message_id: str) -> dict | None: ...
    def find_active_run(self, *, opportunity_id: str | None, customer_key: str,
                        exclude_run_id: str) -> dict | None: ...
    def list_runs_by_status(self, statuses: set[str]) -> list[dict]: ...
    def list_trackable_runs(self, exclude_statuses: set[str]) -> list[dict]: ...
    def find_run_by_channel(self, channel_id: str) -> dict | None: ...
    def upsert_escalation(self, key: str, row: dict) -> None: ...
    def list_escalations(self, status: str | None = None) -> list[dict]: ...
    def list_runs(self, limit: int = 100) -> list[dict]: ...
    def list_audit(self, run_id: str) -> list[dict]: ...
    def list_tool_calls(self, run_id: str) -> list[dict]: ...
    def list_llm_calls(self, run_id: str) -> list[dict]: ...
    def get_kv(self, key: str) -> str | None: ...
    def set_kv(self, key: str, value: str) -> None: ...
    def upsert_ae(self, email: str, name: str, phone: str, active: bool = True) -> None: ...
    def lookup_ae(self, email: str) -> dict | None: ...
    def add_migration_check(self, row: dict) -> None: ...
    def list_migration_checks(self, run_id: str) -> list[dict]: ...
    def get_tracking(self, run_id: str) -> dict | None: ...
    def upsert_tracking(self, run_id: str, **fields: Any) -> None: ...
    # observability (newest first, `since` is an ISO timestamp compared against the stored ts)
    def page_llm_calls(self, *, limit: int, offset: int, model: str | None = None, node: str | None = None,
                       run_id: str | None = None, since: str | None = None) -> dict: ...
    def page_tool_calls(self, *, limit: int, offset: int, tool: str | None = None, agent: str | None = None,
                        status: str | None = None, run_id: str | None = None, since: str | None = None) -> dict: ...
    def get_tool_call(self, call_id: int) -> dict | None: ...
    def llm_stats(self, since: str | None = None) -> dict: ...
    def tool_stats(self, since: str | None = None) -> list[dict]: ...
    def overview_stats(self) -> dict: ...


def _no_id(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "id"}


class MemoryStore:
    def __init__(self) -> None:
        self.idem: dict[str, dict] = {}
        self.audit_rows: list[dict] = []
        self.tool_calls: list[dict] = []
        self.llm_calls: list[dict] = []
        self.runs: dict[str, dict] = {}
        self.escalations: dict[str, dict] = {}
        self.kv: dict[str, str] = {}
        self.aes: dict[str, dict] = {}
        self.migration_checks: list[dict] = []
        self.tracking: dict[str, dict] = {}
        self._seq = 0

    def get_idem(self, key: str) -> dict | None:
        v = self.idem.get(key)
        return None if v is None or IN_FLIGHT in v else v

    def put_idem(self, key: str, value: dict) -> None:
        # First completed write wins (same as PgStore); a completion replaces an in-flight reservation.
        if key not in self.idem or IN_FLIGHT in self.idem[key]:
            self.idem[key] = value

    def reserve_idem(self, key: str) -> dict | None:
        """Atomically mark `key` in flight. None = reserved now; else {'started_at'} or {'result'} of the holder."""
        if key in self.idem:
            return _reservation(self.idem[key])
        self.idem[key] = {IN_FLIGHT: now()}
        return None

    def peek_idem(self, key: str) -> dict | None:
        """Like reserve_idem's answer, without reserving."""
        return _reservation(self.idem.get(key))

    def release_idem(self, key: str) -> None:
        """Drop an in-flight reservation (the side effect certainly did not happen). Never drops a result."""
        if IN_FLIGHT in self.idem.get(key, {}):
            del self.idem[key]

    def add_audit(self, row: dict) -> None:
        self.audit_rows.append(row)

    def add_tool_call(self, row: dict) -> None:
        self.tool_calls.append({**row, "id": len(self.tool_calls) + 1})

    def add_llm_call(self, row: dict) -> None:
        self.llm_calls.append({**row, "id": len(self.llm_calls) + 1})

    def upsert_run(self, run_id: str, **fields: Any) -> None:
        if unknown := set(fields) - RUN_FIELDS:
            raise ValueError(f"unknown run fields: {unknown}")
        if run_id not in self.runs:
            self._seq += 1
            # Every column present (None when unset), same row shape as PgStore.
            self.runs[run_id] = {"id": run_id, "created_at": now(), "seq": self._seq, **dict.fromkeys(RUN_FIELDS)}
        self.runs[run_id].update(fields, updated_at=now())

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    def find_run_by_message(self, gmail_message_id: str) -> dict | None:
        return next((r for r in self.runs.values() if r.get("gmail_message_id") == gmail_message_id), None)

    def find_active_run(self, *, opportunity_id: str | None, customer_key: str,
                        exclude_run_id: str) -> dict | None:
        for r in self.runs.values():
            if r["id"] == exclude_run_id or r.get("status") in DEAD_STATUSES:
                continue
            if not r.get("rocketlane_project_id") and r.get("status") not in IN_FLIGHT_STATUSES:
                continue
            if (opportunity_id and r.get("opportunity_id") == opportunity_id) or \
                    r.get("customer_key") == customer_key:
                return r
        return None

    def list_runs_by_status(self, statuses: set[str]) -> list[dict]:
        return sorted((r for r in self.runs.values() if r.get("status") in statuses), key=lambda r: r["seq"])

    def list_trackable_runs(self, exclude_statuses: set[str]) -> list[dict]:
        """Runs with a project, not in `exclude_statuses`, not yet handed off or with the internal handoff post
        still pending (oldest first)."""
        return sorted((r for r in self.runs.values() if r.get("rocketlane_project_id")
                       and r.get("status") not in exclude_statuses
                       and (not (t := self.tracking.get(r["id"]) or {}).get("handed_off_at")
                            or t.get("handoff_slack") == "pending")), key=lambda r: r["seq"])

    def find_run_by_channel(self, channel_id: str) -> dict | None:
        return next((r for r in self.runs.values() if r.get("slack_channel_id") == channel_id), None)

    def upsert_escalation(self, key: str, row: dict) -> None:
        self.escalations.setdefault(key, {"key": key, "created_at": now()}).update(row)

    def list_escalations(self, status: str | None = None) -> list[dict]:
        return [e for e in self.escalations.values() if status is None or e.get("status") == status]

    def list_runs(self, limit: int = 100) -> list[dict]:
        return sorted(self.runs.values(), key=lambda r: r["seq"], reverse=True)[:limit]

    def list_audit(self, run_id: str) -> list[dict]:
        return [r for r in self.audit_rows if r["run_id"] == run_id]

    def list_tool_calls(self, run_id: str) -> list[dict]:
        return [_no_id(r) for r in self.tool_calls if r["run_id"] == run_id]

    def list_llm_calls(self, run_id: str) -> list[dict]:
        return [_no_id(r) for r in self.llm_calls if r["run_id"] == run_id]

    # ---------- observability ----------
    def _page(self, rows: list[dict], fields: tuple, limit: int, offset: int, since: str | None,
              **eq: str | None) -> dict:
        match = [r for r in rows if (since is None or str(r.get("ts") or "") >= since)
                 and all(v is None or r.get(k) == v for k, v in eq.items())]
        match.sort(key=lambda r: r["id"], reverse=True)
        out = []
        for r in match[offset:offset + limit]:
            row = {**r, "customer_name": (self.runs.get(r["run_id"]) or {}).get("customer_name")}
            if row.get("cost_usd") is not None:
                row["cost_usd"] = float(row["cost_usd"])
            out.append({k: row.get(k) for k in fields})
        return {"total": len(match), "rows": out}

    def page_llm_calls(self, *, limit, offset, model=None, node=None, run_id=None, since=None):
        return self._page(self.llm_calls, LLM_ROW_FIELDS, limit, offset, since, model=model, node=node,
                          run_id=run_id)

    def page_tool_calls(self, *, limit, offset, tool=None, agent=None, status=None, run_id=None, since=None):
        return self._page(self.tool_calls, TOOL_ROW_FIELDS, limit, offset, since, tool=tool, agent=agent,
                          status=status, run_id=run_id)

    def get_tool_call(self, call_id: int) -> dict | None:
        r = next((r for r in self.tool_calls if r["id"] == call_id), None)
        return {**r, "customer_name": (self.runs.get(r["run_id"]) or {}).get("customer_name")} if r else None

    def llm_stats(self, since: str | None = None) -> dict:
        rows = [r for r in self.llm_calls if since is None or str(r.get("ts") or "") >= since]

        def groups(*keys: str) -> list[dict]:
            by: dict[tuple, list[dict]] = {}
            for r in rows:
                by.setdefault(tuple(r.get(k) for k in keys), []).append(r)
            return by_calls([{**dict(zip(keys, k, strict=True)), **_llm_group_of(v)} for k, v in by.items()], *keys)

        days: dict[str, list[dict]] = {}
        for r in rows:
            days.setdefault(str(r.get("ts") or "")[:10], []).append(r)
        buckets = [0] * (len(LATENCY_EDGES_MS) + 1)
        for r in rows:
            if r.get("latency_ms") is not None:
                buckets[latency_bucket(r["latency_ms"])] += 1
        return {"totals": _llm_group_of(rows), "by_model": groups("model"), "by_node": groups("agent", "node"),
                "by_prompt": groups("prompt_version"),
                "by_day": [{"day": d, **_llm_group_of(v)} for d, v in sorted(days.items())],
                "latency_buckets": buckets}

    def tool_stats(self, since: str | None = None) -> list[dict]:
        def fresh(r: dict) -> bool:
            return since is None or str(r.get("ts") or "") >= since

        by: dict[str, list[dict]] = {}
        for r in self.tool_calls:
            if fresh(r):
                by.setdefault(r["tool"], []).append(r)
        rows = []
        for tool, v in by.items():
            lat = [r["latency_ms"] for r in v if r.get("latency_ms") is not None]
            rows.append(dict(tool=tool, calls=len(v), ok=sum(r["status"] == "ok" for r in v),
                             failed=sum(r["status"] == "failed" for r in v),
                             avg=sum(lat) / len(lat) if lat else None, p95=percentile(lat, .95)))
        counts: dict[tuple[str, str], int] = {}
        for a in self.audit_rows:
            step = str(a.get("step") or "")
            if step.startswith("tool:") and a.get("status") in ("replayed", "blocked") and fresh(a):
                k = (step[5:], a["status"])
                counts[k] = counts.get(k, 0) + 1
        return merge_tool_stats(rows, counts)

    def overview_stats(self) -> dict:
        by_status: dict[str, int] = {}
        days: dict[str, int] = {}
        for r in self.runs.values():
            by_status[r.get("status") or "UNKNOWN"] = by_status.get(r.get("status") or "UNKNOWN", 0) + 1
            day = str(r.get("created_at") or "")[:10]
            days[day] = days.get(day, 0) + 1
        first: dict[str, str] = {}
        for t in self.tool_calls:
            if t["tool"] == CREATE_PROJECT_TOOL and t["status"] == "ok" and t["run_id"] in self.runs:
                first[t["run_id"]] = min(first.get(t["run_id"], t["ts"]), t["ts"])
        outcomes = [a["status"] for a in self.audit_rows if a.get("step") == "decide_tier"]
        return {
            "runs_total": len(self.runs), "runs_by_status": by_status,
            "runs_by_day": [{"day": d, "runs": n} for d, n in sorted(days.items())],
            "escalations_open": sum(e.get("status") == "open" for e in self.escalations.values()),
            "email_to_project": email_to_project([(self.runs[k]["created_at"], v) for k, v in first.items()]),
            "calls": {"placed": sum(t["tool"] == CALL_TOOL and t["status"] == "ok" for t in self.tool_calls),
                      **{k: outcomes.count(k) for k in ("confirmed", "unclear", "no_answer")}},
            "llm_calls": len(self.llm_calls),
            "llm_cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in self.llm_calls), 6),
        }

    def get_kv(self, key: str) -> str | None:
        return self.kv.get(key)

    def set_kv(self, key: str, value: str) -> None:
        self.kv[key] = value

    def upsert_ae(self, email: str, name: str, phone: str, active: bool = True) -> None:
        e = email.strip().lower()
        self.aes[e] = {"email": e, "name": name, "phone": phone, "active": active}

    def lookup_ae(self, email: str) -> dict | None:
        return self.aes.get(email.strip().lower())

    def add_migration_check(self, row: dict) -> None:
        self.migration_checks.append(row)

    def list_migration_checks(self, run_id: str) -> list[dict]:
        return [r for r in self.migration_checks if r["run_id"] == run_id]

    def get_tracking(self, run_id: str) -> dict | None:
        row = self.tracking.get(run_id)
        return dict(row) if row else None

    def upsert_tracking(self, run_id: str, **fields: Any) -> None:
        if unknown := set(fields) - TRACKING_FIELDS:
            raise ValueError(f"unknown tracking fields: {unknown}")
        row = self.tracking.setdefault(run_id, {"run_id": run_id, **dict.fromkeys(TRACKING_FIELDS)})
        row.update(fields)
