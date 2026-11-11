"""Persistence for runs, audit, tool/LLM calls, idempotency and escalations.

MemoryStore backs tests and fake-mode simulations; PgStore (app/db) implements the same methods.
"""

from datetime import UTC, datetime
from typing import Any, Protocol


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store(Protocol):
    def get_idem(self, key: str) -> dict | None: ...
    def put_idem(self, key: str, value: dict) -> None: ...
    def add_audit(self, row: dict) -> None: ...
    def add_tool_call(self, row: dict) -> None: ...
    def add_llm_call(self, row: dict) -> None: ...
    def upsert_run(self, run_id: str, **fields: Any) -> None: ...
    def get_run(self, run_id: str) -> dict | None: ...
    def find_run_by_message(self, gmail_message_id: str) -> dict | None: ...
    def find_active_run(self, *, opportunity_id: str | None, customer_key: str,
                        exclude_run_id: str) -> dict | None: ...
    def upsert_escalation(self, key: str, row: dict) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self.idem: dict[str, dict] = {}
        self.audit_rows: list[dict] = []
        self.tool_calls: list[dict] = []
        self.llm_calls: list[dict] = []
        self.runs: dict[str, dict] = {}
        self.escalations: dict[str, dict] = {}

    def get_idem(self, key: str) -> dict | None:
        return self.idem.get(key)

    def put_idem(self, key: str, value: dict) -> None:
        self.idem[key] = value

    def add_audit(self, row: dict) -> None:
        self.audit_rows.append(row)

    def add_tool_call(self, row: dict) -> None:
        self.tool_calls.append(row)

    def add_llm_call(self, row: dict) -> None:
        self.llm_calls.append(row)

    def upsert_run(self, run_id: str, **fields: Any) -> None:
        self.runs.setdefault(run_id, {"id": run_id, "created_at": now()}).update(fields, updated_at=now())

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
