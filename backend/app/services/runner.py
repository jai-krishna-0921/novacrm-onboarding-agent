"""Owns graph execution for a run: Gmail dedupe, resume, and terminal FAILED on unexpected errors.

Used by the FastAPI webhooks and by the simulation harness, so both exercise the same code.
"""

import uuid
from typing import Any

from langgraph.types import Command

from app.graph.root import validate_resolution
from app.services.audit import audit
from app.services.store import Store


class Runner:
    def __init__(self, graph, store: Store):
        self.graph, self.store = graph, store

    @staticmethod
    def cfg(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def start(self, email: dict) -> tuple[str, dict | None]:
        """Returns (run_id, output). Output is None when this Gmail message was already processed."""
        if existing := self.store.find_run_by_message(email["message_id"]):
            audit(self.store, existing["id"], "root", "dedupe",
                  rationale=f"Gmail message {email['message_id']} already processed; ignored", status="ignored")
            return existing["id"], None
        run_id = str(uuid.uuid4())
        self.store.upsert_run(run_id, status="RECEIVED", gmail_message_id=email["message_id"])
        return run_id, self.invoke(run_id, {"run_id": run_id, "email": email})

    def pending(self, run_id: str) -> dict | None:
        tasks = self.graph.get_state(self.cfg(run_id)).tasks
        intr = [i for t in tasks for i in t.interrupts]
        return intr[0].value if intr else None

    def resume_call(self, run_id: str, call_result: dict) -> dict:
        return self.invoke(run_id, Command(resume=call_result))

    def resolve(self, run_id: str, resolution: dict) -> dict:
        state = self.graph.get_state(self.cfg(run_id)).values
        pending = self.pending(run_id)
        if not pending or pending.get("type") != "escalation":
            raise ValueError("run is not waiting for a human resolution")
        validate_resolution(resolution, state)
        return self.invoke(run_id, Command(resume=resolution))

    def invoke(self, run_id: str, payload: Any) -> dict:
        try:
            return self.graph.invoke(payload, self.cfg(run_id))
        except Exception as e:
            self.store.upsert_run(run_id, status="FAILED", error=f"{type(e).__name__}: {e}")
            audit(self.store, run_id, "root", "failed", rationale=f"unhandled {type(e).__name__}: {e}",
                  status="failed")
            raise
