# Issue log

Every defect found while building the system: how it was found, its severity and its status. Each fixed issue has
a regression test (named where useful). Decisions live in `DECISIONS.md`; this file is about things that went wrong.

Legend: **Fixed** · **Open** (known, accepted for now) · **In progress**.
Sources: TR = tester subagent review, LIVE = first real end-to-end run, EVAL = real-Claude eval run,
AUDIT = idempotency/concurrency audit, API = check against the live Rocketlane/Retell API.

## Intake and workflow

| # | Issue | Sev | Found | Status / fix |
|---|---|---|---|---|
| 1 | Human `set_tier` resolution bypassed guardrails (could set a tier after a project existed) | High | TR1 | Fixed: escalation kinds with allowed resolutions; `set_tier` only for tier escalations, never after a project. |
| 2 | A stale call result (older attempt) was accepted as the answer | High | TR1 | Fixed: the call result must match the current attempt's call id. |
| 3 | Timeout after `POST /projects` re-POSTed blind → possible duplicate project | Critical | TR1 | Fixed: `AmbiguousToolError` → look the project up first. |
| 4 | Duplicate detection compared raw customer names | Medium | TR1 | Fixed: normalised customer key + 15-char Salesforce id. |
| 5 | Unhandled errors left runs stuck in a non-terminal state | High | TR1 | Fixed: Runner marks the run FAILED. |
| 6 | `lookup_ae` crashed on a tool failure | Medium | TR1 | Fixed: escalates (system). |
| 7 | Welcome check missed dates and "Rs." prices | Medium | TR1 | Fixed. |
| 8 | Customer names like "Monday Labs" / "14-Day Fitness" tripped the welcome check | Medium | TR1 | Fixed: customer name masked before checks. |
| 9 | Date regex false positives ("decisions", "24/7") | Low | TR1 | Fixed. |
| 10 | Gateway 5xx / transport errors on writes treated as safe to retry | High | TR2 | Fixed: ambiguous on writes. |
| 11 | Gmail 404 on a deleted message aborted the whole sync | Medium | TR2 | Fixed: skip it; `history_too_old` → 2-day full sync. |
| 12 | Auto-replies could start runs | Medium | TR2 | Fixed: filtered. |
| 13 | Empty Retell key or malformed webhook accepted | High | TR2 | Fixed: 401 / 400. |
| 14 | LLM outage crashed the run | High | TR2 | Fixed: intake escalates; welcome falls back to a static template. |
| 15 | Retry wait used `sleep()` while holding the lock | Medium | TR2 | Fixed: durable wait + scheduler `tick()`. |
| 16 | Lost Retell webhook left the run waiting forever | High | TR2 | Fixed: reconcile via get-call after 180 s; 900 s ceiling counts as failed. |
| 17 | One failing run in `tick()` stopped the others; Retell polled while holding the lock | Medium | TR3 | Fixed. |
| 18 | Failed lookup after an ambiguous create re-POSTed the project | Critical | TR4 | Fixed: any lookup failure → permanent "verify in Rocketlane". |
| 19 | Empty PM email left the Project Manager placeholder unfilled silently | Medium | TR4 | Fixed: audit warning. |
| 20 | Escalation text said "API unavailable" for every create failure | Low | TR4 | Fixed. |

## Security

| # | Issue | Sev | Found | Status / fix |
|---|---|---|---|---|
| 21 | Anyone could forge `From: <AE>` and trigger a real call + project | Critical | Review of the deployed system | Fixed: Gmail `Authentication-Results` (mx.google.com only) must show dmarc=pass or aligned dkim=pass. Verified on real inbox mail. |
| 22 | `/runs`, `/escalations`, `/resolve`, `/admin` were public once deployed | Critical | Pre-deploy review | Fixed: Caddy basic auth on everything except `/webhooks/*` and `/healthz`. |
| 23 | Gmail push token appeared in plain text in access logs | Medium | LIVE | Fixed: uvicorn access log off; Caddy request log redacts it. |
| 24 | Two different Salesforce links in one email silently used the first | Medium | TR (sender-auth review) | Fixed: clarification. |
| 25 | HTML-only emails lost the Salesforce link | Medium | TR (sender-auth review) | Fixed. |

## Integrations (found against the live APIs)

