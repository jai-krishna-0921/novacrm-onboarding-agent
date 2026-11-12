"""FastAPI app: Gmail + Retell webhooks (ack fast, work in background), run/escalation API for the dashboard."""

import base64
import hmac
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as FastPath
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth import Auth
from app.config import Settings
from app.container import Container
from app.db.seed import demo_contacts
from app.guardrails.decision import CallResult
from app.llm.client import PRICES
from app.providers.real.gmail import DEAL_SUBJECT
from app.providers.real.retell import map_call_analyzed, verify_signature
from app.services import agent_status, directory
from app.services.agent_status import HB_PUSH, HB_RL_PUSH, HB_SYNC, HB_TICK, HB_TRACKING, beat
from app.services.audit import audit, redact
from app.services.health import HEALTH_INTERVAL, INTEGRATIONS, HealthMonitor, fake_monitor, iso
from app.services.migration_check import (
    MigrationDataUnavailable,
    columns_of,
    compare_columns,
    find_problems,
    row_problems,
    run_migration_check,
)
from app.services.migration_demo import refusal, repair
from app.services.runner import AWAITING, MID_NODE
from app.services.store import LATENCY_EDGES_MS
from app.services.tracking import SKIP_STATUSES, Tracker, tracking_view
from app.tools.registry import PermanentToolError, ToolFailed

log = logging.getLogger("onboarding")
CURSOR = "gmail_history_id"
RECONCILE_AFTER = 180       # seconds without a webhook before we ask Retell directly
CALL_RESULT_TIMEOUT = 900   # seconds after which a call with no usable result counts as a failed attempt
MIGRATION_CHECK_REUSE_SECONDS = 60  # an identical re-check this soon is a double click: return the first batch
ASSET_CACHE = "public, max-age=31536000, immutable"  # Vite puts a content hash in every asset file name
NOT_BUILT = ("<!doctype html><meta charset=utf-8><title>Dashboard not built</title>"
             "<h1>Frontend not built</h1><p>Run <code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code> "
             "(or <code>make web-build</code>), or point WEB_DIST_DIR at a build.</p>")


class Dismissal(BaseModel):
    """A CSM accepting a migration-check failure (e.g. a known junk row). Both fields are required and audited."""
    reason: str = Field(min_length=1, pattern=r"\S")
    by: str = Field(min_length=1, pattern=r"\S")


