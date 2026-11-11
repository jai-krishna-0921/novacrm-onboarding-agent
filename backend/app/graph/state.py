import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypedDict

from app.config import Settings, Tier, TierRule, tier_rules
from app.llm.client import LLM
from app.services.audit import audit
from app.services.keys import customer_key  # noqa: F401  (re-exported)
from app.services.store import Store
from app.tools.registry import ToolRegistry


class OnboardingState(TypedDict, total=False):
    run_id: str
    email: dict                 # InboundEmail.model_dump()
    extracted: dict             # ExtractedDeal.model_dump()
    not_a_deal: bool
    issues: list[str]
    sender_ok: bool             # email sender verified as an active AE
    ae: dict                    # AeLookupOut of the AE to call
    call_attempts: int
    provider_call_id: str
    call_result: dict           # CallResult.model_dump()
    last_call_rationale: str
    retry_at: float | None      # epoch seconds; durable wait before the next call attempt
    retry_since: float | None
    plan_tier: str
    tier_source: str            # "ae_call" | "human:<who>"
    project_id: str
    welcome_message: str
    channel_id: str
    status: str
    escalation_reason: str
    escalated_by: str           # agent that raised the escalation
    escalation_kind: str        # tier | input | duplicate | system (decides allowed resolutions)
    escalation_count: int
    resolution: dict


@dataclass
class Deps:
    settings: Settings
    store: Store
    registry: ToolRegistry
    llm: LLM
    today: Callable[[], date] = date.today
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time
    rules: dict[Tier, TierRule] = field(init=False)

    def __post_init__(self):
        self.rules = tier_rules(self.settings)

    def set_status(self, state: OnboardingState, status: str, **run_fields: Any) -> dict:
        self.store.upsert_run(state["run_id"], status=status, **run_fields)
        return {"status": status}

    def audit(self, state: OnboardingState, agent: str, step: str, rationale: str, *,
              input: Any = None, output: Any = None, status: str = "ok") -> None:
        audit(self.store, state["run_id"], agent, step, input=input, output=output,
              rationale=rationale, status=status)

    def escalate(self, state: OnboardingState, agent: str, reason: str, kind: str) -> dict:
        self.audit(state, agent, "escalate_request", f"[{kind}] {reason}", status="escalated")
        return {"escalation_reason": reason, "escalated_by": agent, "escalation_kind": kind}
