"""FastAPI app: Gmail + Retell webhooks (ack fast, work in background), run/escalation API for the dashboard."""

import json
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import Settings
from app.container import Container
from app.providers.real.gmail import DEAL_SUBJECT
from app.providers.real.retell import map_call_analyzed, verify_signature
from app.services.audit import audit
from app.tools.registry import PermanentToolError

log = logging.getLogger("onboarding")
CURSOR = "gmail_history_id"


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
            if cursor is None:
                k.store.set_kv(CURSOR, k.inbox.current_history_id())
                return
            try:
                # Deal-like subject OR any mail from a known AE (AEs don't always write "Closed Won").
                emails, new_cursor = k.inbox.new_deal_emails(
                    cursor, lambda e: bool(DEAL_SUBJECT.search(e.subject)) or k.store.lookup_ae(e.sender) is not None)
            except PermanentToolError as e:
                if "history_too_old" in str(e):
                    log.warning("gmail history cursor too old; resetting to now")
                    k.store.set_kv(CURSOR, k.inbox.current_history_id())
                    return
                raise
            for e in emails:
                run_id, _ = k.runner.start(e.model_dump())
                log.info("deal email %s -> run %s", e.message_id, run_id)
            k.store.set_kv(CURSOR, new_cursor)

    def poll_loop(seconds: int) -> None:
        while not stop.wait(seconds):
            try:
                sync_inbox()
            except Exception:  # noqa: BLE001 - keep polling; errors are logged
                log.exception("inbox poll failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.container is None:
            from app.container import build_container

            app.state.container = build_container(Settings())
        k = app.state.container
        if k.start_poller and k.inbox is not None:
            threading.Thread(target=poll_loop, args=(20,), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="NovaCRM onboarding agent", lifespan=lifespan)
    app.state.container = c
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
        if not verify_signature(raw, k.settings.retell_api_key, request.headers.get("x-retell-signature")):
            raise HTTPException(401, "bad signature")
        payload = json.loads(raw)
        if payload.get("event") != "call_analyzed":
            return Response(status_code=204)
        call_id = payload["call"]["call_id"]
        dedupe = f"webhook:call:{call_id}:call_analyzed"
        if k.store.get_idem(dedupe) is not None:
            return Response(status_code=204)
        k.store.put_idem(dedupe, {"received": True})
        bg.add_task(resume_call, payload)
        return Response(status_code=204)

    def resume_call(payload: dict) -> None:
        k = container()
        run_id, result = map_call_analyzed(payload)
        with lock:
            pending = k.runner.pending(run_id)
            if not pending or pending.get("type") != "call" or pending.get("provider_call_id") != result.provider_call_id:
                audit(k.store, run_id, "root", "voice_webhook",
                      rationale=f"ignored result for {result.provider_call_id}: run is not waiting on that call",
                      status="ignored")
                return
            audit(k.store, run_id, "root", "voice_webhook", rationale="call result received; resuming",
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

    @app.post("/admin/gmail/watch")
    def gmail_watch():
        k = container()
        if k.inbox is None:
            raise HTTPException(400, "gmail not configured (PROVIDER_MODE=fake)")
        return k.inbox.watch(k.settings.gmail_pubsub_topic)

    return app


app = create_app()
