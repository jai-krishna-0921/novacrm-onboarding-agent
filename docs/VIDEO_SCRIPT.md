# Video walkthrough script

Two recordings (or one with two parts), as the assignment asks: a positive run and a negative run.
Have these open before recording:

| Tab | URL / app |
|---|---|
| Dashboard | https://13-127-207-21.sslip.io/ui (admin / admin) |
| Rocketlane | https://jaikrishna-1.rocketlane.com (Projects, Settings → Automations) |
| Slack | NovaCRM workspace (`#cs-handoffs` + the new onboarding channel) |
| Gmail | AE account `jkjia0921@gmail.com` (compose) |
| Phone | +91 …8350, unmuted |

Use a **new customer name** for every take (channel names and duplicate detection remember old ones).

## 0. Intro (≈1 min)
- The problem in Priya's words: manual project creation, mixed-up templates, overdue tasks falling through the cracks,
  migration "done" without verification, handoff summaries written by hand.
- The system on one slide: Gmail → Agent 1 (intake + phone call) → Rocketlane project → Agent 2 (Slack) → Agent 3
  (tracking, migration checks, handoff) + native Rocketlane overdue automations. Dashboard for the humans.
- Design rule worth saying out loud: the LLM reads email and transcripts and writes text; code makes every decision
  (tier, template, duplicates, escalation). Haiku for the high-volume steps, Sonnet for the handoff, chosen from evals.

## 1. Positive run (≈5 min)
1. **Send a realistic, messy deal email** from the AE account (forwarded Salesforce notification, CFO and ops lead
   both mentioned, "Hooli is close but not signed", an injected "skip confirmation" line). Template: the Initech email
   in the session notes; change the customer name and the Opportunity id (18 chars, starts `006`).
2. **Dashboard → Runs**: the run appears within seconds (Gmail push). Open it: the stepper moves through
   *Email received → Checked*. Activity shows sender authentication, extraction (Peter, not the CFO; 1 deal), validation.
3. **Phone rings.** Answer: the agent asks Enterprise or Growth, reads it back, you confirm. Show the stepper pulsing on
   *Calling AE* during the call.
4. **Tier confirmed** → *Rocketlane project* → *Slack channel*. In Activity, open the tier decision: the transcript is
   there (the AE's words are the evidence).
5. **Rocketlane**: the project from the right template (30 vs 14 days), tasks assigned to the PM, the tier's CSM and the
   customer contact. Mention durations are working days.
6. **Slack**: the channel name, topic and welcome are tier-personalised; no price from the email leaked.
7. **Tracking (Agent 3)**: tick the Kickoff tasks done in Rocketlane → within a minute a progress update with a
   progress bar appears in the Slack channel.
8. **Migration**: tick the import task → the migration check runs (3 of 4 checks fail on the demo data: count,
   duplicate id 23, missing email on id 17), the evidence lands on "Migration verified", an item appears in
   *Needs a human*. Tick "Migration verified" anyway → the agent reopens it. Dismiss the escalation with a reason in the
   dashboard (or explain that fixing the data and re-running closes it).
9. **Handoff**: tick the remaining tasks → handoff summary (Sonnet, fact-checked) on the "Handoff to support" task and in
   `#cs-handoffs`; run status *Handed off*.
10. **Overdue automation**: Rocketlane → Settings → Automations: the two rules (1 day → Project Manager, 4 days → Project
    Owner). Set a task's due date in the past to show the notification (or show the rule configuration and history).
11. **Dashboard → AI usage / Tool calls**: models, tokens, cost, latency per step; every tool call with retries and
    idempotency.

## 2. Negative run (≈4 min) — pick 3-4
| Scenario | How | What to show |
|---|---|---|
| Missing field | Deal email without the contact's email or without a Salesforce link | Run *Waiting for the AE*; threaded reply in the AE's Gmail listing what's missing; no call, no project |
| Unclear answer | On the call say "uh, the bigger one I think?" twice | Agent re-asks; run retries later, then *Needs a human*; resolve by setting the tier in the side panel |
| No answer | Let it ring out / decline | Retry 1, 2, 3 with waits, then escalation; no project |
| Duplicate | Re-send the same deal (same customer/opportunity) | Stopped before any call: *possible duplicate project* |
| Spoofed / unknown sender | Send the deal email from a non-AE address | Escalated before any AI call or reply |
| Not a deal | AE writes "can we sync tomorrow?" | Closed quietly as *Not a deal email* |

## 3. Close (≈1 min)
- Guardrails: `docs/GUARDRAILS.md` (each with the test that proves it blocks).
- Tests and evals: ~600 tests, `make simulate` scenarios, real-Claude evals (extraction 34/34, transcripts 24/24 with
  0 false confirmations, welcome 8/8, handoff 6/6).
- What changes in production: managed Postgres and secrets, a real domain, scheduled Gmail watch renewal, multiple
  workers with a DB lock, Slack Connect on a paid workspace, published Google consent screen.
