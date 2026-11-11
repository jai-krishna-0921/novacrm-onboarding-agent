# Slack Web API reference (for the onboarding channel flow)

Collected 2026-11-11 from docs.slack.dev (the current home of the Slack API docs; api.slack.com URLs redirect there), PyPI and slack.com help pages. Anything marked **UNVERIFIED** is not stated on an official page and must be checked against a live workspace before relying on it.

Flow: `conversations.create` → `conversations.setTopic` → `conversations.invite` (internal) → `conversations.inviteShared` (customer, Slack Connect) → `chat.postMessage`.

## 1. slack_sdk (Python)

Source: https://pypi.org/project/slack-sdk/ , https://docs.slack.dev/tools/python-slack-sdk/web
- Latest: **slack-sdk 3.46.0** (released 2026-10-30). `requires_python >=3.7`; classifiers list 3.7–3.15, so 3.12 is fine.
- Construction: `WebClient(token=SLACK_BOT_TOKEN)`. Every method is `client.<family>_<method>(...)`, e.g. `client.conversations_create(name=..., is_private=False)`.
- Errors: when the response has `"ok": false`, the call raises `slack_sdk.errors.SlackApiError`. Read the code from `e.response["error"]` (a string such as `"name_taken"`). Always branch on that string, not on the exception message.
- Rate limits: Slack returns **HTTP 429** with a `Retry-After` header in seconds (`int(e.response.headers["Retry-After"])`). The error string is `ratelimited`.
- Built-in retry (since v3.9.0):
  ```python
  from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
  client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=1))
  ```
  This handler sleeps for `Retry-After` and retries. You can also pass `retry_handlers=[...]` to the constructor. **By default only `ConnectionErrorRetryHandler` is on** (one retry, exponential backoff with jitter), so 429s are *not* retried unless you add the handler.
