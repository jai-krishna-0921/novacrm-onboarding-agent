"""Every external action goes through ToolRegistry.call():
allowlist -> input validation -> idempotency -> execute with retry -> record -> store result."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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


class ToolRegistry:
    def __init__(self, store: Store, allowlist: dict[str, set[str]], max_attempts: int = 3,
                 sleep: Callable[[float], None] = time.sleep, backoff_base: float = 0.5):
        self.store, self.allowlist, self.max_attempts = store, allowlist, max_attempts
        self.sleep, self.backoff_base = sleep, backoff_base
        self._tools: dict[str, _Spec] = {}

    def register(self, name: str, fn: Callable[[Any], BaseModel],
                 input_model: type[BaseModel], output_model: type[BaseModel]) -> None:
        self._tools[name] = _Spec(fn, input_model, output_model)

    def call(self, agent: str, tool: str, args: dict, *, run_id: str,
             idempotency_key: str | None = None, rationale: str = "") -> Any:
        safe_args = redact(args)
        if tool not in self.allowlist.get(agent, set()):
            self._audit(run_id, agent, tool, safe_args, None, "blocked",
                        f"agent '{agent}' is not allowed to call '{tool}'")
            raise ToolNotAllowed(f"{agent} -> {tool}")

        spec = self._tools[tool]
        parsed = spec.input_model.model_validate(args)

        if idempotency_key and (cached := self.store.get_idem(idempotency_key)) is not None:
            self._audit(run_id, agent, tool, safe_args, cached, "replayed",
                        f"idempotency key {idempotency_key} already completed; returning stored result")
            return spec.output_model.model_validate(cached)

        attempt, error = 0, ""
        started = time.monotonic()
        while attempt < self.max_attempts:
            attempt += 1
            try:
                result = spec.fn(parsed)
                out = spec.output_model.model_validate(result.model_dump())
                dumped = out.model_dump(mode="json")
                self._record(run_id, agent, tool, safe_args, dumped, "ok", attempt, started, idempotency_key)
                self._audit(run_id, agent, tool, safe_args, dumped, "ok", rationale or "tool call succeeded")
                if idempotency_key:
                    self.store.put_idem(idempotency_key, dumped)
                return out
            except TransientToolError as e:
                error = f"transient: {e}"
                if attempt < self.max_attempts:
                    self.sleep(self.backoff_base * 2 ** (attempt - 1))
            except PermanentToolError as e:
                error = f"permanent: {e}"
                break

        self._record(run_id, agent, tool, safe_args, {"error": error}, "failed", attempt, started,
                     idempotency_key)
        self._audit(run_id, agent, tool, safe_args, {"error": error}, "failed",
                    f"gave up after {attempt} attempt(s)")
        raise ToolFailed(tool, error)

    def _record(self, run_id, agent, tool, args, result, status, attempt, started, key) -> None:
        self.store.add_tool_call(dict(run_id=run_id, ts=now(), agent=agent, tool=tool, args=args,
                                      result=redact(result), status=status, attempt=attempt,
                                      latency_ms=int((time.monotonic() - started) * 1000),
                                      idempotency_key=key))

    def _audit(self, run_id, agent, tool, args, output, status, rationale) -> None:
        self.store.add_audit(dict(run_id=run_id, ts=now(), agent=agent, step=f"tool:{tool}",
                                  input=args, output=redact(output), rationale=rationale, status=status))
