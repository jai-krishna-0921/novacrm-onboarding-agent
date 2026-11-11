# Retell AI API reference (outbound plan-tier confirmation call)

Compiled 2026-11-11 from docs.retellai.com (OpenAPI spec revision `2026-11-10-114095e`, SDK `retell-sdk` 6.2.19).
Base URL: `https://api.retellai.com`. Auth: `Authorization: Bearer $RETELL_API_KEY`.
Items marked **UNVERIFIED** were not confirmed on an official page.

## 1. Create phone call

`POST https://api.retellai.com/v2/create-phone-call` (operationId `createPhoneCall`). Success = **HTTP 201**.
Source: https://docs.retellai.com/api-references/create-phone-call, https://docs.retellai.com/deploy/outbound-call

| Field | Req? | Notes (from spec) |
|---|---|---|
| `from_number` | **required** | E.164. Must be purchased from or imported to Retell. |
| `to_number` | **required** | E.164. Spec text: "If using a number purchased from Retell, only US numbers are supported as destination." (contradicted by the International page, see section 7) |
| `override_agent_id` | optional | One-time agent override; does not bind agent to the number. Otherwise the number's bound **Outbound Agent** is used. |
| `override_agent_version` | optional | int, or tag string (`latest`, `latest_published`, `prod`...). |
| `agent_override` | optional | `{agent: {...}, retell_llm: {...}, conversation_flow: {...}}` per-call partial config. |
| `metadata` | optional | Arbitrary object, "storage purpose only", returned on the call object/webhooks. "size limit applies" (limit not stated). |
| `retell_llm_dynamic_variables` | optional | `{string: string}` only; injected into prompt as `{{var}}`. |
| `idempotency_key` | optional | 5-255 chars `^[A-Za-z0-9_-]+$`; replays original response (same `call_id`) for 1 hour. |
| `custom_sip_headers` | optional | `{string: string}` |
| `ignore_e164_validation` | optional | custom telephony only |
| `honor_internal_dnc` | optional | 400 if contact is marked do_not_call |

```bash
curl --request POST 'https://api.retellai.com/v2/create-phone-call' \
  --header "Authorization: Bearer $RETELL_API_KEY" \
  --header 'Content-Type: application/json' \
  --data '{
    "from_number": "+14155550100",
    "to_number": "+14155550123",
    "idempotency_key": "appointment-reminder-20261027-001"
  }'
```

**Response** (`V2PhoneCallResponse`): the full call object (same shape as Get Call), not just two fields. Required props:
`call_type` (`"phone_call"`), `from_number`, `to_number`, `direction`; plus `call_id`, `agent_id`, `call_status`
(normally `"registered"` at creation), `metadata`, `retell_llm_dynamic_variables`, etc.

