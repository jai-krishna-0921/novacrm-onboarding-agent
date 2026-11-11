"""Persistence for runs, audit, tool/LLM calls, idempotency and escalations.

MemoryStore backs tests and fake-mode simulations; PgStore (app/db) implements the same methods.
"""

from datetime import UTC, datetime
from typing import Any, Protocol

RUN_FIELDS = {"status", "gmail_message_id", "opportunity_id", "customer_key", "customer_name", "plan_tier",
              "rocketlane_project_id", "slack_channel_id", "error"}
# Agent 3 (project tracking): latest task snapshot plus the handoff record, one row per run.
TRACKING_FIELDS = {"tasks", "synced_at", "handoff_summary", "handed_off_at"}


def now() -> str:
    return datetime.now(UTC).isoformat()


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
        self.tool_calls.append(row)

    def add_llm_call(self, row: dict) -> None:
        self.llm_calls.append(row)

    def upsert_run(self, run_id: str, **fields: Any) -> None:
        if unknown := set(fields) - RUN_FIELDS:
            raise ValueError(f"unknown run fields: {unknown}")
        if run_id not in self.runs:
            self._seq += 1
            self.runs[run_id] = {"id": run_id, "created_at": now(), "seq": self._seq}
        self.runs[run_id].update(fields, updated_at=now())

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    def find_run_by_message(self, gmail_message_id: str) -> dict | None:
        return next((r for r in self.runs.values() if r.get("gmail_message_id") == gmail_message_id), None)

    def find_active_run(self, *, opportunity_id: str | None, customer_key: str,
                        exclude_run_id: str) -> dict | None:
        for r in self.runs.values():
            if r["id"] == exclude_run_id or r.get("status") in {"FAILED", "CANCELLED"}:
                continue
            if not r.get("rocketlane_project_id"):
                continue
            if (opportunity_id and r.get("opportunity_id") == opportunity_id) or \
                    r.get("customer_key") == customer_key:
                return r
        return None

    def upsert_escalation(self, key: str, row: dict) -> None:
        self.escalations.setdefault(key, {"key": key, "created_at": now()}).update(row)

    def list_escalations(self, status: str | None = None) -> list[dict]:
        return [e for e in self.escalations.values() if status is None or e.get("status") == status]

    def list_runs(self, limit: int = 100) -> list[dict]:
        return sorted(self.runs.values(), key=lambda r: r["seq"], reverse=True)[:limit]

    def list_audit(self, run_id: str) -> list[dict]:
        return [r for r in self.audit_rows if r["run_id"] == run_id]

    def list_tool_calls(self, run_id: str) -> list[dict]:
        return [r for r in self.tool_calls if r["run_id"] == run_id]

    def list_llm_calls(self, run_id: str) -> list[dict]:
        return [r for r in self.llm_calls if r["run_id"] == run_id]

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
