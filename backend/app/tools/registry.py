"""Every external action goes through ToolRegistry.call():
allowlist -> input validation -> idempotency (reserve the key BEFORE the side effect) -> execute with retry
-> record -> store result.

A key still "in flight" when a call starts means an earlier attempt was cut off (worker died, or the provider
timed out after the request was sent) and the side effect may already exist. It is never silently repeated:
the tool's `reconcile` lookup adopts the earlier result; a tool the provider deduplicates may re-send within
`repeat_within` seconds; anything else fails once with "outcome unknown" (the run escalates for a human).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

from pydantic import BaseModel

from app.services.audit import redact
from app.services.store import Store, now


class ToolNotAllowed(Exception):
    """An agent tried to use a tool outside its allowlist. Never retried; the run fails."""


class TransientToolError(Exception):
    """5xx, timeouts, rate limits: safe to retry."""


class AmbiguousToolError(TransientToolError):
    """Timed out after the request was sent: the write may or may not have happened."""


class PermanentToolError(Exception):
    """4xx and business errors: retrying will not help."""


class ToolFailed(Exception):
    def __init__(self, tool: str, reason: str):
        super().__init__(f"{tool} failed: {reason}")
        self.tool, self.reason = tool, reason


@dataclass(frozen=True)
class _Spec:
    fn: Callable[[Any], BaseModel]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    reconcile: Callable[[Any], BaseModel | None] | None  # read-only "did it already happen?" lookup
    repeat_within: float  # seconds after which re-sending an in-flight call is no longer safe (0: never)


class ToolRegistry:
    def __init__(self, store: Store, allowlist: dict[str, set[str]], max_attempts: int = 3,
                 sleep: Callable[[float], None] = time.sleep, backoff_base: float = 0.5):
        self.store, self.allowlist, self.max_attempts = store, allowlist, max_attempts
        self.sleep, self.backoff_base = sleep, backoff_base
        self._tools: dict[str, _Spec] = {}

    def register(self, name: str, fn: Callable[[Any], BaseModel],
                 input_model: type[BaseModel], output_model: type[BaseModel], *,
                 reconcile: Callable[[Any], BaseModel | None] | None = None, repeat_within: float = 0) -> None:
        """`reconcile(args)` returns the earlier result if the side effect already happened, else None.
        `repeat_within`: re-sending is harmless (math.inf) or deduplicated by the provider for that many seconds."""
        self._tools[name] = _Spec(fn, input_model, output_model, reconcile, repeat_within)

    def call(self, agent: str, tool: str, args: dict, *, run_id: str,
             idempotency_key: str | None = None, rationale: str = "", audit_success: bool = True) -> Any:
        """audit_success=False: routine read-only polls (e.g. the tracking sync) are recorded in tool_calls but
        don't add an audit row on success; failures, blocks and reconciled outcomes are always audited."""
        safe_args = redact(args)
        if tool not in self.allowlist.get(agent, set()):
            self._audit(run_id, agent, tool, safe_args, None, "blocked",
                        f"agent '{agent}' is not allowed to call '{tool}'")
            raise ToolNotAllowed(f"{agent} -> {tool}")

        spec = self._tools[tool]
        parsed = spec.input_model.model_validate(args)

        if idempotency_key:
            held = self.store.reserve_idem(idempotency_key)
            if held and "result" in held:
                self._audit(run_id, agent, tool, safe_args, held["result"], "replayed",
                            f"idempotency key {idempotency_key} already completed; returning stored result")
                return spec.output_model.model_validate(held["result"])
            if held:
                out = self._in_flight(spec, parsed, run_id, agent, tool, safe_args, idempotency_key,
                                      held["started_at"])
                if out is not None:
                    return out

        attempt, error, maybe_sent, ambiguous = 0, "", False, False
        started = time.monotonic()
        while attempt < self.max_attempts:
            attempt += 1
            try:
                result = spec.fn(parsed)
                return self._success(spec, result, run_id, agent, tool, safe_args, attempt, started,
                                     idempotency_key, rationale or "tool call succeeded", audit=audit_success)
            except AmbiguousToolError as e:
                error, maybe_sent = f"ambiguous: {e}", True
                if spec.repeat_within != float("inf"):
                    ambiguous = True  # never re-send blindly: look it up below, or let a human verify
                    break
                if attempt < self.max_attempts:
                    self.sleep(self.backoff_base * 2 ** (attempt - 1))
            except TransientToolError as e:
                error, maybe_sent = f"transient: {e}", True
                if attempt < self.max_attempts:
                    self.sleep(self.backoff_base * 2 ** (attempt - 1))
            except PermanentToolError as e:
                error = f"permanent: {e}"
                break

        if ambiguous:
            if spec.reconcile:
                try:
                    found = spec.reconcile(parsed)
                except Exception as e:  # noqa: BLE001 - any lookup failure: still unknown, never re-send
                    found, error = None, f"{error}; lookup failed ({type(e).__name__}: {e})"
                if found is not None:
                    return self._success(spec, found, run_id, agent, tool, safe_args, attempt, started,
                                         idempotency_key, "outcome confirmed by lookup after an ambiguous timeout")
            error = f"outcome unknown ({error}); verify in the target system before retrying"
        if idempotency_key and not maybe_sent:
            self.store.release_idem(idempotency_key)  # every attempt was rejected: nothing happened
        self._record(run_id, agent, tool, safe_args, {"error": error}, "failed", attempt, started,
                     idempotency_key)
        self._audit(run_id, agent, tool, safe_args, {"error": error}, "failed",
                    f"gave up after {attempt} attempt(s)")
        raise ToolFailed(tool, error)

    def _success(self, spec, result, run_id, agent, tool, safe_args, attempt, started, key, rationale,
                 audit: bool = True) -> Any:
        out = spec.output_model.model_validate(result.model_dump())
        dumped = out.model_dump(mode="json")
        self._record(run_id, agent, tool, safe_args, dumped, "ok", attempt, started, key)
        if audit:
            self._audit(run_id, agent, tool, safe_args, dumped, "ok", rationale)
        if key:
            self.store.put_idem(key, dumped)
        return out

    def _in_flight(self, spec, parsed, run_id, agent, tool, safe_args, key, started_at) -> Any:
        """Key reserved by an earlier attempt that never recorded an outcome. Returns the adopted result, or None
        when it is safe to execute; raises ToolFailed when the outcome is unknown."""
        started = time.monotonic()
        if spec.reconcile:
            try:
                found = spec.reconcile(parsed)
            except Exception as e:  # noqa: BLE001
                self._fail(run_id, agent, tool, safe_args, started, key,
                           f"outcome unknown: an earlier attempt was interrupted and the lookup failed "
                           f"({type(e).__name__}: {e}); verify in the target system before retrying")
            if found is not None:
                return self._success(spec, found, run_id, agent, tool, safe_args, 0, started, key,
                                     "earlier interrupted attempt had succeeded; adopted its result")
            self._audit(run_id, agent, tool, safe_args, None, "ok",
                        "earlier attempt was interrupted; lookup found nothing, executing")
            return None
        age = (datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds()
        if age < spec.repeat_within:
            self._audit(run_id, agent, tool, safe_args, None, "ok",
                        f"earlier attempt was interrupted {int(age)}s ago; re-sending is safe (deduplicated)")
            return None
        # The human who resolves the resulting escalation is the reconciliation: their retry executes.
        self.store.release_idem(key)
        self._fail(run_id, agent, tool, safe_args, started, key,
                   f"outcome unknown: an earlier attempt (started {started_at}) was interrupted and may have "
                   "completed; verify in the target system - a retry will perform it again")

    def _fail(self, run_id, agent, tool, safe_args, started, key, error) -> NoReturn:
        self._record(run_id, agent, tool, safe_args, {"error": error}, "failed", 0, started, key)
        self._audit(run_id, agent, tool, safe_args, {"error": error}, "failed", error)
        raise ToolFailed(tool, error)

    def _record(self, run_id, agent, tool, args, result, status, attempt, started, key) -> None:
        self.store.add_tool_call(dict(run_id=run_id, ts=now(), agent=agent, tool=tool, args=args,
                                      result=redact(result), status=status, attempt=attempt,
                                      latency_ms=int((time.monotonic() - started) * 1000),
                                      idempotency_key=key))

    def _audit(self, run_id, agent, tool, args, output, status, rationale) -> None:
        self.store.add_audit(dict(run_id=run_id, ts=now(), agent=agent, step=f"tool:{tool}",
                                  input=args, output=redact(output), rationale=rationale, status=status))