Errors: 400, 401, 402 (PaymentRequired), 409 (newly purchased number 2-minute outbound hold; retry after given time), 422, 429, 500.
Rate limit: call creation 1000 / 10s plus 60 4xx errors / min (https://docs.retellai.com/get-started/sdk).
Prerequisite: account **KYC verification** before any outbound call (https://docs.retellai.com/accounts/kyc).

## 2. Get call

`GET https://api.retellai.com/v2/get-call/{call_id}` (operationId `getCall`).
Source: https://docs.retellai.com/api-references/get-call, https://docs.retellai.com/reliability/debug-call-disconnect

Relevant fields: `call_id`, `call_status`, `disconnection_reason`, `start_timestamp`, `end_timestamp` (ms epoch),
`duration_ms`, `transcript` (string, "Agent: ...\nUser: ..."), `transcript_object`, `transcript_with_tool_calls`,
`metadata`, `retell_llm_dynamic_variables`, `collected_dynamic_variables`, `call_analysis`, `recording_url`.

`call_status` enum: `registered | not_connected | ongoing | ended | error`.

`disconnection_reason` full enum (with resulting `call_status` per debug-call-disconnect page):

| ended | not_connected | error |
|---|---|---|
| `user_hangup`, `agent_hangup`, `user_requested_dnc`, `user_requested_callback`*, `call_transfer`, `transfer_bridged`, `transfer_cancelled`, `call_take_over`, `voicemail_reached`, `ivr_reached`, `inactivity`, `max_duration_reached`, `no_concurrency_fallback`, `manual_stopped`* | `dial_busy`, `dial_failed`, `dial_no_answer`, `invalid_destination`, `telephony_provider_permission_denied`, `telephony_provider_unavailable`, `sip_routing_error`, `marked_as_spam`, `network_blocked`, `user_declined` | `concurrency_limit_reached`, `no_valid_payment`, `credit_exhausted`, `budget_reached`, `scam_detected`, `error_llm_websocket_open`, `error_llm_websocket_lost_connection`, `error_llm_websocket_runtime`, `error_llm_websocket_corrupt_payload`, `error_no_audio_received`, `error_asr`, `error_retell`, `error_unknown`, `error_user_not_joined`, `registered_call_timeout` |

\* in the OpenAPI enum; status column for `user_requested_callback`, `manual_stopped`, `error_no_audio_received` and later `error_*` rows **UNVERIFIED** (not in the portion of the table read).

`call_analysis` (`CallAnalysis`): `call_summary` (string), `in_voicemail` (bool), `user_sentiment`
(`Negative|Positive|Neutral|Unknown`), `call_successful` (bool), `custom_analysis_data` (object).

## 3. Webhooks

Sources: https://docs.retellai.com/features/webhook-overview, https://docs.retellai.com/features/secure-webhook,
https://docs.retellai.com/features/register-webhook

Voice events: `call_started`, `call_ended`, `call_analyzed`, `transcript_updated`, `transfer_started`,
`transfer_bridged`, `transfer_cancelled`, `transfer_ended`. Agent default (`webhook_events` unset):
`call_started`, `call_ended`, `call_analyzed`.

- `call_ended`: all call-object fields **except `call_analysis`**.
- `call_analyzed`: full call object **including `call_analysis`**. Read analysis only from this event.
- Calls that never connect (`dial_failed`, `dial_no_answer`, `dial_busy`) get **no `call_started`**, but still get `call_ended` and `call_analyzed`.

Registration: account-level (dashboard Webhooks tab) or agent-level `webhook_url` on create/update agent
(agent-level replaces account-level for that agent). URL may contain `{{dynamic_vars}}`. Must be publicly reachable
(localhost/private IPs blocked; use ngrok for dev).

Payload = `{"event": ..., "call": <call object>}`. `metadata` and `retell_llm_dynamic_variables` appear at
`call.metadata` / `call.retell_llm_dynamic_variables` (omitted if not provided at creation). Sample from docs:

```json
{
  "event": "call_analyzed",
  "call": {
    "call_type": "phone_call",
    "from_number": "+12137771234",
    "to_number": "+12137771235",
    "direction": "inbound",
    "call_id": "Jabr9TXYYJHfvl6Syypi88rdAHYHmcq6",
    "agent_id": "oBeDLoLOeuAbiuaMFXRtDOLriTJ5tSxD",
    "call_status": "ended",
    "metadata": {},
    "retell_llm_dynamic_variables": { "customer_name": "John Doe" },
    "start_timestamp": 1714608475945,
    "end_timestamp": 1714608491736,
    "disconnection_reason": "user_hangup",
    "transcript": "...",
    "transcript_object": [ ... ],
    "transcript_with_tool_calls": [ ... ],
    "call_analysis": {
      "call_summary": "The agent called the user to ask about their purchase inquiry. ...",
      "in_voicemail": false,
      "user_sentiment": "Positive",
      "call_successful": true,
      "custom_analysis_data": { "appointment_booked": true }
    }
  }
}
```

**Delivery/retries**: POST; timeout **10 s** (agent field `webhook_timeout_ms`, default 10000); if no 2xx within the
timeout, **retried up to 3 times**. Delivered in order but non-blocking. Expect duplicates: dedupe on
`event` + `call_id` (lifecycle events fire at most once per call). Respond 2xx (204, no body) fast; do work async.

**Signature**: header `X-Retell-Signature` (docs also write `x-retell-signature`; HTTP headers are case-insensitive).
Format `v={timestamp_ms},d={hex_digest}`, where digest = `HMAC-SHA256(raw_body + timestamp, api_key)`; reject if
timestamp is more than 5 min off. Only the API key with the **webhook badge** in the dashboard can verify.
Optional IP allowlist: `100.20.5.228`. Use the **raw** body, never re-serialized JSON.

```python
from retell import Retell
retell = Retell(api_key=os.environ["RETELL_API_KEY"])

raw_body = (await request.body()).decode("utf-8")
valid = retell.verify(
    raw_body,
    api_key=str(os.environ["RETELL_API_KEY"]),
    signature=str(request.headers.get("X-Retell-Signature")),
)
if not valid:
    return JSONResponse(status_code=401, content={"message": "Unauthorized"})
post_data = json.loads(raw_body)
```

Python helper as documented: instance method `retell.verify(body: str, api_key: str, signature: str) -> bool`
(exact type hints / sync-vs-async in the SDK source **UNVERIFIED**; Node uses static `await Retell.verify(...)`).
Manual fallback (no SDK), per docs algorithm:

```python
import hmac, hashlib, re, time
def verify(raw_body: str, api_key: str, sig: str) -> bool:
    m = re.match(r"v=(\d+),d=(.*)", sig or "")
    if not m or abs(time.time()*1000 - int(m[1])) > 5*60*1000: return False
    exp = hmac.new(api_key.encode(), (raw_body + m[1]).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, m[2])
```

## 4. Post-call analysis (custom fields)

Sources: https://docs.retellai.com/api-references/create-agent, https://docs.retellai.com/features/post-call-analysis-create,
https://docs.retellai.com/features/post-call-analysis-consumption

Agent field `post_call_analysis_data`: array of items, types `string`, `enum`, `boolean`, `number`
(dashboard labels: Text, Selector, Boolean, Number). Model via `post_call_analysis_model`.
Set on `POST /create-agent` or `PATCH /update-agent/{agent_id}` (no `/v2` prefix on these).

- `EnumAnalysisData`: required `type: "enum"`, `name`, `description`, `choices` (non-empty string array); optional `required`, `conditional_prompt`.
- `BooleanAnalysisData`: required `type: "boolean"`, `name`, `description`; optional `required`, `conditional_prompt`.
- `required: true` and not extracted means call marked unsuccessful. Explain choices in `description`; `choices` holds only the values.

```json
"post_call_analysis_data": [
  {"type": "enum", "name": "plan_tier", "description": "Plan tier the rep confirmed.",
   "choices": ["enterprise", "growth"]},
  {"type": "boolean", "name": "user_reached", "description": "Was the user reached or not? Set to false if voicemail is detected ..."}
]
```
(Shape from the schema; `plan_tier` is an illustrative name. `user_reached` description is the docs example.)

Output: `call.call_analysis.custom_analysis_data.<name>` in the `call_analyzed` webhook and Get Call.
**Custom fields are NOT populated for calls that did not connect or had no conversation**, so check key existence.

## 5. Voicemail detection and max duration

Sources: https://docs.retellai.com/build/handle-voicemail, https://docs.retellai.com/api-references/create-agent

- `voicemail_option`: `{"action": VoicemailAction, "detection_prompt": str|null}`; `null` disables. Actions:
  `{"type":"hangup"}`, `{"type":"static_text","text":"..."}`, `{"type":"prompt","text":"..."}`, plus a bridge-transfer variant.
  Example: `{"action": {"type": "static_text", "text": "Please give us a callback tomorrow at 10am."}}`.
- Detection runs only in the **first 3 minutes** after connect (not configurable). Outbound and web calls only.
- Outcome: `disconnection_reason = "voicemail_reached"` (hangup), `call_analysis.in_voicemail`.
- API-created agents: set `voicemail_option` explicitly (the dashboard default is hang up).
- `ivr_option` (`{"action":{"type":"hangup"}}`) handles IVR. Calls classified as IVR end with `ivr_reached`, not `voicemail_reached`.
- `max_call_duration_ms`: min 60000, max 7200000, **default 3600000 (1 h)**. Outcome `max_duration_reached`.
- `end_call_after_silence_ms`: min 10000, default 600000. Outcome `inactivity`.
- Ring timeout field `ring_duration_ms` exists on the agent (range/default not read: **UNVERIFIED**).

## 6. Python SDK

Source: https://docs.retellai.com/get-started/sdk
- PyPI package: **`retell-sdk`** (`pip install retell-sdk`), import `from retell import Retell`. Python >= 3.9. Current 6.2.19.
- Client: `client = Retell(api_key=os.environ["RETELL_API_KEY"])` (env var is read by default).
- Create call: **`client.call.create_phone_call(from_number=..., to_number=..., override_agent_id=..., metadata=..., retell_llm_dynamic_variables=...)`**.
- Get call: `client.call.retrieve("119c3f8e47135a29e65947eeb34cf12d")` (from get-call spec code sample).
- Async client: docs say "async support"; class name (`AsyncRetell`) **UNVERIFIED**.

## 7. International outbound (US Retell number to India +91), pricing

Sources: https://docs.retellai.com/deploy/international-call, https://docs.retellai.com/deploy/outbound-call,
https://docs.retellai.com/reliability/fraud-protection, https://docs.retellai.com/accounts/billing, https://www.retellai.com/pricing

- International page: Retell-managed numbers can call **15 countries** incl. **India**, using the normal outbound flow.
  It even gives "a US-based support team can use a Retell number to reach customers in India" as an example.
  Rates/min: Twilio India **$0.15**, Telnyx India **$0.25** (US $0.015 Twilio / $0.03 Telnyx).
- **Conflict**: the create-phone-call spec still says Retell-purchased numbers support only US destinations. Do one test call to India before relying on it.
- Per-number `allowed_outbound_country_list` (ISO codes, via `PATCH /update-phone-number/{number}`): unset or empty allows all; if set, it must include `"IN"`. Sanctioned countries are always blocked.
- Imported numbers: international ability depends on your telephony provider.
- KYC must pass before outbound calling. Indian carrier spam/DND filtering of US caller IDs: **UNVERIFIED**.
- Billing: new accounts get **$10 free trial credits** (once per email). Usage is prepaid credits. At $0 balance, calls stop (`credit_exhausted`).
  Payment method needed beyond trial / to buy numbers (https://docs.retellai.com/accounts/add-payment).
- Pricing page (marketing site): voice agents **$0.07-$0.31/min** pay-as-you-go. Example total $0.11/min = Retell Voice Infra $0.055 + TTS $0.015 + LLM $0.04 (+ telephony).
  Retell phone number $2.00/month. 20 concurrent calls free. These figures change often, so recheck before quoting.
