"""Owns graph execution for a run: Gmail dedupe, resume, and terminal FAILED on unexpected errors.

Used by the FastAPI webhooks and by the simulation harness, so both exercise the same code.
"""

import uuid
from typing import Any

from langgraph.types import Command

from app.graph.root import validate_resolution
from app.services.audit import audit
from app.services.store import Store

# Statuses a run only holds while a node is executing (every interrupt or end sets another status first).
MID_NODE = {"RECEIVED", "TIER_CONFIRMED", "PROJECT_CREATED"}
# Waiting statuses and the interrupt type the checkpoint must show for them.
AWAITING = {"AWAITING_CALL": "call", "AWAITING_RETRY": "wait"}
STUCK = "stuck"  # escalation key infix for runs whose worker died mid-node (raised by the scheduler)


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

    def resume_wait(self, run_id: str) -> dict:
        return self.invoke(run_id, Command(resume={"type": "wake"}))

    def escalate_stuck(self, run_id: str, reason: str) -> None:
        """Outside the graph (it is not at an interrupt): open a system escalation; resolve() handles it."""
        n = sum(1 for e in self.store.list_escalations() if e["run_id"] == run_id and f":{STUCK}:" in e["key"])
        self.store.upsert_escalation(f"{run_id}:{STUCK}:{n + 1}", dict(
            run_id=run_id, reason=reason, kind="system", agent="root", status="open"))
        self.store.upsert_run(run_id, status="ESCALATED")
        audit(self.store, run_id, "root", "escalate", rationale=reason, status="escalated")

    def _open_stuck(self, run_id: str) -> dict | None:
        return next((e for e in self.store.list_escalations("open")
                     if e["run_id"] == run_id and f":{STUCK}:" in e["key"]), None)

    def resolve(self, run_id: str, resolution: dict) -> dict:
        if esc := self._open_stuck(run_id):
            return self._resolve_stuck(run_id, esc, resolution)
        state = self.graph.get_state(self.cfg(run_id)).values
        pending = self.pending(run_id)
        if not pending or pending.get("type") != "escalation":
            raise ValueError("run is not waiting for a human resolution")
        validate_resolution(resolution, state)
        return self.invoke(run_id, Command(resume=resolution))

    def _resolve_stuck(self, run_id: str, esc: dict, resolution: dict) -> dict:
        validate_resolution(resolution, {"escalation_kind": "system"})
        self.store.upsert_escalation(esc["key"], dict(status="resolved", resolved_by=resolution["by"],
                                                      resolution=resolution))
        audit(self.store, run_id, "root", "resolve", input=resolution,
              rationale=f"human resolution by {resolution['by']}: {resolution['action']} (stuck run)")
        if resolution["action"] == "cancel":
            self.store.upsert_run(run_id, status="CANCELLED")
            return {}
        # Resume the interrupted node from its checkpoint; the registry's in-flight keys make that safe.
        values = self.graph.get_state(self.cfg(run_id)).values
        self.store.upsert_run(run_id, status=values.get("status") or "RECEIVED")
        return self.invoke(run_id, None)

    def invoke(self, run_id: str, payload: Any) -> dict:
        try:
            return self.graph.invoke(payload, self.cfg(run_id))
        except Exception as e:
            self.store.upsert_run(run_id, status="FAILED", error=f"{type(e).__name__}: {e}")
            audit(self.store, run_id, "root", "failed", rationale=f"unhandled {type(e).__name__}: {e}",
                  status="failed")
            raise
