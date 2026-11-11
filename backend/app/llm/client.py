"""LLM wrapper. Every call records model, prompt version, tokens, latency and cost."""

import time
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.services.store import Store, now

T = TypeVar("T", bound=BaseModel)
PROMPTS = Path(__file__).parent / "prompts"

# USD per million tokens (input, output). Source: claude-api skill model table, 2026-10.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}


def load_prompt(name: str) -> tuple[str, str]:
    """`extract_v1` -> (text, version)."""
    return (PROMPTS / f"{name}.md").read_text(), name


class LLM(Protocol):
    def parse(self, *, run_id: str, agent: str, node: str, prompt: str, user: str,
              schema: type[T]) -> T: ...


class LLMRefused(Exception):
    pass


class AnthropicLLM:
    def __init__(self, store: Store, model: str, api_key: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key or None, max_retries=2, timeout=60)
        self.store, self.model = store, model

    def parse(self, *, run_id, agent, node, prompt, user, schema):
        system, version = load_prompt(prompt)
        started = time.monotonic()
        resp = self.client.messages.parse(
            model=self.model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": user}], output_format=schema,
        )
        usage = resp.usage
        p_in, p_out = PRICES.get(self.model, (0.0, 0.0))
        self.store.add_llm_call(dict(
            run_id=run_id, ts=now(), agent=agent, node=node, model=self.model, prompt_version=version,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=round((usage.input_tokens * p_in + usage.output_tokens * p_out) / 1e6, 6),
        ))
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise LLMRefused(f"{node}: stop_reason={resp.stop_reason}")
        return resp.parsed_output
