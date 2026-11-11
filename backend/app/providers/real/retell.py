"""Retell AI voice provider: start calls, verify webhooks, normalize call results (docs/reference/retell-api.md)."""

import hashlib
import hmac
import re
import time

import httpx

from app.guardrails.decision import CallResult
from app.tools.definitions import VoiceStartIn, VoiceStartOut
from app.tools.registry import AmbiguousToolError, PermanentToolError, TransientToolError

RETELL_BASE = "https://api.retellai.com"
_SIG = re.compile(r"^v=(\d+),d=([0-9a-f]+)$")

NO_ANSWER = {"dial_no_answer", "user_declined", "registered_call_timeout", "error_user_not_joined"}
VOICEMAIL = {"voicemail_reached", "ivr_reached"}


def verify_signature(raw_body: str, api_key: str, signature: str | None, now_ms: int | None = None) -> bool:
    m = _SIG.match(signature or "")
    if not m:
        return False
    ts = int(m[1])
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now_ms - ts) > 5 * 60 * 1000:
        return False
    expected = hmac.new(api_key.encode(), (raw_body + m[1]).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, m[2])


def map_call_analyzed(payload: dict) -> tuple[str, CallResult]:
    """`call_analyzed` webhook -> (run_id, normalized CallResult). Unconnected calls never carry a tier."""
    call = payload["call"]
    run_id = (call.get("metadata") or {}).get("run_id")
    if not run_id:
        raise ValueError("webhook call has no metadata.run_id")
    reason = call.get("disconnection_reason") or ""
    if reason in NO_ANSWER:
        status = "no_answer"
    elif reason == "dial_busy":
        status = "busy"
    elif reason in VOICEMAIL:
        status = "voicemail"
    elif call.get("call_status") in ("not_connected", "error") or reason.startswith("error") \
            or call.get("call_status") != "ended":
        status = "failed"
    else:
        status = "connected"
    if status != "connected":
        return run_id, CallResult(provider_call_id=call["call_id"], status=status)
    data = (call.get("call_analysis") or {}).get("custom_analysis_data") or {}
    tier = data.get("plan_tier") if data.get("plan_tier") in ("enterprise", "growth", "unclear") else None
    confirmed = data.get("tier_confirmed") if isinstance(data.get("tier_confirmed"), bool) else None
    # Retell labels the callee "User:"; our prompts and audit call them the AE.
    transcript = re.sub(r"^User:", "AE:", call.get("transcript") or "", flags=re.M)
    return run_id, CallResult(provider_call_id=call["call_id"], status=status, plan_tier=tier,
                              confirmed=confirmed, transcript=transcript)


class RetellVoice:
    def __init__(self, api_key: str, agent_id: str, from_number: str, timeout: float = 20.0):
        self.agent_id, self.from_number = agent_id, from_number
        self.http = httpx.Client(base_url=RETELL_BASE, timeout=timeout,
                                 headers={"Authorization": f"Bearer {api_key}"})

    def start_call(self, a: VoiceStartIn) -> VoiceStartOut:
        body = {"from_number": self.from_number, "to_number": a.to_number, "override_agent_id": self.agent_id,
                "metadata": {"run_id": a.run_id, "attempt": a.attempt},
                "retell_llm_dynamic_variables": {"ae_name": a.ae_name, "customer_name": a.customer_name}}
        if a.idempotency_key:
            # Retell replays the original response (same call_id) for 1 hour instead of dialling again.
            body["idempotency_key"] = a.idempotency_key
        try:
            r = self.http.post("/v2/create-phone-call", json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            raise TransientToolError(f"connect error: {e}") from e  # request never sent
        except httpx.TransportError as e:
            # Read timeout / reset after the request went out: the AE's phone may already be ringing.
            raise AmbiguousToolError(f"{type(e).__name__} after sending create-phone-call") from e
        if r.status_code in (409, 429) or r.status_code >= 500:  # 409: new-number outbound hold
            raise TransientToolError(f"retell {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise PermanentToolError(f"retell {r.status_code}: {r.text[:200]}")
        return VoiceStartOut(provider_call_id=r.json()["call_id"])

    def get_call(self, call_id: str) -> dict | None:
        """Call object for reconciliation when a webhook was lost. None if unknown."""
        r = self.http.get(f"/v2/get-call/{call_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
