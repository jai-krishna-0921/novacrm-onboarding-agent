"""The LangChain-backed LLM must: use native structured output, record usage/cost, and raise on refusal/parse failure."""

import pytest
from langchain_core.messages import AIMessage

from app.llm.client import LangChainLLM, LLMRefused
from app.llm.schemas import DealFields
from app.services.store import MemoryStore


class FakeStructured:
    def __init__(self, result):
        self.result, self.messages = result, None

    def invoke(self, messages):
        self.messages = messages
        return self.result


class FakeChat:
    def __init__(self, result):
        self.structured = FakeStructured(result)
        self.kwargs = None

    def with_structured_output(self, schema, **kwargs):
        self.kwargs = {"schema": schema, **kwargs}
        return self.structured


def raw(stop="end_turn"):
    return AIMessage(content="", usage_metadata={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
                     response_metadata={"stop_reason": stop})


def make(result, model="claude-haiku-5-5"):
    store = MemoryStore()
    chat = FakeChat(result)
    return LangChainLLM(store, model, chat_factory=lambda m: chat), chat, store


def call(llm):
    return llm.parse(run_id="r1", agent="intake", node="extract_fields", prompt="extract_v1",
                     user="<email>hi</email>", schema=DealFields)


def test_uses_native_json_schema_structured_output_with_raw_usage():
    parsed = DealFields(customer_name="Acme")
    llm, chat, _ = make({"raw": raw(), "parsed": parsed, "parsing_error": None})
    assert call(llm) == parsed
    assert chat.kwargs["method"] == "json_schema" and chat.kwargs["include_raw"] is True
    system, user = chat.structured.messages
    assert "untrusted" in system.content.lower() and user.content == "<email>hi</email>"


def test_records_tokens_cost_and_prompt_version():
    llm, _, store = make({"raw": raw(), "parsed": DealFields(), "parsing_error": None})
    call(llm)
    [row] = store.llm_calls
    assert (row["input_tokens"], row["output_tokens"], row["prompt_version"]) == (1000, 200, "extract_v1")
    assert row["cost_usd"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
    assert row["model"] == "claude-haiku-5-5" and row["node"] == "extract_fields"


@pytest.mark.parametrize("result", [
    {"raw": raw("refusal"), "parsed": None, "parsing_error": None},
    {"raw": raw(), "parsed": None, "parsing_error": ValueError("bad json")},
])
def test_refusal_or_unparseable_output_raises_and_is_still_costed(result):
    llm, _, store = make(result)
    with pytest.raises(LLMRefused):
        call(llm)
    assert len(store.llm_calls) == 1
