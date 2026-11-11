"""The only place a plan tier becomes 'confirmed'. No tier is ever defaulted."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, StrictBool

from app.config import Tier


class CallOutcome(StrEnum):
    CONFIRMED = "confirmed"
    UNCLEAR = "unclear"
    NO_ANSWER = "no_answer"


class CallResult(BaseModel):
    """Normalized voice-provider result (provider adapters map their payloads into this)."""

    provider_call_id: str
    status: str  # connected | no_answer | voicemail | busy | failed
    plan_tier: str | None = None
    confirmed: StrictBool | None = None
    transcript: str = ""


@dataclass(frozen=True)
class TierDecision:
    outcome: CallOutcome
    tier: Tier | None
    rationale: str


def _norm(v: str | None) -> str | None:
    return v.strip().lower() if v else None


def decide_tier(result: CallResult, cross_check: str | None) -> TierDecision:
    if result.status != "connected":
        return TierDecision(CallOutcome.NO_ANSWER, None, f"call not connected ({result.status})")
    tier = _norm(result.plan_tier)
    if tier not in {t.value for t in Tier}:
        return TierDecision(CallOutcome.UNCLEAR, None, f"AE answer not a valid tier ({result.plan_tier!r})")
    if result.confirmed is not True:
        return TierDecision(CallOutcome.UNCLEAR, None, "AE did not explicitly confirm the read-back")
    if _norm(cross_check) != tier:
        return TierDecision(CallOutcome.UNCLEAR, None,
                            f"transcript cross-check disagrees (provider={tier}, transcript={cross_check})")
    return TierDecision(CallOutcome.CONFIRMED, Tier(tier),
                        f"AE confirmed {tier}; provider analysis and transcript cross-check agree")