- Slack warns that apps that keep calling after being rate limited "run the risk of being permanently disabled" (https://docs.slack.dev/apis/web-api/rate-limits).

## 2. conversations.create

Source: https://docs.slack.dev/reference/methods/conversations.create
- Bot scopes (any one works for the matching channel type): `channels:manage` (public), `groups:write` (private), plus `im:write` / `mpim:write`. `channels:write` is also listed. Rate limit: **Tier 2** (~20/min).
- Args: `name` (required), `is_private` (bool, optional), `team_id` (only needed for org-level tokens; ignored for workspace tokens).
- Name rules: **lowercase letters, numbers, hyphens and underscores only, max 80 characters**. Slack always validates the name and does not auto-fix it, so normalize in code first. Store both the returned `channel.id` and `channel.name`.
- Errors that matter for this flow: `name_taken`, `invalid_name`, `invalid_name_maxlength`, `invalid_name_punctuation` (name is only punctuation), `invalid_name_required` / `no_channel` (empty), `invalid_name_specials` (special or uppercase chars), `restricted_action` (a workspace pref blocks channel creation), `cannot_create_channel`, `missing_scope`, `ratelimited`. `internal_error` and `fatal_error` "may have partially succeeded", so look the channel up before retrying.
- The bot that creates the channel is a member of it (**UNVERIFIED** on this page, but setTopic, invite and inviteShared all require membership, and this is the standard behaviour).

## 3. Topic, internal invites, welcome message, duplicate lookup

**conversations.setTopic** (https://docs.slack.dev/reference/methods/conversations.setTopic)
- Bot scopes: `channels:manage` or `channels:write.topic` (public); `groups:write` or `groups:write.topic` (private). Rate limit: Tier 2.
- Args: `channel` (ID), `topic`. **Max 250 chars** (`too_long`). No formatting or links.
- Errors: `channel_not_found`, `not_in_channel`, `is_archived`, `user_is_restricted`, `too_long`, `method_not_supported_for_channel_type`.

**conversations.invite** (https://docs.slack.dev/reference/methods/conversations.invite)
- Bot scopes: `channels:manage` or `channels:write.invites` (public); `groups:write` or `groups:write.invites` (private). Rate limit: **Tier 3** (~50/min).
- Args: `channel`, `users` (comma-separated user IDs, 1–1000), `force` (bool, default false: when true, skips invalid IDs and invites the valid ones).
- Without `force`, one bad ID can block the whole call. Errors: `already_in_channel` (treat as success for idempotency), `cant_invite_self`, `user_not_found`, `not_in_channel`, `channel_not_found`, `is_archived`, `user_is_restricted`, `ura_max_channels`, `no_permission`.
- Internal users need Slack **user IDs**, not emails. Resolving an email to an ID uses `users.lookupByEmail` (scope `users:read.email`). That method was not re-checked for this doc (**UNVERIFIED**).

**chat.postMessage** (https://docs.slack.dev/reference/methods/chat.postMessage)
- Bot scope: `chat:write` (`chat:write.public` only if posting to public channels the bot is not in; `chat:write.customize` to change the username/icon).
- Rate limit: special tier, about **1 message/sec per channel** with short bursts allowed.
- Args: `channel`, `text`, `blocks` (Block Kit JSON array), `mrkdwn` (default true), `unfurl_links` / `unfurl_media`, `thread_ts`.
- When `blocks` is given, `text` becomes the notification and screen-reader fallback. It is not enforced, but Slack recommends it, so always send both. Keep `text` to about 4,000 chars. Messages over 40,000 chars are truncated (warning `message_truncated`).
- Errors: `no_text`, `invalid_blocks`, `invalid_blocks_format`, `msg_blocks_too_long`, `channel_not_found`, `not_in_channel`, `is_archived`, `restricted_action_read_only_channel`.

**Duplicate detection via conversations.list** (https://docs.slack.dev/reference/methods/conversations.list)
- Bot scopes: `channels:read` (public), `groups:read` (private). Rate limit: Tier 2.
- Args: `types` (`public_channel,private_channel`), `exclude_archived`, `limit` (<1000, default 100, docs recommend ≤200), `cursor`, `team_id`.
- There is **no name filter**, so page through with `response_metadata.next_cursor` and match `name` in code. Pages can come back with fewer items than `limit` even when more remain, so keep going until the cursor is empty.
- Private channels only show up if the bot is a member. Archived channels still hold their name, so a `name_taken` on create can point to an archived channel.
- Recommended: persist `channel_id` in our DB as the primary idempotency key. Use list or `name_taken` only as a fallback.

## 4. Slack Connect: conversations.inviteShared

Source: https://docs.slack.dev/reference/methods/conversations.inviteShared
- Bot scope: **`conversations.connect:write`**. No user-token scope is listed. Rate limit: Tier 2, applied per user when `emails` is used.
- Args: `channel` (required), `emails` (array) **or** `user_ids` (array). **Only one email or one user ID per call**, and bots must specify a recipient (`recipients_not_specified`). `external_limited` (bool, **default true**): when true, the invitee can only send messages. They cannot invite others, export history, change visibility, or rename or set the topic, and the channel shows as private in their workspace.
- Success response: `{"ok": true, "invite_id": "I02UKAJ6RJA", "is_legacy_shared_channel": false}`. `conf_code` and `url` come back only for link invites (`external_limited=false` and no specific recipient).
- The doc says "The invited user must be known to your app", meaning a channel is already shared between the two orgs. It is **UNVERIFIED** whether this restricts bot invites by `emails` to a brand-new external org. Test this early. If it fails, the fallback is a human sending the invite from the Slack UI.
- The app must be in the channel (`not_in_channel` / `no_permission`). Only the host org can share (`not_owner`). Inviting someone from another org turns a normal channel into a Connect channel. You cannot share `#general` or mandatory channels (`cannot_share_mandatory_channel`), DMs or MPDMs (`invalid_channel_type`).
- Errors: `not_paid` ("only available to paid teams"), `restricted_action` (workspace invite-permission settings block this invite type; also the example error for a missing recipient), `not_allowed_for_grid_workspace` (Slack Connect not enabled), `no_external_invite_permission`, `invalid_email`, `too_many_emails`, `user_not_found`, `already_in_channel`, `connection_limit_exceeded`, `connection_limit_exceeded_pending` (a pending invite already exists, so treat it as idempotent), `member_limit_exceeded`, `inviter_email_not_verified`, `invite_lookup_error`, `channel_archived`, `cannot_share_cross_workspace_channel`.
- **How the invitee accepts:** with `user_ids`, they get a notification in their Slack client. With `emails`, they get a transactional email with a join URL. Their own admins may also have to approve, and so may the host org (see `conversations.acceptSharedInvite` / `approveSharedInvite`, https://docs.slack.dev/reference/methods/conversations.acceptSharedInvite). Our system cannot complete acceptance; it can only record "invite sent".
- **Plan requirements** (https://slack.com/help/articles/115004151203): Slack Connect *channels* need **each organisation on a paid subscription**. DMs work on any plan. A free-plan invitee can accept with a one-time **90-day Pro trial**, no credit card needed (`free_trial_accepted` on acceptSharedInvite; `not_paid` if the trial was already used). When the trial ends without an upgrade, the Connect channels are disconnected. Enterprise Grid hosts let free-plan teams join without a trial.
- **Does a Pro *trial* on the host side satisfy `not_paid`?** **UNVERIFIED.** No official page states it. Slack's trial pages talk about trials for *invitees*. Treat this as an assumption to test live.
- 2021 change: `external_limited` was added and the default became the limited invite type (https://docs.slack.dev/changelog). No 2025–2026 changes to the Slack Connect methods were found.

## 5. Developer Program sandboxes (2026)

Source: https://docs.slack.dev/tools/developer-sandboxes (provision at https://api.slack.com/developer-program/sandboxes)
- Joining the Slack Developer Program is free. Sandboxes are **Enterprise org environments** with "the same features included with all Enterprise plans", within the limits below.
- **Basic sandbox** (no payment method needed): up to 2 workspaces, archived after 1 month of inactivity, no guests, no ROSI, and **"cannot connect to other workspaces or sandboxes via Slack Connect"**.
- **Full sandbox** (unlocked by adding a payment method, which is not charged and only confirms you are human, or by being in a paid-plan workspace): up to 5 workspaces, 6-month lifespan (extendable), max 8 users and 2 guests, Free-plan message and file policies, and **max 3 Slack Connect teams, "Slack Connect is only allowed between sandboxes"**.
- Limits: up to 10 sandboxes provisioned per 30 days, at most 2 active at once.
- For the demo, two full sandboxes (one for "NovaCRM", one for a fake "customer") are the documented way to exercise `inviteShared` for free. Slack Connect between two workspaces *inside one* sandbox org is not described (**UNVERIFIED**). Whether `not_paid` ever fires in a full sandbox is also **UNVERIFIED**, though Enterprise features suggest it does not.

## 6. 2025–2026 changes that matter

- **conversations.history / conversations.replies rate cut** (https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps): for commercially distributed, non-Marketplace apps (created on or after 2025-05-29, or new installs), these move to **Tier 1-like: 1 req/min, max 15 objects**. **Internal / custom apps are not affected** (50+/min, limit up to 1000). Our single-workspace internal app is exempt, and our flow does not call these methods anyway.
- API Terms of Service update: in effect for apps created on or after 2025-05-29, and from 2025-06-30 for older apps (same changelog).
- `assistant_view` (AI assistant messaging) is deprecated for **February 2027** and replaced by the Agent Sessions API (https://docs.slack.dev/changelog/tags/deprecation, 2026-08-20). This does not affect the methods above.
- New manifest properties in 2026 (`features.agent_view`, `settings.is_mcp_enabled`) and a `slack manifest diff` CLI command are optional and do not affect this flow (reported via search results; **UNVERIFIED** in detail).
- Classic (non-granular) apps have a deprecation date that search results disagree on (one says 2026-11-16, **UNVERIFIED**). Use a granular-scope bot token (`xoxb-`) from a new app, and this does not matter.
- No deprecations were found for conversations.create, setTopic, invite, list, inviteShared or chat.postMessage.

## 7. Scope set for our bot (minimum)

`channels:manage` (create/topic/invite, public) · `groups:write` (only if `is_private=True`) · `channels:read` (+ `groups:read`) for the list fallback · `chat:write` · `conversations.connect:write` · `users:read.email` (only if we resolve internal users by email). After changing scopes, reinstall the app.
