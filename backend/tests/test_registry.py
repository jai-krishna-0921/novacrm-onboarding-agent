import pytest
from pydantic import BaseModel, ValidationError

from app.services.store import MemoryStore
from app.tools.registry import PermanentToolError, ToolFailed, ToolNotAllowed, ToolRegistry, TransientToolError


class EchoIn(BaseModel):
    text: str
    phone: str | None = None


class EchoOut(BaseModel):
    text: str


class Counter:
    def __init__(self, failures: list[Exception] | None = None):
        self.calls = 0
        self.failures = list(failures or [])

    def __call__(self, args: EchoIn) -> EchoOut:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return EchoOut(text=args.text.upper())


@pytest.fixture
def store():
    return MemoryStore()


def make(store, fn, allow=("intake",)):
    reg = ToolRegistry(store, allowlist={a: {"echo"} for a in allow}, sleep=lambda s: None)
    reg.register("echo", fn, EchoIn, EchoOut)
    return reg


def test_successful_call_returns_typed_output_and_is_audited(store):
    fn = Counter()
    out = make(store, fn).call("intake", "echo", {"text": "hi"}, run_id="r1")
    assert out == EchoOut(text="HI")
    [row] = store.tool_calls
    assert (row["tool"], row["agent"], row["status"], row["attempt"]) == ("echo", "intake", "ok", 1)
    assert store.audit_rows[-1]["step"] == "tool:echo" and store.audit_rows[-1]["ts"]


def test_agent_outside_allowlist_is_blocked_before_execution(store):
    fn = Counter()
    with pytest.raises(ToolNotAllowed):
        make(store, fn).call("comms", "echo", {"text": "hi"}, run_id="r1")
    assert fn.calls == 0
    assert store.audit_rows[-1]["status"] == "blocked"


def test_invalid_args_never_reach_the_tool(store):
    fn = Counter()
    with pytest.raises(ValidationError):
        make(store, fn).call("intake", "echo", {"nope": 1}, run_id="r1")
    assert fn.calls == 0


def test_idempotency_key_returns_stored_result_without_reexecuting(store):
    fn = Counter()
    reg = make(store, fn)
    a = reg.call("intake", "echo", {"text": "hi"}, run_id="r1", idempotency_key="k1")
    b = reg.call("intake", "echo", {"text": "different"}, run_id="r1", idempotency_key="k1")
    assert a == b == EchoOut(text="HI") and fn.calls == 1


def test_transient_errors_are_retried_then_succeed(store):
    fn = Counter([TransientToolError("503"), TransientToolError("timeout")])
    out = make(store, fn).call("intake", "echo", {"text": "hi"}, run_id="r1")
    assert out.text == "HI" and fn.calls == 3
    assert store.tool_calls[-1]["attempt"] == 3


def test_persistent_transient_errors_fail_after_three_attempts(store):
    fn = Counter([TransientToolError("503")] * 5)
    with pytest.raises(ToolFailed):
        make(store, fn).call("intake", "echo", {"text": "hi"}, run_id="r1", idempotency_key="k")
    assert fn.calls == 3
    assert store.get_idem("k") is None  # a failure must not be cached as a result
    assert store.tool_calls[-1]["status"] == "failed"


def test_permanent_errors_are_not_retried(store):
    fn = Counter([PermanentToolError("400 bad request")])
    with pytest.raises(ToolFailed):
        make(store, fn).call("intake", "echo", {"text": "hi"}, run_id="r1")
    assert fn.calls == 1


def test_phone_numbers_are_masked_in_logs(store):
    make(store, Counter()).call("intake", "echo", {"text": "hi", "phone": "+919876543210"}, run_id="r1")
    logged = str(store.tool_calls) + str(store.audit_rows)
    assert "+919876543210" not in logged and "3210" in logged
