# Gmail API + Pub/Sub push reference (deal-email intake)

Collected 2026-11-11 from developers.google.com/workspace/gmail, docs.cloud.google.com/pubsub, Google OAuth docs and PyPI. Signatures were checked by importing the current library versions. Anything marked **UNVERIFIED** is not on an official page, or is my inference; check it against the live API before relying on it.

## 1. Libraries, OAuth and refresh tokens

**PyPI latest as of 2026-11-11** (source: https://pypi.org/pypi/<name>/json). All four require Python >=3.10.

| Package | Version | Released |
|---|---|---|
| google-api-python-client | 2.202.0 | 2026-11-10 |
| google-auth | 2.62.2 | 2026-11-10 |
| google-auth-oauthlib | 1.5.0 | 2026-09-29 |
| google-cloud-pubsub (only for admin scripts; the webhook doesn't need it) | 2.43.0 | 2026-10-15 |

The quickstart also installs `google-auth-httplib2`.

**Setting up the Desktop-app client.** Source: https://developers.google.com/workspace/gmail/api/quickstart/python. The Cloud console now calls the consent-screen area **"Google Auth Platform"**, with pages named Branding, Audience, Data Access and Clients.
Branding (app name, support email) → Audience: **External**, status **Testing**, add the Gmail address as a **test user** (max 100) → Data Access: add Gmail scopes → Clients: Create Client → **Desktop app** → download JSON.
The client form has a new "Use this client for an AI-powered agent" checkbox, and it **cannot be changed later**. Leave it unchecked: it only matters for Google's Workspace MCP servers, not the REST API (see §7).

**One-time consent** (run as a script on a laptop, never inside the server):
```python
from google_auth_oauthlib.flow import InstalledAppFlow
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)      # opens a browser; port=0 picks a free port (library default is 8080)
print(creds.refresh_token)                  # store as GMAIL_REFRESH_TOKEN in .env, never log it
```
The quickstart persists everything with `creds.to_json()` → `token.json` and reloads it with `Credentials.from_authorized_user_file`. Installed-app flows normally return a refresh token on first consent. If one comes back as `None`, revoke the app at myaccount.google.com/permissions and run the flow again (**UNVERIFIED** remedy).

**Server side: building Credentials from env.** Signature checked against google-auth 2.62.2: `Credentials(token, refresh_token=None, id_token=None, token_uri=None, client_id=None, client_secret=None, scopes=None, ...)`.
```python
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
creds = Credentials(None, refresh_token=RT, token_uri="https://oauth2.googleapis.com/token",
                    client_id=CID, client_secret=CSECRET, scopes=SCOPES)
creds.refresh(Request())                     # optional; the client refreshes on first call
gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
```

**Refresh-token expiry still applies in Testing.** Sources: https://developers.google.com/identity/protocols/oauth2 and https://support.google.com/cloud/answer/15549945.
- If the project is External with publishing status **Testing**, refresh tokens **expire after 7 days**. The only exception is when you request nothing beyond name, email and openid scopes, and Gmail scopes don't qualify.
- **Expect to re-consent weekly.** A refresh that fails with `invalid_grant` means re-run the consent script. The system should catch this and escalate rather than crash.
- Other ways a token dies: unused for 6 months; password change while it holds Gmail scopes; >100 refresh tokens per account per client (oldest silently invalidated).

## 2. Scopes

Source: https://developers.google.com/workspace/gmail/api/auth/scopes

| Scope | Class | Enough for |
|---|---|---|
| `gmail.readonly` | **Restricted** | watch, history.list, messages.get |
| `gmail.send` | Sensitive | messages.send only |
| `gmail.modify` | **Restricted** | read + send + label changes (no permanent delete) |
| `gmail.compose`, `gmail.metadata`, `https://mail.google.com/` | Restricted | — |
| `gmail.labels` | Non-sensitive | label management only |

- `watch` and `history.list` accept any of `mail.google.com`, `gmail.modify`, `gmail.readonly` or `gmail.metadata`. `metadata` can't fetch bodies (`format=full` is disallowed), so it isn't enough here.
- `messages.send` accepts `mail.google.com`, `gmail.modify`, `gmail.compose` or `gmail.send`.
- **Choose `gmail.modify` alone** if you also want to mark messages processed with a label. Otherwise use `gmail.readonly` + `gmail.send`. Either combination includes a restricted scope.
- What "restricted" means: a **production** app needs restricted-scope verification, plus a security assessment if it stores restricted-scope data on servers.
- In **Testing** the app is limited to its listed test users. Each user sees an "unverified app" warning, and tokens expire after 7 days. Google doesn't list verification as a requirement for Testing status. Fine for a demo; not shippable to real customers without verification.

## 3. users.watch

Sources: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/watch and https://developers.google.com/workspace/gmail/api/guides/push

`POST https://gmail.googleapis.com/gmail/v1/users/me/watch`
```json
{"topicName": "projects/<proj>/topics/<topic>", "labelIds": ["INBOX"], "labelFilterBehavior": "INCLUDE"}
```

**Request fields:** `topicName` (required; topic must exist, in **the same project as the OAuth client**); `labelIds[]` (optional; omit = push on every change); `labelFilterBehavior` (**current**, enum `INCLUDE`/`EXCLUDE`). `labelFilterAction` is **deprecated** ("caused incorrect behavior in some cases") and ignored when `labelFilterBehavior` is set; don't send it.

**Response:** `{"historyId": "<string>", "expiration": "<epoch ms string>"}`. Persist the `historyId` as the starting cursor.

**Renewal:**
- You must call `watch` at least once every **7 days**. Google recommends **once per day** (cron).
- Calling `watch` again renews the watch; no `stop` is needed first.
- `POST users/me/stop` ends notifications within a few minutes.

**IAM:** grant `gmail-api-push@system.gserviceaccount.com` **publish** rights on the topic. The doc only says "publish privileges"; in practice that means the `roles/pubsub.publisher` role (**UNVERIFIED** role name):
```
gcloud pubsub topics add-iam-policy-binding <topic> \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com --role=roles/pubsub.publisher
```
If your org enforces Domain Restricted Sharing (`iam.allowedPolicyMemberDomains`), the binding is rejected. A personal project has no org policy, so this should be fine (**UNVERIFIED** for your project).

**Notification rate:** at most **1 event/sec per watched user**. Google **drops** anything above that, so one notification can stand for several messages. Always drain `history.list`, never assume one push equals one message. Don't let your own writes (labeling, the reply you send) trigger an endless notification loop.

## 4. Pub/Sub push subscription → FastAPI

Sources: https://docs.cloud.google.com/pubsub/docs/push, https://docs.cloud.google.com/pubsub/docs/subscription-properties, https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions, https://developers.google.com/workspace/gmail/api/guides/push

**Request body** (wrapped format, the default):
```json
{"message": {"data": "<base64>", "attributes": {}, "messageId": "136969346945", "message_id": "136969346945",
             "publishTime": "2026-11-11T10:00:00.000Z", "publish_time": "2026-11-11T10:00:00.000Z"},
 "subscription": "projects/<proj>/subscriptions/<sub>", "deliveryAttempt": 1}
```
- `deliveryAttempt` appears only when a dead-letter policy is set.
- `data` decodes to `{"emailAddress": "user@gmail.com", "historyId": "9876543210"}`.
- The Gmail guide calls the encoding "Base64URL" while Pub/Sub says base64. Use a decoder that accepts both, e.g. `base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))` (**UNVERIFIED** which alphabet is actually sent).
- `messageId` is the Pub/Sub ID, unrelated to the Gmail message ID.
- The notification's `historyId` is the mailbox's *new* history ID. Fetch changes from your **stored** cursor, not from this value.

**Ack semantics:**
- Return **102, 200, 201, 202 or 204** to ack. Any other status is a nack, and Pub/Sub redelivers.
- The ack deadline defaults to **10 s** (max 600 s). Push can't extend the deadline per message.
- **Ack fast:** persist `{historyId}`, return 204, and process asynchronously. Don't call the LLM or Retell inside the request.

**Retries and delivery:** retry policy is immediate by default, or exponential backoff (min 10 s / max 600 s defaults). Separately, push backoff pauses delivery 100 ms–60 s after failures (slow-start window). Dead-letter max attempts default 5 (range 5–100). Retention defaults to 7 days. A subscription expires after **31 days without activity**; set `--expiration-period=never`.
- Redelivery and duplicates happen, so dedupe on Gmail message `id`. The push page doesn't say "at-least-once" explicitly; that's standard Pub/Sub semantics.

**Authenticating pushes (recommended: OIDC).**
- Create the subscription with `--push-auth-service-account=<sa-email>` and optionally `--push-auth-token-audience=<aud>`.
- Pub/Sub then sends `Authorization: Bearer <Google-signed JWT>`.
- The creator needs `iam.serviceAccounts.actAs` on that service account.
- The doc also says the signing identity needs `iam.serviceAccounts.getOpenIdToken` / `roles/iam.serviceAccountTokenCreator`. **UNVERIFIED** whether that still applies to new projects; grant it if token signing fails.

Verify the token:
```python
from google.oauth2 import id_token
from google.auth.transport import requests as g_requests
claims = id_token.verify_oauth2_token(bearer, g_requests.Request(), audience=EXPECTED_AUD)  # checks sig, aud, exp
if claims.get("email") != PUSH_SA_EMAIL or not claims.get("email_verified"):
    raise HTTPException(401)
```
- The signature is `verify_oauth2_token(id_token, request, audience=None, clock_skew_in_seconds=0)`. It also checks the issuer is accounts.google.com.
- Set the audience explicitly (e.g. the ngrok URL or a fixed string) and check it. The doc doesn't state a default audience; I believe it defaults to the endpoint URL (**UNVERIFIED**).
- Google's own Python sample *also* checks a shared-secret `?token=` query param. That's an app-level extra, not a substitute for OIDC: query strings end up in logs. OIDC alone is fine; add the token if you want defense in depth against a rotating ngrok URL.
- The endpoint must be publicly reachable HTTPS. ngrok's certificate is publicly trusted.

```
gcloud pubsub subscriptions create gmail-push --topic=<topic> --push-endpoint=https://<ngrok>/webhooks/gmail \
  --push-auth-service-account=<sa>@<proj>.iam.gserviceaccount.com --push-auth-token-audience=<aud> \
  --ack-deadline=10 --expiration-period=never
```
When the ngrok URL changes, run `gcloud pubsub subscriptions modify-push-config gmail-push --push-endpoint=<new> ...` with the same auth flags.

**Billing** (source: https://cloud.google.com/pubsub/pricing):
- The first **10 GiB/month** of Message Delivery Basic throughput per **billing account** is free; after that it's $40/TiB.
- Because the allowance is per billing account, assume you must **link a billing account** to enable Pub/Sub. **UNVERIFIED** whether it works without one.
- Gmail notifications are tiny; the cost is effectively zero.
- **UNVERIFIED:** the minimum billable size per message (historically 1 KB) isn't confirmed on the current page.

## 5. Fetching new messages

Sources: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list, https://developers.google.com/workspace/gmail/api/guides/sync, https://developers.google.com/workspace/gmail/api/reference/rest/v1/Format

**`GET users/me/history`:**
- Params: `startHistoryId` (required), `historyTypes=messageAdded` (enum also has `messageDeleted`, `labelAdded`, `labelRemoved`), `labelId=INBOX`, `maxResults` (default 100, max 500), `pageToken`.
- Response: `history[]`. Each record has `id`, `messages[]`, `messagesAdded[]`, `messagesDeleted[]`, `labelsAdded[]` and `labelsRemoved[]`.
- Inside `messagesAdded[].message`, usually only `id` and `threadId` are filled in.
- Also returns `nextPageToken` and the mailbox's current `historyId`.
- Page until `nextPageToken` is absent, then store the response `historyId` as the new cursor.
- Advance the cursor only after the work is durably queued. Use a DB transaction and dedupe on message `id`.

**404 = cursor too old or invalid → full sync.**
- A `historyId` is typically valid for **at least a week**, sometimes only hours.
- Fallback: `messages.list` (e.g. `q="newer_than:2d"`, `labelIds=INBOX`) → `messages.get` each one, skipping IDs already processed. Then reset the cursor from the newest message's `historyId`, or call `watch` again and use its `historyId`.

**`GET users/me/messages/{id}?format=full`:**
- Formats: `minimal` (id + labels), `full` (parsed `payload`), `raw` (base64url RFC 822 in `raw`), `metadata` (headers). `full`/`raw` not allowed with `gmail.metadata`.
- Message fields: `id`, `threadId`, `labelIds`, `snippet`, `historyId`, `internalDate`, `payload`, `sizeEstimate`, `raw`.
- `payload` is a MessagePart: `partId`, `mimeType`, `filename`, `headers[{name,value}]`, `body{attachmentId,size,data}`, `parts[]`.
- `body.data` is base64url. Large parts have only `attachmentId`; fetch those with `messages.attachments.get`.

Decoding:
```python
def _b64(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
def extract_text(part):           # depth-first; prefer text/plain, fall back to stripped text/html
    plain, html = [], []
    def walk(p):
        mt, data = p.get("mimeType", ""), p.get("body", {}).get("data")
        if mt == "text/plain" and data and not p.get("filename"): plain.append(_b64(data))
        elif mt == "text/html" and data and not p.get("filename"): html.append(_b64(data))
        for c in p.get("parts", []) or []: walk(c)
    walk(part); return "\n".join(plain) if plain else html_to_text("\n".join(html))
```
- Decoding as UTF-8 assumes UTF-8. The real charset is in the part's `Content-Type` header.
- Simpler and more robust: use `format=raw` and `email.message_from_bytes(raw, policy=email.policy.default).get_body(("plain","html"))`.
- Keep headers `Message-ID`, `Subject`, `From`, `References` for the reply.
- **Quota** (source: https://developers.google.com/workspace/gmail/api/reference/quota): `messages.get` costs 20 units, or **60 for `full`/`raw`**. history.list costs 2, messages.list 5, send 100, watch 100. Limit: 6,000 units/min per user.

## 6. Replying in-thread

Sources: https://developers.google.com/workspace/gmail/api/guides/threads, https://developers.google.com/workspace/gmail/api/guides/sending, https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send

A reply joins the thread only if all three hold:
1. `threadId` is set on the Message you send.
2. `In-Reply-To` and `References` follow RFC 2822.
3. The `Subject` matches.

```python
import base64
from email.message import EmailMessage
def build_reply(orig_headers: dict, thread_id: str, to: str, body: str, sender: str) -> dict:
    msg = EmailMessage()
    msg["To"], msg["From"] = to, sender
    subj = orig_headers["Subject"]
    msg["Subject"] = subj if subj.lower().startswith("re:") else f"Re: {subj}"
    mid = orig_headers["Message-ID"]                       # the RFC 822 Message-ID header, NOT the Gmail id
    msg["In-Reply-To"] = mid
    msg["References"] = (orig_headers.get("References", "") + " " + mid).strip()
    msg.set_content(body)
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(), "threadId": thread_id}
gmail.users().messages().send(userId="me", body=build_reply(...)).execute()
```
- The doc says the subjects must "match". Gmail tolerates a `Re:` prefix (**UNVERIFIED** exact rule); keeping the original subject verbatim with `Re: ` prepended is the safe choice.
- Header names in `payload.headers` vary in case (`Message-Id` vs `Message-ID`), so look them up case-insensitively.
- Make sends idempotent: record the send in the DB before or atomically with the call. Pub/Sub redelivery and graph retries must not double-email the AE.

## 7. 2025–2026 changes that matter

Source: https://developers.google.com/workspace/gmail/api/release-notes, plus the pages cited above.
- **`labelFilterAction` is deprecated** in favor of `labelFilterBehavior`. That matches your assumption: use `labelFilterBehavior`.
- **Quotas (2026-05-01):** new tiering model; `messages.get`/`threads.get` etc. cost more (`full`/`raw` = 60). Applies to projects created on/after 2026-05-01. Daily threshold 80M units/project, usage **charges "planned for later in 2026"** with ≥90 days' notice. Irrelevant at our volume.
- **Agentic OAuth clients (2026-10-12):** new immutable client checkbox "Use this client for an AI-powered agent". From 2026-11-11 Workspace **MCP servers** reject clients without it; **REST-only apps are unaffected**, so leave it unchecked.
- Console rename: "OAuth consent screen" is now **Google Auth Platform** (Branding / Audience / Data Access / Clients).
- Testing-mode 7-day refresh-token expiry: **still in force**.
- No 2025–2026 changes to watch, history, the push payload or threading were found.
- Consumer Gmail is deprecating Gmailify/POP and third-party "Send as" (Jan 2027). This doesn't affect the API.
