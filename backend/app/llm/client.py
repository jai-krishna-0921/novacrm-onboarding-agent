"""LLM wrapper over LangChain's ChatAnthropic. Every call records model, prompt version, tokens, latency, cost.

Structured output uses method="json_schema" (Claude's native structured outputs). The default
"function_calling" method forces a tool choice, which current Claude models reject.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.services.store import Store, now

T = TypeVar("T", bound=BaseModel)
PROMPTS = Path(__file__).parent / "prompts"

# USD per million tokens (input, output). Source: claude-api skill model table (cached 2026-10).
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}


def load_prompt(name: str) -> tuple[str, str]:
    """`extract_v1` -> (text, version)."""
    return (PROMPTS / f"{name}.md").read_text(), name


class LLM(Protocol):
    def parse(self, *, run_id: str, agent: str, node: str, prompt: str, user: str,
              schema: type[T]) -> T: ...


class LLMRefused(Exception):
    pass


def _chat_anthropic(model: str, api_key: str | None = None) -> Any:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, api_key=api_key or None, max_tokens=2048, max_retries=2, timeout=60)


class LangChainLLM:
    def __init__(self, store: Store, model: str, api_key: str | None = None,
                 chat_factory: Callable[[str], Any] | None = None):
        self.store, self.model = store, model
        self.chat = (chat_factory or (lambda m: _chat_anthropic(m, api_key)))(model)

    def parse(self, *, run_id, agent, node, prompt, user, schema):
        system, version = load_prompt(prompt)
        runnable = self.chat.with_structured_output(schema, method="json_schema", include_raw=True)
        started = time.monotonic()
        out = runnable.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        raw = out["raw"]
        usage = getattr(raw, "usage_metadata", None) or {}
        tin, tout = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        p_in, p_out = PRICES.get(self.model, (0.0, 0.0))
        stop = (getattr(raw, "response_metadata", None) or {}).get("stop_reason")
        self.store.add_llm_call(dict(
            run_id=run_id, ts=now(), agent=agent, node=node, model=self.model, prompt_version=version,
            input_tokens=tin, output_tokens=tout, latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=round((tin * p_in + tout * p_out) / 1e6, 6), stop_reason=stop,
        ))
        if stop == "refusal" or out.get("parsing_error") or out.get("parsed") is None:
            raise LLMRefused(f"{node}: stop_reason={stop} parsing_error={out.get('parsing_error')}")
        return out["parsed"]