| # | Issue | Sev | Found | Status / fix |
|---|---|---|---|---|
| 26 | Rocketlane: `GET /projects/placeholders` returns 400; ids are at `GET /placeholders` | — | API | Fixed: resolve names via `/placeholders`. |
| 27 | Gmail push subscription created with an empty token (`.env` placeholder comment parsed as a value) → all pushes 401 | High | LIVE | Fixed: real token, subscription updated; first push 200. |
| 28 | First real call failed: `RETELL_FROM_NUMBER` had a hyphen (`+1510822-8650`) → Retell 404 | High | LIVE | Fixed: phone settings normalised to E.164 (test). |
| 29 | Retell agent never published and number not bound to it | High | LIVE | Fixed: `make retell-setup` publishes and binds; India allowed. |
| 30 | Retell agent spoke too fast for the AE | Medium | LIVE (user) | Fixed: voice speed 0.8, slower prompt, repeat on request. |
| 31 | `retell-setup` couldn't update a published agent (versions immutable, engine can't be swapped) | Medium | LIVE | Fixed: new draft version, edit its LLM in place, publish. |
| 32 | Rocketlane task list returns `phase: null` unless `includeFields=phase` → tracking would never see a phase complete | High | API | Fixed (test). |
| 33 | Slack Connect unavailable on the free workspace | — | Design | Accepted: degrades to an internal channel with an audit note. |

## LLM quality (real-Claude evals)

