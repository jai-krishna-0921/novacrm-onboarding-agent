# NovaCRM Onboarding Agent

Multi-agent automation of a mid-market SaaS customer-onboarding workflow (Rocketlane Forward Deployed Engineer take-home).

A "deal closed" email arrives in Gmail. **Agent 1 (Intake & Routing)** extracts and validates the deal, **phones the AE** (Retell AI) to confirm the plan tier, and creates a **Rocketlane** project from the matching template. **Agent 2 (Communication)** opens a personalized **Slack** channel. **Agent 3 (Project Tracking)** watches the Rocketlane project: phase updates in Slack, automatic data-migration verification (a "Migration verified" task ticked without a passing check is reopened), and a fact-checked handoff summary for support at the end. Overdue-task escalation runs as a native **Rocketlane automation** (1 day overdue → Project Manager, 4 days → Project Owner). An ops dashboard at `/ui` shows every run live.

## Design rules
1. Customer Success owns the process (templates, durations, owners). Tier → template mapping is config, never prompt text.
2. The LLM reads unstructured input (email, call transcript) and drafts text. **Code makes every decision.**
3. **No confirmed tier, no project.** Unclear or no answer → retry (max 3) → human escalation. A tier is never defaulted.
4. Every external action goes through one tool registry: per-agent allowlist, input validation, idempotency keys, retry, audit.
5. Anything ambiguous goes to a human escalation queue. Missing fields → reply to the AE, never guess.

## Architecture
```
Gmail ──Pub/Sub push / 20 s poll──▶ FastAPI ──▶ LangGraph (Postgres checkpointer)
                                       ▲          ├─ root router (code only) + human escalation
Retell call webhook (HMAC-verified) ───┘          ├─ Agent 1: check sender → extract → validate → AE lookup
                                                  │           → call AE ⇄ interrupt/resume → decide tier
                                                  │           → duplicate check → create Rocketlane project
                                                  └─ Agent 2: draft welcome (fact-checked) → Slack channel/topic/invites/post
```
- **LLM:** Claude via LangChain `ChatAnthropic`, native JSON-schema structured output; tokens and cost recorded per call. Haiku 5.5 for extraction, transcript cross-check and welcome; Sonnet 5.5 for the handoff summary (chosen from evals on both).
- **Durable pause:** the graph checkpoints while the phone call happens and resumes on Retell's `call_analyzed` webhook.

## Guardrails (each has a test proving it blocks)
| Guardrail | Behaviour |
|---|---|
| Sender is an active AE | Checked before any LLM call or reply; otherwise escalated |
| Required fields present and valid | Threaded Gmail reply to the AE listing what's missing; nothing created |
| One deal per email | Escalated |
| Tier confirmed | Provider analysis says enterprise/growth **and** explicit read-back confirmation **and** an independent LLM transcript cross-check agrees |
| Call outcomes | No answer, voicemail, IVR, hedged, stale/misrouted webhooks: never a tier; retry, then escalate |
| Duplicates | Normalized customer name + 15-char Salesforce ID, checked locally and in Rocketlane; idempotent creates; an ambiguous timeout never re-POSTs |
| Human resolution | `set_tier` only for unconfirmed-tier escalations, never after a project exists |
| Welcome message | Fact-checked (name, duration, CSM model, no prices or dates); one regeneration, then a static template |
| Audit | Every step: timestamp, agent, input, output, rationale, status; phone numbers masked |

## Run it
```bash
cp .env.example .env            # fill in credentials (never committed)
make up migrate seed            # Postgres, schema, AE directory + checkpointer tables
make test                       # offline: fake providers + scripted LLM, ~5 s
make simulate SCENARIO=all      # 10 mock end-to-end runs (happy, no-answer, API down, duplicates, ...)
make eval                       # real-Claude evals: extraction, transcripts (false-confirmed must be 0), welcome
make preflight                  # read-only checks of every integration
make api                        # FastAPI on :8000 (PROVIDER_MODE=fake|real)
```
Setup guides: `docs/ROCKETLANE_SETUP.md`, `docs/CONNECTIONS.md`. Verified API notes: `docs/reference/`.
Why it's built this way: `docs/DECISIONS.md`. Every guardrail and the test that proves it: `docs/GUARDRAILS.md`.
Every defect found and how it was fixed: `docs/ISSUES.md`. Deploy (AWS EC2): `deploy/aws_up.sh`, `deploy/deploy.sh`.

## Repository
- `backend/app/graph`: LangGraph root router and the two agent subgraphs
- `backend/app/guardrails`: pure decision code (input validation, tier decision, output checks)
- `backend/app/tools`: tool registry and contracts
- `backend/app/providers/{real,fake}`: Rocketlane, Retell, Slack, Gmail clients and their scripted fakes
- `backend/app/main.py`: webhooks and the run/escalation API
- `backend/evals`: labelled datasets and the eval runner
- `docs/`: requirements analysis, implementation plan, setup guides
