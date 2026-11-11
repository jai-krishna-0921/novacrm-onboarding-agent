"""FastAPI app: Gmail + Retell webhooks (ack fast, work in background), run/escalation API for the dashboard."""

import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import Settings
from app.container import Container
from app.guardrails.decision import CallResult
from app.providers.real.gmail import DEAL_SUBJECT
from app.providers.real.retell import map_call_analyzed, verify_signature
from app.services.audit import audit
from app.services.migration_check import MigrationDataUnavailable, run_migration_check
from app.tools.registry import PermanentToolError

log = logging.getLogger("onboarding")
CURSOR = "gmail_history_id"
RECONCILE_AFTER = 180       # seconds without a webhook before we ask Retell directly
CALL_RESULT_TIMEOUT = 900   # seconds after which a call with no usable result counts as a failed attempt


class Dismissal(BaseModel):
    """A CSM accepting a migration-check failure (e.g. a known junk row). Both fields are required and audited."""
    reason: str = Field(min_length=1, pattern=r"\S")
    by: str = Field(min_length=1, pattern=r"\S")


class Resolution(BaseModel):
    action: str
    by: str
    tier: str | None = None
    note: str | None = None


def create_app(c: Container | None = None) -> FastAPI:
    lock = threading.Lock()  # one graph step at a time: closes the concurrent-duplicate-project race for the demo
    stop = threading.Event()

    def container() -> Container:
        return app.state.container

    # ---------- inbox sync (shared by Pub/Sub push and the polling fallback) ----------
    def sync_inbox() -> None:
        k = container()
        if k.inbox is None:
            return
        with lock:
            cursor = k.store.get_kv(CURSOR)

            def relevant(e) -> bool:
                # Deal-like subject OR any mail from a known AE (AEs don't always write "Closed Won").
                return bool(DEAL_SUBJECT.search(e.subject)) or k.store.lookup_ae(e.sender) is not None

            if cursor is None:
                k.store.set_kv(CURSOR, k.inbox.current_history_id())
                return
            try:
                emails, new_cursor = k.inbox.new_deal_emails(cursor, relevant)
            except PermanentToolError as e:
                if "history_too_old" not in str(e):
                    raise
                # Cursor expired: full-sync the recent inbox (runs dedupe by message id), then reset the cursor.
                log.warning("gmail history cursor too old; falling back to a 2-day full sync")
                new_cursor = k.inbox.current_history_id()
                emails = k.inbox.recent_deal_emails(relevant)
            for e in emails:
                run_id, _ = k.runner.start(e.model_dump())
                log.info("deal email %s -> run %s", e.message_id, run_id)
            k.store.set_kv(CURSOR, new_cursor)

    def tick(now: float | None = None) -> None:
        """Scheduler: resume due retry waits; reconcile calls whose webhook never arrived.

        Each run is isolated (one failure never blocks the others) and Retell is polled OUTSIDE the lock;
        results are applied under the lock through the same pending-call guard as the webhook."""
        k, now = container(), now if now is not None else time.time()
        stale: list[tuple[str, str, float]] = []
        with lock:
            for run in k.store.list_runs(500):
                try:
                    status = run.get("status")
                    if status == "AWAITING_RETRY":
                        p = k.runner.pending(run["id"])
                        if p and p.get("type") == "wait" and p["until"] <= now:
                            k.runner.resume_wait(run["id"])
                    elif status == "AWAITING_CALL" and k.voice is not None:
                        age = now - datetime.fromisoformat(run["updated_at"]).timestamp()
                        p = k.runner.pending(run["id"])
                        if age >= RECONCILE_AFTER and p and p.get("type") == "call":
                            stale.append((run["id"], p["provider_call_id"], age))
                except Exception:  # noqa: BLE001
                    log.exception("scheduler: run %s failed", run.get("id"))
        for run_id, call_id, age in stale:
            try:
                reconcile(run_id, call_id, age)
            except Exception:  # noqa: BLE001
                log.exception("scheduler: reconcile %s failed", run_id)

    def reconcile(run_id: str, call_id: str, age: float) -> None:
        k = container()
        try:
            call, err = k.voice.get_call(call_id), None
        except Exception as e:  # noqa: BLE001 - provider down: fall through to the timeout ceiling
            call, err = None, e
        state = (call or {}).get("call_status")
        finished = call and (state in ("not_connected", "error") or (state == "ended" and call.get("call_analysis")))
        if finished:
            call = {**call, "metadata": {**(call.get("metadata") or {}), "run_id": run_id}}
            _, result = map_call_analyzed({"event": "call_analyzed", "call": call})
            why = "webhook never arrived; result fetched from Retell"
        elif age >= CALL_RESULT_TIMEOUT:
            result = CallResult(provider_call_id=call_id, status="failed")
            why = (f"no usable result {int(age)}s after the call was placed (retell: "
                   f"{state or (type(err).__name__ if err else 'call not found')}); counting the attempt as failed")
        else:
            return
        with lock:
            _apply_call_result(run_id, result, "voice_reconcile", why)

    def poll_loop(seconds: int) -> None:
        while not stop.wait(seconds):
            try:
                sync_inbox()
            except Exception:  # noqa: BLE001 - keep polling; errors are logged
                log.exception("inbox poll failed")

    def tick_loop(seconds: int) -> None:
        while not stop.wait(seconds):
            try:
                tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.container is None:
            from app.container import build_container

            app.state.container = build_container(Settings())
        k = app.state.container
        if k.start_poller and k.inbox is not None:
            threading.Thread(target=poll_loop, args=(20,), daemon=True).start()
        if k.start_poller:
            threading.Thread(target=tick_loop, args=(5,), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="NovaCRM onboarding agent", lifespan=lifespan)
    app.state.container = c
    app.state.tick = tick
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"],
                       allow_headers=["*"])

    # ---------- webhooks ----------
    @app.post("/webhooks/gmail")
    def gmail_push(token: str, bg: BackgroundTasks):
        if token != container().settings.gmail_push_token or not token:
            raise HTTPException(401, "bad push token")
        bg.add_task(sync_inbox)
        return {"ok": True}

    @app.post("/webhooks/voice", status_code=204)
    async def voice_webhook(request: Request, bg: BackgroundTasks):
        k = container()
        raw = (await request.body()).decode()
        key = k.settings.retell_api_key
        if not key or not verify_signature(raw, key, request.headers.get("x-retell-signature")):
            raise HTTPException(401, "bad signature")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
            if payload.get("event") != "call_analyzed":
                return Response(status_code=204)
            map_call_analyzed(payload)  # validates call_id, metadata.run_id and field types
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise HTTPException(400, f"malformed webhook: {e}") from e
        # No early dedupe marker: resume_call only acts if the run is waiting on exactly this call, so a
        # redelivery after a failed resume still works and a duplicate after success is ignored.
        bg.add_task(resume_call, payload)
        return Response(status_code=204)

    def resume_call(payload: dict) -> None:
        with lock:
            _resume_call_locked(payload)

    def _resume_call_locked(payload: dict, step: str = "voice_webhook") -> None:
        run_id, result = map_call_analyzed(payload)
        _apply_call_result(run_id, result, step, "call result received; resuming")

    def _apply_call_result(run_id: str, result: CallResult, step: str, why: str) -> None:
        k = container()
        pending = k.runner.pending(run_id)
        if not pending or pending.get("type") != "call" or pending.get("provider_call_id") != result.provider_call_id:
            audit(k.store, run_id, "root", step,
                  rationale=f"ignored result for {result.provider_call_id}: run is not waiting on that call",
                  status="ignored")
            return
        audit(k.store, run_id, "root", step, rationale=why,
              output=result.model_dump(exclude={"transcript"}))
        k.runner.resume_call(run_id, result.model_dump())

    # ---------- dashboard API ----------
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "mode": container().settings.provider_mode}

    @app.get("/runs")
    def list_runs():
        return container().store.list_runs()

    @app.get("/runs/{run_id}")
    def run_detail(run_id: str):
        k = container()
        run = k.store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        llm = k.store.list_llm_calls(run_id)
        return {"run": run, "pending": k.runner.pending(run_id), "audit": k.store.list_audit(run_id),
                "tool_calls": k.store.list_tool_calls(run_id),
                "migration_checks": k.store.list_migration_checks(run_id),
                "llm": {"calls": len(llm), "input_tokens": sum(r["input_tokens"] for r in llm),
                        "output_tokens": sum(r["output_tokens"] for r in llm),
                        "cost_usd": round(sum(float(r["cost_usd"]) for r in llm), 6), "by_node": llm}}

    @app.get("/escalations")
    def list_escalations(status: str | None = None):
        return container().store.list_escalations(status)

    @app.post("/runs/{run_id}/resolve")
    def resolve(run_id: str, body: Resolution):
        k = container()
        with lock:
            try:
                k.runner.resolve(run_id, body.model_dump(exclude_none=True))
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        return {"ok": True, "status": k.store.get_run(run_id)["status"]}

    @app.post("/runs/{run_id}/migration-check")
    def migration_check(run_id: str):
        k = container()
        run = k.store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        if not run.get("rocketlane_project_id"):
            raise HTTPException(404, "run has no Rocketlane project yet")
        with lock:
            try:
                return run_migration_check(k.store, k.registry, k.migration_contacts, run_id)
            except MigrationDataUnavailable as e:
                raise HTTPException(503, f"migration databases unreachable: {e}") from e

    @app.post("/runs/{run_id}/migration-escalation/dismiss")
    def dismiss_migration_escalation(run_id: str, body: Dismissal):
        """Close the open migration escalation without a passing re-run. Never touches the Rocketlane task:
        the evidence note stays, and a person still decides whether to tick "Migration verified"."""
        k = container()
        with lock:
            open_esc = [e for e in k.store.list_escalations("open")
                        if e["run_id"] == run_id and e.get("kind") == "migration"]
            if not open_esc:
                raise HTTPException(404, "no open migration escalation for this run")
            for e in open_esc:
                k.store.upsert_escalation(e["key"], dict(status="dismissed", resolved_by=body.by,
                                                         resolution={"action": "dismiss", "note": body.reason}))
            audit(k.store, run_id, "migration", "dismiss_migration_escalation",
                  input={"by": body.by}, rationale=f"dismissed by {body.by}: {body.reason}")
        return {"ok": True, "dismissed": [e["key"] for e in open_esc]}

    @app.post("/admin/gmail/watch")
    def gmail_watch():
        k = container()
        if k.inbox is None:
            raise HTTPException(400, "gmail not configured (PROVIDER_MODE=fake)")
        return k.inbox.watch(k.settings.gmail_pubsub_topic)

    return app


app = create_app()