| # | Issue | Sev | Found | Status / fix |
|---|---|---|---|---|
| 34 | Extraction "repaired" a malformed email (`dana at acme dot com`) instead of copying it | Medium | EVAL | Fixed: `extract_v2`. 26/26. |
| 35 | Extraction echoed prompt-injection text into `notes` | Medium | EVAL | Fixed: `extract_v2`. |
| 36 | Extraction used a no-reply From header as the AE | Low | EVAL | Fixed: `extract_v2`. |
| 37 | Welcome eval judge scored the approved phases as "invented" (0/8) | — | EVAL | Fixed (eval bug): judge gets the approved template content. 8/8. |
| 38 | Welcome didn't name the plan | Low | EVAL | Fixed: `welcome_v2`. |
| 39 | Handoff check flagged dates that were in the data | Medium | EVAL | Fixed: any date in the input may be cited. |
| 40 | Handoff check flagged "no failed checks" as a contradiction | Low | EVAL | Fixed: negations allowed (test). |
| 61 | Everyday AE mail (meeting requests, lost or slipped deals) reached extraction and was escalated as "0 deals; needs a human to split" | Medium | Review (user question) | Fixed: `deal_count == 0` closes the run as `NOT_A_DEAL` (no reply, no escalation); `extract_v3` counts only signed deals; 8 new eval cases. 34/34. |
| 63 | Call transcript not stored with the run: the AE's words (the evidence for the tier decision) are only in Retell | Medium | LIVE (Initech) | Fixed: transcript stored on the `decide_tier` audit entry (test). |
| 64 | Every 60 s tracking sync writes a tool-call audit row even when nothing changed (noise in Activity) | Low | LIVE (Initech) | Fixed: routine read-only syncs go to tool_calls only; failures still audited (test). |
| 65 | Agent's opening line was cut into fragments when the AE spoke over it | Low | LIVE (Initech) | Fixed: interruption sensitivity 0.5, shorter opener; also voice speed 0.95, responsiveness 0.9, Retell model claude-5.5-haiku for lower latency (agent v2). |
| 66 | Rocketlane create timed out on the live run; reconciled by lookup (one project) | — | LIVE (Initech) | Works as designed (idempotency fix #45). |
| 62 | Eval dataset had a 16-character Salesforce ID (invalid) | Low | EVAL | Fixed: test data. |
| 41 | Handoff person-name check is heuristic (single first names not caught); 2 known false positives | Low | TR (tracking) | Open: costs one redraft, then the static template. |

## Tracking (Agent 3)

| # | Issue | Sev | Found | Status / fix |
|---|---|---|---|---|
| 42 | Handoff could happen while migration was unverified | High | TR (tracking) | Fixed. |
| 43 | Five medium defects (double posts, reopen flapping, status regressions) | Medium | TR (tracking) | Fixed. |
| 44 | Lock held while the handoff is drafted (up to two LLM calls can delay webhooks) | Low | TR (tracking) | Open. |

## Idempotency and concurrency (audit, 2026-11-12)

Root cause: idempotency keys were recorded **after** a side effect succeeded, and post-send timeouts were retried
as transient for Retell, Slack and Gmail.

| # | Issue | Sev | Status |
|---|---|---|---|
| 45 | Worker dies mid-create + redelivered call webhook → second Rocketlane project | Critical | Fixed: key reserved in-flight before every side effect; Rocketlane create reconciles by externalReferenceId; webhook guard also requires `AWAITING_CALL`. |
| 46 | Retell create-call timeout re-dials the AE; restart re-runs an accepted call | High | Fixed: Retell idempotency key per (run, attempt), re-sent only within 50 min; read timeout = ambiguous. |
| 47 | Run killed mid-node is never recovered or escalated | High | Fixed: `tick()` escalates runs stuck mid-step (`STUCK_RUN_MINUTES`, default 10); retry resumes from the checkpoint. |
| 48 | Slack welcome re-posted after a timeout; channel created but response lost → `name_taken` forever | Medium | Fixed: channel reconciled by name (only if created by our bot); history checked before re-posting. |
| 49 | Resent deal email for an onboarded opportunity still phones the AE (duplicate check after the call) | Medium | Fixed: `precheck_duplicate` before the call (post-call check kept). |
| 50 | Gmail clarification re-sent after a send timeout / crash | Medium | Fixed: deterministic Message-ID per run; sent mail searched before re-sending. |
| 51 | Migration-check double click runs two batches | Low | Fixed: identical check within 60 s returns the first result. |
| 52 | Every guarantee assumes one process (`--workers 1`); multi-worker needs a DB lock | — | Open (documented) |

## Operations

| # | Issue | Sev | Status |
|---|---|---|---|
| 53 | Gmail watch expires after 7 days and isn't renewed automatically | Medium | Open: `POST /admin/gmail/watch` before ~18 Nov; production would schedule it daily. |
| 54 | Gmail refresh token expires after 7 days (consent screen in Testing mode) | Medium | Open: re-run `make gmail-auth`, or publish the consent screen. |
| 55 | Test suite slightly over the 10 s budget (~11 s) | Low | Open. |
| 56 | Slack re-post check needs the `channels:history` scope; without it an interrupted post escalates instead of re-posting | Low | Open: add the scope to the Slack app and reinstall. |
| 57 | Retell idempotency key and Gmail Message-ID reconcile are documented but not yet exercised live | Low | Open: only matters after a crash or timeout mid-send. |
| 58 | Rocketlane search lag: a crash seconds after a create may not find the new project on restart | Low | Open: escalates as "verify in Rocketlane". |
| 59 | A human "retry" after an unknown call outcome places a new call (escalation text warns) | Low | Accepted. |
| 60 | ~35 pre-existing mypy errors (`make lint`) | Low | Open: ruff is clean; mypy errors are typing-only. |

## Stress test (2026-11-12, fake providers + local Postgres)

Ran: 200 concurrent deal emails through the real app with push + poller racing and ~290 call webhooks (25 %
duplicated, 10 % lost, stale/forged mixed in); 120 emails at 10–30 % injected provider faults with human retries;
8 processes killed right after a side effect and restarted over the same Postgres; 50 tracked projects with tasks
completing in random order and `tick()` racing `/sync`; 12k tool-call and 10k LLM-call rows behind the dashboard
endpoints; 200 parallel wrong-password logins. Invariants that held: one project per opportunity, one call per
attempt, one channel + welcome per project, every run done or escalated, complete masked audit trails, one progress
post per phase, one handoff note, dashboard aggregates equal a brute-force recount, endpoints < 70 ms, auth intact.

| # | Issue | Sev | Status |
|---|---|---|---|
| 67 | Two long customer names truncate to the same Slack channel name; a Slack timeout on the second adopts the first customer's channel | Critical | Fixed: channel names end in an 8-char hash of the run id; timeout reconcile only adopts a channel carrying this run's hash. |
| 68 | Scheduler and tracking only scan the newest 500 runs (older retries/projects ignored) | High | Fixed: scheduler and tracking select runs by status (indexed), any age. |
| 69 | Re-sent deal email while the first run is still on the phone calls the AE again (pre-check only saw runs with a project) | Medium | Fixed: pre-call duplicate check includes active runs awaiting a call/retry. |
| 70 | Gmail push ack waits behind lock-blocked background work | Medium | Fixed: async Gmail push and /healthz; bursts coalesce into one queued sync. |
| 71 | Non-Latin customer names normalise to an empty key (false duplicates, same channel name) | Medium | Fixed: Unicode-aware customer key with a hash fallback (never empty). |
| 72 | `SLACK_HANDOFF_CHANNEL` pointing at another customer's channel isn't refused | Medium | Fixed: refused if the handoff channel belongs to any run. |
| 73 | Huge `offset` / id on dashboard endpoints → 500 instead of 422 | Low | Fixed: offset and ids bounded → 422. |
| 74 | Logout doesn't revoke the session server-side (copied cookie valid for 12 h) | Low | Fixed: session ids revoked on logout (in-process list; single worker). |
| 75 | Handoff could fire with earlier-phase tasks still open | Low | Fixed: handoff waits for every phase. |
| 76 | Failed `#cs-handoffs` post never retried; channel id not recorded on escalated runs | Low | Fixed: failed #cs-handoffs post retried on the next sync; channel id saved at creation. |
| 77 | Default test suite ~22 s (budget 10 s) | Low | Fixed: pytest-xdist (-n 4) and shared per-worker Postgres; ~8 s. |

| 78 | Migration check read only `id, name, email`: a column dropped in transit (schema drift) went unnoticed, and demo tables had identical layouts | Medium | Fixed: tables read with all columns; fifth check `schema` fails on source columns missing in the destination; demo tables now differ in rows and columns. |
| 79 | Problem ids sorted as text (`5` after `47`) | Low | Fixed: numeric ids sort numerically. |
| 80 | Destination rows with no id / an id the source lacks failed `record_count` but the dashboard showed no problem, and Fix kept them | High | Fixed: new problem kind `not_in_source` (summary, row tags, dashboard); Fix drops those rows. |
| 81 | Destination without an `id` column crashed the check (KeyError) instead of failing `schema` | High | Fixed: ids read with `.get`; the schema check reports `id` missing. |
| 82 | Fix failed on column names with spaces/hyphens ("First Name") | Medium | Fixed: positional bind parameters; identifiers quoted. |
| 83 | `make e2e-tracking` rewrote MIG_DEST_URL without the demo-database guard | Medium | Fixed: refuses unless the destination is the demo database. |
| 84 | A customer column literally named `problems` is shadowed in `/migration/data` rows | Low | Known; demo tables don't have one. |
| 85 | Chaos stress test flaky (~1 in 8 at 30% faults): "orphan" channel/project | Low (test) | Not a product defect: create response and the follow-up lookup were both lost, and the run escalated as "outcome unknown, verify before retrying" after the simulated human's 3 retries. The invariant now accepts exactly that case; 25/25 runs pass. |
| 86 | A phase marked Completed in the Rocketlane UI (tasks left open) or a Blocked task/phase went unnoticed by Agent 3 | Medium | Fixed: rule e reads phase statuses; flags to Needs a human + internal Slack, clears itself when fixed in Rocketlane; customer never told. |
| 87 | Comments and the At risk flag are invisible to Agent 3 | Low | Known API limit: no comment endpoint; `atRisk` is write-only (verified live). Blocked status is the readable signal. |
| 88 | Validation rationale showed a Python list (`['customer_email: missing']`) in the Activity log | Low | Fixed: issues joined as text. |
| 89 | A rule-e flag open at handoff (Blocked Go-Live phase, Blocked task outside the template phases) stayed open forever: handed-off runs are never synced again | High | Fixed: handoff waits while a flag is open and goes ahead once it is fixed in Rocketlane. |
| 90 | Flags on a run that stops being tracked (cancelled) could never clear | Medium | Fixed: the scheduler closes them with "run is no longer tracked". |
| 91 | Salesforce links rewritten by Outlook SafeLinks / Google redirects were rejected (needless clarification email) | Medium | Fixed: one level of gateway wrapping is unwrapped before validation. |
| 92 | `/lightning/r/<id>/view` (no `Opportunity` segment) rejected | Low | Fixed: segment optional; the `006` id prefix is still required. |
| 93 | Internal flag post skipped silently when no internal channel is configured | Low | Fixed: audited. |
