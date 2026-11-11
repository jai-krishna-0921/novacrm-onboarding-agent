import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app.guardrails.decision import CallResult
from app.providers.real.retell import RETELL_BASE, RetellVoice, map_call_analyzed, verify_signature
from app.tools.definitions import VoiceStartIn
from app.tools.registry import PermanentToolError, TransientToolError

KEY = "key_webhook"
NOW_MS = 1_794_400_000_000


def sign(body: str, ts: int = NOW_MS, key: str = KEY) -> str:
    return f"v={ts},d={hmac.new(key.encode(), (body + str(ts)).encode(), hashlib.sha256).hexdigest()}"


# ---------- signature ----------

def test_valid_signature_passes():
    body = '{"event":"call_analyzed"}'
    assert verify_signature(body, KEY, sign(body), now_ms=NOW_MS)


@pytest.mark.parametrize("sig", [None, "", "garbage", "v=1,d=abc"])
def test_malformed_or_wrong_signature_fails(sig):
    assert not verify_signature('{"a":1}', KEY, sig, now_ms=NOW_MS)


def test_tampered_body_fails():
    assert not verify_signature('{"a":2}', KEY, sign('{"a":1}'), now_ms=NOW_MS)


def test_stale_timestamp_fails():
    body = "{}"
    assert not verify_signature(body, KEY, sign(body, ts=NOW_MS - 6 * 60 * 1000), now_ms=NOW_MS)


# ---------- payload mapping ----------

def payload(**call):
    base = {"call_id": "call_abc", "call_status": "ended", "disconnection_reason": "user_hangup",
            "metadata": {"run_id": "run-1", "attempt": 1},
            "transcript": "Agent: Enterprise or Growth?\nUser: Enterprise.\nAgent: Confirm?\nUser: Yes.",
            "call_analysis": {"custom_analysis_data": {"plan_tier": "enterprise", "tier_confirmed": True}}}
    return {"event": "call_analyzed", "call": {**base, **call}}


def test_connected_call_maps_tier_confirmation_and_transcript():
    run_id, r = map_call_analyzed(payload())
    assert run_id == "run-1"
    assert r == CallResult(provider_call_id="call_abc", status="connected", plan_tier="enterprise",
                           confirmed=True, transcript=payload()["call"]["transcript"].replace("User:", "AE:"))


@pytest.mark.parametrize("reason,status", [
    ("dial_no_answer", "no_answer"), ("user_declined", "no_answer"), ("dial_busy", "busy"),
    ("dial_failed", "failed"), ("invalid_destination", "failed"), ("marked_as_spam", "failed"),
    ("voicemail_reached", "voicemail"), ("ivr_reached", "voicemail"),
    ("error_unknown", "failed"), ("credit_exhausted", "failed"),
])
def test_unconnected_outcomes_never_carry_a_tier(reason, status):
    _, r = map_call_analyzed(payload(disconnection_reason=reason, call_status="not_connected"))
    assert r.status == status and r.plan_tier is None and r.confirmed is None


def test_missing_custom_analysis_is_unclear_not_an_error():
    _, r = map_call_analyzed(payload(call_analysis={}))
    assert r.status == "connected" and r.plan_tier is None and r.confirmed is None


def test_non_boolean_confirmation_is_dropped():
    _, r = map_call_analyzed(payload(call_analysis={"custom_analysis_data": {"plan_tier": "growth",
                                                                              "tier_confirmed": "yes"}}))
    assert r.plan_tier == "growth" and r.confirmed is None


def test_payload_without_run_id_is_rejected():
    with pytest.raises(ValueError):
        map_call_analyzed(payload(metadata={}))


# ---------- start call ----------

@respx.mock
def test_start_call_posts_expected_body():
    route = respx.post(f"{RETELL_BASE}/v2/create-phone-call").mock(
        return_value=httpx.Response(201, json={"call_id": "call_1", "call_status": "registered"}))
    v = RetellVoice("api-key", "agent_9", "+14155550123")
    out = v.start_call(VoiceStartIn(to_number="+919876543210", ae_name="Jai", customer_name="Globex",
                                    run_id="run-1", attempt=2))
    assert out.provider_call_id == "call_1"
    req = route.calls.last.request
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer api-key"
    assert body == {"from_number": "+14155550123", "to_number": "+919876543210", "override_agent_id": "agent_9",
                    "metadata": {"run_id": "run-1", "attempt": 2},
                    "retell_llm_dynamic_variables": {"ae_name": "Jai", "customer_name": "Globex"}}


@respx.mock
@pytest.mark.parametrize("status,exc", [(500, TransientToolError), (429, TransientToolError),
                                        (400, PermanentToolError), (402, PermanentToolError)])
def test_start_call_error_mapping(status, exc):
    respx.post(f"{RETELL_BASE}/v2/create-phone-call").mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(exc):
        RetellVoice("k", "a", "+1").start_call(VoiceStartIn(to_number="+2", ae_name="x", customer_name="y",
                                                            run_id="r", attempt=1))
