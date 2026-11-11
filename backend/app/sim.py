"""Drives the real graph against fake providers: used by pytest and `make simulate`.

The voice fake resolves each call interrupt immediately from its script, standing in for the
Retell webhook that resumes the graph in production.
"""

import json
import re

from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings
from app.graph.root import build_graph
from app.graph.state import Deps
from app.llm.client import LLMError
from app.llm.schemas import DealFields, HandoffDraft, TranscriptReading, WelcomeDraft
from app.providers.fake import FakeWorld
from app.services.runner import Runner
from app.services.store import MemoryStore, now
from app.tools.definitions import InboundEmail, build_registry  # noqa: F401 (re-export)


class FakeLLM:
    """Scripted structured outputs. Default transcript reading uses only the AE's own lines."""

    def __init__(self, fields: DealFields, reading: TranscriptReading | None = None,
                 welcome: str | None = None, handoff: list | None = None):
        self.fields, self.reading, self.welcome = fields, reading, welcome
        self.handoff = handoff  # scripted handoff drafts (str, or an Exception to raise), consumed in order
        self.calls: dict[str, int] = {}
        self.store = None

    def parse(self, *, run_id, agent, node, prompt, user, schema):
        self.calls[node] = self.calls.get(node, 0) + 1
        if self.store is not None:
            self.store.add_llm_call(dict(run_id=run_id, ts=now(), agent=agent, node=node, model="fake",
                                         prompt_version=prompt, input_tokens=0, output_tokens=0,
                                         latency_ms=0, cost_usd=0.0))
        if schema is DealFields:
            return self.fields.model_copy()
        if schema is TranscriptReading:
            return self.reading or _read_transcript(user)
        if schema is WelcomeDraft:
            if self.welcome is not None:
                return WelcomeDraft(message=self.welcome)
            f = json.loads(user)
            return WelcomeDraft(message=(
                f"Welcome to NovaCRM, {f['customer_name']}! Your {f['duration_days']}-day onboarding starts now "
                f"with your {f['csm_model']} Customer Success Manager: Kickoff, Data Migration, "
                f"Configuration and Go-Live. Next up: scheduling the kickoff call."))
        if schema is HandoffDraft:
            if self.handoff:
                nxt = self.handoff.pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                return HandoffDraft(summary=nxt)
            if self.handoff is not None:
                raise LLMError("draft_handoff: no scripted draft left")
            f = json.loads(user.split("\n", 1)[1].rsplit("\n", 1)[0])
            m = f["migration"]
            return HandoffDraft(summary=(
                f"{f['customer_name']} finished the {f['plan']} plan ({f['duration_days']}-day onboarding, "
                f"{f['csm_model']} CSM). All four phases are complete with {f['tasks_done']} of {f['tasks_total']} "
                f"tasks done. Data migration check: {m['result']}. Support should watch the first weeks of usage."))
        raise AssertionError(f"FakeLLM has no script for {schema}")


def _read_transcript(text: str) -> TranscriptReading:
    ae = " ".join(re.findall(r"AE:\s*([^\n]*?)(?=\s*Agent:|\n|$)", text, flags=re.S)).lower()
    tiers = [t for t in ("enterprise", "growth") if t in ae]
    tier = tiers[0] if len(tiers) == 1 else "unclear"
    return TranscriptReading(plan_tier=tier, ae_confirmed=tier != "unclear" and "yes" in ae,
                             reason=ae[:120] or "no AE speech")


class Harness:
    def __init__(self, settings: Settings, llm=None, **world_kw):
        self.settings = settings
        self.store = MemoryStore()
        self.world = FakeWorld(**world_kw)
        self.llm = llm or FakeLLM(fields=DealFields())
        if isinstance(self.llm, FakeLLM):
            self.llm.store = self.store
        registry = build_registry(self.store, self.world, sleep=lambda s: None)
        self.deps = Deps(settings, self.store, registry, self.llm, sleep=lambda s: None)
        self.graph = build_graph(self.deps, InMemorySaver())
        self.runner = Runner(self.graph, self.store)
        self.auto_wait = True  # resume durable retry waits immediately (tests can turn this off)

    def _cfg(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def receive(self, email: InboundEmail) -> str:
        run_id, out = self.runner.start(email.model_dump())
        if out is not None:
            self._settle(run_id, out)
        return run_id

    def resolve(self, run_id: str, resolution: dict) -> None:
        self._settle(run_id, self.runner.resolve(run_id, resolution))

    def _drive(self, run_id: str, payload) -> None:
        """Low-level: invoke (or resume from checkpoint with payload=None) and settle call interrupts."""
        self._settle(run_id, self.runner.invoke(run_id, payload))

    def _settle(self, run_id: str, out: dict) -> None:
        # Stand-in for the Retell webhook: answer each pending call from the voice script.
        while out.get("__interrupt__"):
            req = out["__interrupt__"][0].value
            if req["type"] == "wait" and self.auto_wait:
                out = self.runner.resume_wait(run_id)
            elif req["type"] == "call":
                out = self.runner.resume_call(run_id, self.world.voice.result_for(req["provider_call_id"]))
            else:
                return  # waiting for a human (or a durable wait when auto_wait is off)

    def resume_wait(self, run_id: str) -> None:
        self._settle(run_id, self.runner.resume_wait(run_id))

    def status(self, run_id: str) -> str:
        return self.store.runs[run_id]["status"]

    def state(self, run_id: str) -> dict:
        return self.graph.get_state(self._cfg(run_id)).values

    def escalation(self, run_id: str) -> dict:
        return [e for e in self.store.escalations.values() if e["run_id"] == run_id][-1]