def _since(raw: str | None) -> str | None:
    """ISO date/datetime -> UTC isoformat, comparable with the stored ts strings. Naive means UTC."""
    if raw is None:
        return None
    try:
        d = datetime.fromisoformat(raw)
    except ValueError as e:
        raise HTTPException(422, "since must be an ISO date or datetime") from e
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _bucket_labels() -> list[str]:
    s = [e // 1000 for e in LATENCY_EDGES_MS]
    return [f"< {s[0]} s", *(f"{a}-{b} s" for a, b in zip(s, s[1:], strict=False)), f"{s[-1]} s+"]


LIMIT = Query(25, ge=1, le=200)
OFFSET = Query(0, ge=0, le=10**9)  # bounded: a value past BIGINT would be a Postgres error (500)
ROW_ID = FastPath(ge=1, le=2**63 - 1)


class AeIn(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field(max_length=500)
    phone: str = Field(max_length=64)
    active: bool = True


class AePatch(BaseModel):
    name: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=64)
    active: bool | None = None


MIGRATION_DEMO_RUN = "migration_demo"  # synthetic run id for the demo fix/reset audit rows


RL_SETTLE_SECONDS = 2.0


def _basic_password(request: Request) -> str:
    """Password from an Authorization: Basic header (Rocketlane's webhook auth), else ""."""
    h = request.headers.get("authorization") or ""
    if not h.lower().startswith("basic "):
        return ""
    try:
        return base64.b64decode(h[6:].strip(), validate=True).decode().partition(":")[2]
    except (ValueError, UnicodeDecodeError):
        return ""

CALL_PROGRESS = {"call_started": "started", "call_ended": "ended"}  # Retell events shown on the dashboard

def _canonical(rows: list[dict]) -> tuple:
    """Columns + row multiset in a fixed order, to tell whether a demo write would change anything."""
    cols = columns_of(rows)
    return cols, sorted(tuple(str(r.get(c) or "") for c in cols) for r in rows)


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

    def tracker() -> Tracker:
        k = container()
        return Tracker(k.settings, k.store, k.registry, k.llm, k.migration_contacts)

    # ---------- inbox sync (shared by Pub/Sub push and the polling fallback) ----------
    sync_queue = threading.Lock()
    sync_queued = [False]  # a push-triggered sync is waiting for the lock: further pushes need no sync of their own

    def push_sync() -> None:
        """Pushes arriving while a sync is already queued are covered by it (it reads the cursor once it runs), so
        a burst of pushes holds at most one worker thread waiting on the lock instead of one each."""
        with sync_queue:
            if sync_queued[0]:
                return
            sync_queued[0] = True
        sync_inbox()

    def sync_inbox() -> None:
        k = container()
        with lock:
            with sync_queue:
                sync_queued[0] = False  # from here on, a new push needs a new sync
            if k.inbox is None:
                return
            cursor = k.store.get_kv(CURSOR)

            def relevant(e) -> bool:
                # Deal-like subject OR any mail from a known AE (AEs don't always write "Closed Won").
                return bool(DEAL_SUBJECT.search(e.subject)) or k.store.lookup_ae(e.sender) is not None

            if cursor is None:
                k.store.set_kv(CURSOR, k.inbox.current_history_id())
                beat(k.store, HB_SYNC)
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
            beat(k.store, HB_SYNC)

    def tick(now: float | None = None) -> None:
        """Scheduler: resume due retry waits; reconcile calls whose webhook never arrived; escalate runs whose
        worker died mid-node (nothing else would ever resume them).

        Each run is isolated (one failure never blocks the others) and Retell is polled OUTSIDE the lock;
        results are applied under the lock through the same pending-call guard as the webhook."""
        k, now = container(), now if now is not None else time.time()
        stale: list[tuple[str, str, float]] = []
        with lock:
            # By state, not the newest N: an old waiting/stuck run must never fall out of the scheduler's view.
            for run in k.store.list_runs_by_status(set(AWAITING) | MID_NODE):
                try:
                    status = run.get("status")
                    age = now - datetime.fromisoformat(run["updated_at"]).timestamp()
                    p = k.runner.pending(run["id"]) if status in AWAITING else None
                    # An AWAITING_* status written just before the worker died, with the interrupt it implies never
                    # checkpointed (pending is missing or the consumed one), is as stuck as a mid-node run.
                    orphaned = status in AWAITING and (p or {}).get("type") != AWAITING[status]
                    if (status in MID_NODE or orphaned) and age >= k.settings.stuck_run_minutes * 60:
                        # Under the lock no graph step is running in this process, so this run is not progressing.
                        k.runner.escalate_stuck(run["id"], (
                            f"Run stopped in {status} for {int(age // 60)} min (worker died mid-step?). A side "
                            "effect may be half done; retry resumes from the last checkpoint and reuses anything "
                            "already created, cancel stops the run."))
                    elif status == "AWAITING_RETRY":
                        if p and p.get("type") == "wait" and p["until"] <= now:
                            k.runner.resume_wait(run["id"])
                    elif status == "AWAITING_CALL" and k.voice is not None and age >= RECONCILE_AFTER \
                            and p and p.get("type") == "call":
                        stale.append((run["id"], p["provider_call_id"], age))
                except Exception:  # noqa: BLE001
                    log.exception("scheduler: run %s failed", run.get("id"))
        for run_id, call_id, age in stale:
            try:
                reconcile(run_id, call_id, age)
            except Exception:  # noqa: BLE001
                log.exception("scheduler: reconcile %s failed", run_id)
        if k.registry is not None and now - last_tracking[0] >= k.settings.tracking_interval_seconds:
            last_tracking[0] = now
            track_all()
            beat(k.store, HB_TRACKING, now)
        beat(k.store, HB_TICK, now)  # status page: a tick that raised above never beats, so it shows as stalled

    last_tracking = [float("-inf")]

    def track_all() -> None:
        """Agent 3: sync every live project. Lock per run (webhooks interleave); one failure never blocks others."""
        t = tracker()
        for run_id in t.due_runs():
            try:
                with lock:
                    t.sync(run_id)
            except Exception as e:  # noqa: BLE001
                log.exception("scheduler: tracking sync %s failed", run_id)
                audit(container().store, run_id, "tracking", "sync_failed", status="failed",
                      rationale=f"{type(e).__name__}: {e}")

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

    def health_loop() -> None:
        while True:
            try:
                container().health.run_once()
            except Exception:  # noqa: BLE001
                log.exception("integration health check failed")
            if stop.wait(HEALTH_INTERVAL):
                return

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
            threading.Thread(target=tick_loop, args=(agent_status.TICK_SECONDS,), daemon=True).start()
        if k.start_poller and k.health is not None and k.settings.provider_mode == "real":
            threading.Thread(target=health_loop, daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="NovaCRM onboarding agent", lifespan=lifespan)
    app.state.container = c
    app.state.tick = tick
    app.state.started_at = time.time()
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"],
                       allow_headers=["*"])
    auth = Auth(lambda: container().settings)
    app.middleware("http")(auth.guard)  # login + same-origin check on everything but webhooks/healthz/login

    # ---------- dashboard UI: the React build (web/dist), same-origin; the login guard in app/auth.py covers it ----
    def dist() -> Path:
        return Path(container().settings.web_dist_dir)

    def spa_index() -> Response:
        """Every dashboard page is the SPA's index.html (client-side routes); revalidated so new builds reach
        browsers at once. No build: a clear 503 page instead of a crash."""
        index = dist() / "index.html"
        if not index.is_file():
            return HTMLResponse(NOT_BUILT, status_code=503, headers={"Cache-Control": "no-store"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.include_router(auth.router(spa_index))

    # ---------- webhooks ----------
    @app.post("/webhooks/gmail")
    async def gmail_push(token: str, bg: BackgroundTasks):
        # async: the ack needs no worker thread, so a backlog of lock-blocked background tasks (call webhooks)
        # can't delay it past Pub/Sub's ack deadline. The sync itself is coalesced (see push_sync).
        if token != container().settings.gmail_push_token or not token:
            raise HTTPException(401, "bad push token")
        bg.add_task(beat, container().store, HB_PUSH)
        bg.add_task(push_sync)
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
            if payload.get("event") in CALL_PROGRESS:
                call = payload["call"]
                run_id, call_id = (call.get("metadata") or {}).get("run_id"), call["call_id"]
                if run_id and isinstance(run_id, str) and isinstance(call_id, str):
                    bg.add_task(record_call_progress, run_id, call_id, payload["event"],
                                str(call.get("disconnection_reason") or ""))
                return Response(status_code=204)
            if payload.get("event") != "call_analyzed":
                return Response(status_code=204)
            map_call_analyzed(payload)  # validates call_id, metadata.run_id and field types
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise HTTPException(400, f"malformed webhook: {e}") from e
        # No early dedupe marker: resume_call only acts if the run is waiting on exactly this call, so a
        # redelivery after a failed resume still works and a duplicate after success is ignored.
        bg.add_task(resume_call, payload)
        return Response(status_code=204)

    @app.post("/webhooks/rocketlane", status_code=204)
    async def rocketlane_webhook(request: Request, bg: BackgroundTasks, token: str = ""):
        """A task changed in Rocketlane: sync that project now instead of at the next poll. The payload is only a
        hint (which project); Agent 3 re-reads everything through the API, so nothing in it is trusted."""
        expected = container().settings.rocketlane_webhook_token
        if not expected or not hmac.compare_digest((token or _basic_password(request)).encode(), expected.encode()):
            raise HTTPException(401, "bad token")
        try:
            payload = json.loads(await request.body() or b"{}")
            data = payload.get("data") if isinstance(payload, dict) else None
            item = (data or {}).get("task") or (data or {}).get("phase") or (data or {}).get("project") or {}
            project = item.get("project") if isinstance(item.get("project"), dict) else item
            project_id = str(project.get("projectId") or "")
        except (ValueError, AttributeError, TypeError) as e:
            raise HTTPException(400, f"malformed webhook: {type(e).__name__}") from e
        if project_id.isdigit():
            bg.add_task(sync_project_soon, project_id)
        return Response(status_code=204)

    syncing: set[str] = set()
    syncing_lock = threading.Lock()

    def sync_project_soon(project_id: str) -> None:
        """One sync per project at a time; events arriving meanwhile are covered by it (it reads the latest)."""
        run = next((r for r in container().store.list_trackable_runs(SKIP_STATUSES)
                    if str(r.get("rocketlane_project_id")) == project_id), None)
        if not run:
            return
        with syncing_lock:
            if run["id"] in syncing:
                return
            syncing.add(run["id"])
        try:
            time.sleep(RL_SETTLE_SECONDS)  # Rocketlane's reads lag its writes by a moment
            with lock:
                tracker().sync(run["id"])
            beat(container().store, HB_RL_PUSH)
        except Exception as e:  # noqa: BLE001
            log.exception("rocketlane webhook: sync %s failed", run["id"])
            audit(container().store, run["id"], "tracking", "sync_failed", status="failed",
                  rationale=f"{type(e).__name__}: {e}")
        finally:
            with syncing_lock:
                syncing.discard(run["id"])

    def record_call_progress(run_id: str, call_id: str, event: str, reason: str) -> None:
        """Dashboard only: the AE picked up / hung up. The result itself arrives later with call_analyzed, so
        without this the page would show the call as ringing until the analysis is done. Decides nothing."""
        k = container()
        pending = k.runner.pending(run_id) or {}
        if pending.get("type") != "call" or pending.get("provider_call_id") != call_id:
            return  # not the call this run waits on (stale, or another run's): nothing to show
        audit(k.store, run_id, "root", "call_progress", status=CALL_PROGRESS[event],
              input={"call_id": call_id}, output={"disconnection_reason": reason} if reason else None,
              rationale="AE is on the call" if event == "call_started"
              else "call ended; waiting for Retell's analysis of the answer")

    def resume_call(payload: dict) -> None:
        with lock:
            _resume_call_locked(payload)

    def _resume_call_locked(payload: dict, step: str = "voice_webhook") -> None:
        run_id, result = map_call_analyzed(payload)
        _apply_call_result(run_id, result, step, "call result received; resuming")

    def _apply_call_result(run_id: str, result: CallResult, step: str, why: str) -> None:
        k = container()
        pending = k.runner.pending(run_id)
        # After a crash mid-node the checkpoint can still show the consumed call interrupt; the run status (set
        # by the graph once the result is processed) is the second witness that the run still waits for it.
        waiting = (k.store.get_run(run_id) or {}).get("status") == "AWAITING_CALL"
        if not waiting or not pending or pending.get("type") != "call" \
                or pending.get("provider_call_id") != result.provider_call_id:
            audit(k.store, run_id, "root", step,
                  rationale=f"ignored result for {result.provider_call_id}: run is not waiting on that call",
                  status="ignored")
            return
        audit(k.store, run_id, "root", step, rationale=why,
              output=result.model_dump(exclude={"transcript"}))
        k.runner.resume_call(run_id, result.model_dump())

    # ---------- dashboard API ----------
    @app.get("/healthz")
    async def healthz():  # async: answered on the event loop, never queued behind busy worker threads
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
                "tracking": tracking_view(k.store, run_id),
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
                return run_migration_check(k.store, k.registry, k.migration_contacts, run_id,
                                           reuse_within=MIGRATION_CHECK_REUSE_SECONDS)
            except MigrationDataUnavailable as e:
                raise HTTPException(503, f"migration databases unreachable: {e}") from e

    @app.post("/runs/{run_id}/sync")
    def sync_run(run_id: str):
        """Agent 3 on demand: same sync the scheduler runs every TRACKING_INTERVAL_SECONDS."""
        k = container()
        run = k.store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        if not run.get("rocketlane_project_id"):
            raise HTTPException(404, "run has no Rocketlane project yet")
        with lock:
            try:
                return tracker().sync(run_id)
            except ToolFailed as e:
                raise HTTPException(502, f"Rocketlane unavailable: {e}") from e

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

    # ---------- observability (sidebar pages; aggregation runs in the store) ----------
    @app.get("/metrics/overview")
    def metrics_overview():
        return container().store.overview_stats()

    @app.get("/metrics/llm")
    def metrics_llm(since: str | None = None, days: int = Query(14, ge=1, le=90)):
        stats = container().store.llm_stats(_since(since))
        today = datetime.now(UTC).date()
        by_day = {d["day"]: d for d in stats["by_day"]}
        empty = {k: (0 if k.startswith(("calls", "input", "output", "cost")) else None)
                 for k in stats["totals"]}
        series = [{**empty, **by_day.get(day, {}), "day": day}
                  for day in ((today - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1))]
        return {**stats, "series": series, "latency_bucket_labels": _bucket_labels(),
                "prices_usd_per_mtok": PRICES, "prices_estimated": True}

    @app.get("/llm-calls")
    def llm_calls_page(limit: int = LIMIT, offset: int = OFFSET, model: str | None = None,
                       node: str | None = None, run_id: str | None = None, since: str | None = None):
        page = container().store.page_llm_calls(limit=limit, offset=offset, model=model, node=node, run_id=run_id,
                                                since=_since(since))
        return {"total": page["total"], "limit": limit, "offset": offset, "rows": page["rows"]}

    @app.get("/metrics/tools")
    def metrics_tools(since: str | None = None):
        return {"tools": container().store.tool_stats(_since(since))}

    @app.get("/tool-calls")
    def tool_calls_page(limit: int = LIMIT, offset: int = OFFSET, tool: str | None = None,
                        agent: str | None = None, status: str | None = None, run_id: str | None = None,
                        since: str | None = None):
        page = container().store.page_tool_calls(limit=limit, offset=offset, tool=tool, agent=agent, status=status,
                                                 run_id=run_id, since=_since(since))
        return {"total": page["total"], "limit": limit, "offset": offset, "rows": page["rows"]}

    @app.get("/tool-calls/{call_id}")
    def tool_call_detail(call_id: int = ROW_ID):
        row = container().store.get_tool_call(call_id)
        if not row:
            raise HTTPException(404, "tool call not found")
        return redact(row)  # payloads were redacted when written; mask again in case a writer skipped it

    @app.post("/admin/gmail/watch")
    def gmail_watch():
        k = container()
        if k.inbox is None:
            raise HTTPException(400, "gmail not configured (PROVIDER_MODE=fake)")
        out = k.inbox.watch(k.settings.gmail_pubsub_topic)
        if exp := (out or {}).get("expiration"):  # epoch milliseconds (string)
            k.store.set_kv(agent_status.GMAIL_WATCH, iso(int(exp) / 1000))
        return out

    # ---------- agent status page (reads heartbeats, audit rows and cached health; never calls out) ----------
    fake_health: list[HealthMonitor] = []

    def integrations() -> list[dict]:
        k = container()
        if k.health is not None:
            return k.health.snapshot()
        if k.settings.provider_mode == "fake":
            if not fake_health:
                fake_health.append(fake_monitor())
            return fake_health[0].snapshot()
        return [{"name": n, "ok": False, "detail": "health monitor not running", "checked_at": None}
                for n in INTEGRATIONS]

    @app.get("/agents/status")
    def agents_status():
        k = container()
        return agent_status.build_status(k.store, k.settings, integrations(), now=time.time(),
                                         started_at=app.state.started_at, inbox_enabled=k.inbox is not None)

    def who(request: Request) -> str:
        """The signed-in user (set by the auth guard); the admin name when auth is off (tests/fake only)."""
        return getattr(request.state, "user", None) or container().settings.admin_username

    # ---------- AE directory (who may trigger onboarding and gets the tier call; no delete: deactivate) ----------
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        """/aes bodies carry phone numbers: answer with a plain-string detail that never echoes the input.
        Every other route keeps FastAPI's default 422."""
        if request.url.path == "/aes" or request.url.path.startswith("/aes/"):
            msgs = [f"{'.'.join(str(p) for p in e.get('loc', ())[1:]) or 'body'}: {e.get('msg', 'invalid')}"
                    for e in exc.errors()]
            return JSONResponse({"detail": "; ".join(msgs) or "invalid request"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.get("/aes")
    def list_aes():
        return [directory.public(a) for a in container().store.list_aes()]

    @app.get("/aes/history")
    def aes_history(limit: int = LIMIT):
        return container().store.list_audit(directory.AUDIT_RUN)[::-1][:limit]

    @app.post("/aes", status_code=201)
    def create_ae(body: AeIn, request: Request):
        k = container()
        try:
            email, name = directory.clean_email(body.email), directory.clean_name(body.name)
            phone = directory.clean_phone(body.phone)
        except directory.Invalid as e:
            raise HTTPException(422, str(e)) from e
        if not k.store.add_ae(email, name, phone, body.active):
            raise HTTPException(409, f"an AE with email {email} already exists")
        directory.audit_change(k.store, who(request), "ae_create", email, {},
                               {"name": name, "phone": phone, "active": body.active})
        return directory.public(k.store.lookup_ae(email) or {})

    @app.patch("/aes/{email}")
    def update_ae(email: str, body: AePatch, request: Request):
        k = container()
        current = k.store.lookup_ae(email)
        if not current:
            raise HTTPException(404, "AE not found")
        try:
            fields = {k_: v for k_, v in dict(
                name=None if body.name is None else directory.clean_name(body.name),
                phone=None if body.phone is None else directory.clean_phone(body.phone),
                active=body.active).items() if v is not None}
        except directory.Invalid as e:
            raise HTTPException(422, str(e)) from e
        changed = {f: v for f, v in fields.items() if current.get(f) != v}
        if not changed:
            return directory.public(current)
        updated = k.store.update_ae(current["email"], **changed)
        if updated is None:
            raise HTTPException(404, "AE not found")
        directory.audit_change(k.store, who(request), "ae_update", current["email"],
                               {f: current.get(f) for f in changed}, changed)
        return directory.public(updated)

    # ---------- migration demo data (source vs destination contacts, same logic as the migration check) ----------
    def contacts() -> tuple[list[dict], list[dict]]:
        k = container()
        if k.migration_contacts is None:
            raise HTTPException(503, "migration databases are not configured")
        try:
            return k.migration_contacts()
        except MigrationDataUnavailable as e:
            raise HTTPException(503, f"migration databases unreachable: {e}") from e

    def summary(source: list[dict], dest: list[dict]) -> dict:
        return {"source_count": len(source), "dest_count": len(dest), "problems": find_problems(source, dest),
                "columns": compare_columns(source, dest), "last_check": container().store.latest_migration_check()}

    @app.get("/migration/summary")
    def migration_summary():
        return summary(*contacts())

    @app.get("/migration/data")
    def migration_data(side: Literal["source", "dest"] = "dest", limit: int = Query(10, ge=1, le=200),
                       offset: int = OFFSET, only_problems: bool = False):
        source, dest = contacts()
        problems = find_problems(source, dest)
        rows = [{**r, "problems": row_problems(r, side, problems)} for r in (source if side == "source" else dest)]
        if only_problems:
            rows = [r for r in rows if r["problems"]]
        return {"total": len(rows), "rows": rows[offset:offset + limit]}

    demo_lock = threading.Lock()

    def demo_write(action: str, request: Request, target) -> dict:
        k = container()
        if why := refusal(k.settings):
            raise HTTPException(409, why)
        if k.migration_write is None:
            raise HTTPException(503, "migration destination is not configured")
        by = who(request)
        with demo_lock:  # not the graph lock: a slow demo database must never stall webhooks or the scheduler
            source, dest = contacts()
            before = find_problems(source, dest)
            rows = target(source, dest)
            changed = _canonical(rows) != _canonical(dest)

            def record(status: str, note: str) -> None:
                audit(k.store, MIGRATION_DEMO_RUN, "migration", f"demo_{action}", input={"by": by}, status=status,
                      output={"changed": changed and status == "ok", "before": before,
                              "after": find_problems(source, rows) if status == "ok" else before},
                      rationale=f"{by} ran demo {action} on the demo destination ({note})")

            if not changed:
                record("ok", "already in that state, nothing written")
                return summary(source, dest)
            try:
                k.migration_write(rows)
            except SQLAlchemyError as e:
                record("failed", f"write failed ({type(e).__name__}); destination unchanged")
                raise HTTPException(503, f"migration destination write failed: {type(e).__name__}") from e
            record("ok", "rows rewritten")  # audited before the re-read: the write happened whatever comes next
            return summary(*contacts())

    @app.post("/migration/demo/fix")
    def migration_demo_fix(request: Request):
        """Repair the demo destination: add missing columns, drop duplicate rows, fill blanks from the source, add
        missing rows."""
        return demo_write("fix", request, repair)

    @app.post("/migration/demo/reset")
    def migration_demo_reset(request: Request):
        """Restore the planted defects (see app/db/seed.py demo_contacts)."""
        return demo_write("reset", request, lambda source, dest: demo_contacts()[1])

    @app.get("/ui/config", include_in_schema=False)
    def ui_config():
        return {"rocketlane_app_url": container().settings.rocketlane_app_url.rstrip("/")}

    @app.get("/ui/assets/{name:path}", include_in_schema=False)
    def ui_asset(name: str):
        base = (dist() / "assets").resolve()
        f = (base / name).resolve()
        if not f.is_relative_to(base) or not f.is_file():  # no traversal out of assets/, no SPA fallback here
            raise HTTPException(404, "not found")
        return FileResponse(f, headers={"Cache-Control": ASSET_CACHE})

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/{page:path}", include_in_schema=False)
    def ui(page: str = ""):
        return spa_index()

    return app


app = create_app()
