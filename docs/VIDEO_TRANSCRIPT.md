# Video transcript (about 14 minutes)

Read it as written. **[Brackets]** are actions, not speech. Slide numbers match the deck.

Before recording:
- dashboard open and logged in: https://13-127-207-21.sslip.io/ui
- Rocketlane, Slack and Gmail open in other tabs, phone nearby
- the Mavis database details file open
- the run emails ready to paste (in your notes: Mavis Analytics, Pied Piper, Hooli)

---

## 1. Intro (30 s)

Hi, I’m Jai Krishna. For this assignment I built an **agentic, multi-agent system** that runs NovaCRM’s customer onboarding end to end.

A closed-deal email comes in. The system confirms the plan with the AE on a real phone call, creates the Rocketlane project from the right template, opens a personalised Slack channel, and tracks the project until it’s handed to support.

It’s all live on AWS, with real Gmail, Retell, Rocketlane and Slack. And I went beyond the brief: the agents also verify the customer’s data migration against their own database.

## 2. The problem (30 s)

Today this is manual, and it breaks in four places:
- messy deal emails;
- an unclear plan tier, so the wrong template gets used;
- manual setup, so steps get missed;
- migration marked done without a real check.

Each of these is now handled by an agent, with guardrails.

## 3. Architecture (1 min)

1. Gmail pushes the deal email to FastAPI, which starts a **LangGraph** run.
2. LangGraph holds the three agents and checkpoints every step in Postgres. So a run can pause for the phone call or for a human, resume exactly where it stopped, and survive a crash without repeating work.
3. Agents never touch an external service directly. Everything goes through one **tool registry**, which calls Retell, Rocketlane, Slack and Gmail.
4. The React dashboard reads the same database. It all runs on AWS with Docker and HTTPS.

**Model choice:** Claude **Haiku** for extraction, transcript reading and drafting, because on my evals it matched Sonnet at about a seventh of the cost. **Sonnet** only writes the handoff summary.

## 4. The agents (1 min)

Three agents, each agentic within its own lane, each with a hard boundary.

- **Intake and Routing** reads the email, calls the AE and creates the project. It can never post to Slack or guess a tier.
- **Communication** owns every customer-facing message. It can’t touch the project or the tier.
- **Project Tracking** watches Rocketlane after setup. It can’t write to the customer itself; it asks the Communication agent to.

So the agent that reads untrusted email can never message a customer. The rule throughout: **the LLM reads and drafts, code decides, and anything unclear goes to a human.**

## 5. Flow 1: email to project (1 min)

Four gates, and every “no” goes somewhere safe:
1. **Known AE with a valid DMARC signature?** If not, a human reviews it. Spoofed mail is stopped before any AI call.
2. **All fields valid?** If not, the agent emails the AE exactly what’s missing and creates nothing.
3. **A new deal?** A duplicate check runs before the call and again before the create.
4. **Tier confirmed?** Three signals must agree:
   - Retell’s call analysis;
   - the AE saying “yes” to the read-back;
   - my own LLM reading of the transcript.

   If they don’t, it retries up to three calls, then goes to a human. It never defaults a tier.

When all four pass: Enterprise gets the 30-day template with a dedicated CSM, and Growth gets the 14-day template with a pooled CSM.

## 6. Flow 2: tracking (40 s)

Agent 3 hears about task changes through a Rocketlane webhook and also polls every 15 seconds.
- A phase starts or ends: the customer gets an update.
- The import is done: it runs the migration checks.
- “Migration verified” is ticked without a passing check: it reopens the task.
- Something is marked Blocked: the team is flagged internally.
- Go-Live is complete: it writes the handoff summary.

Overdue tasks use Rocketlane’s own automation (1 day goes to the PM, 4 days to the owner), so they’re handled even if my service is down.

## 7. Tools, guardrails, idempotency (1 min 15 s)

Every action passes through the tool registry: an allowlist per agent, schema validation, an **idempotency key reserved before the side effect**, safe retries, and an audit row.

Idempotency matters because an API can time out *after* it did the work. My retries look up whether the action already happened before acting again. So it’s one call per attempt, one project, one channel and one welcome. A chaos test proves this, with up to 30 percent injected failures across 120 deals.

The guardrails:
- no confirmed tier, no project;
- a sender check;
- emails treated as data, never as instructions, against prompt injection;
- duplicate checks;
- customer messages fact-checked, so nothing internal leaks;
- everything audited, with phone numbers masked.

**Evals on real Claude:**
- extraction: 34 out of 34;
- call transcripts: 31 out of 31, with **zero false confirmations**;
- welcome messages: 8 out of 8;
- handoff summaries: 6 out of 6.

Plus over 900 automated tests.

## 8. Data migration (40 s)

This one goes beyond the brief. The CSM connects the customer’s database in the dashboard with a read-only user. The connection is tested first, and the password is encrypted and never shown again.

Agent 3 then runs five checks against what landed in NovaCRM: row count, every id copied, no duplicates, required fields filled, and no dropped columns. A pass puts the evidence on the task. A fail escalates, and a premature tick gets reopened.

## 9. Live demo (about 6 min; speak only the lines below)

**Positive run.** [Agents page.] All three agents are healthy.

[Send the Mavis Analytics email.] The run starts from the Gmail push, and the sender and fields pass.

[Answer the call: “Enterprise”, then “Yes”.] The page shows the call live, then “reading the answer”.

[**AE calls** tab, play the recording.] The tier is confirmed by all three signals.

[Rocketlane.] It’s the Enterprise template: 30 days, dedicated CSM.

[Slack.] A personalised channel and welcome, with no price and nothing internal.

[Tick the Kickoff tasks in Rocketlane.] About 15 seconds later the update appears in Slack, and the progress tab updates on its own.

[**Migration check** tab: connect Mavis’s database.] All five checks fail against NovaCRM: missing rows, a duplicate, blank emails, a dropped column.

[**Fix destination data**, then **Run check**.] Now all five pass.

[Rocketlane, Automations.] And the native overdue automation.

**Negative runs.**

[Send the Pied Piper email.] The contact email is missing. The agent asked the AE for it, and nothing was created.

[Re-send the Mavis email.] Duplicate deal. It goes to a human with no call.

[Send the Hooli email; on the calls say “wrong number”.] An unclear answer is never accepted. After three calls the run goes to Needs a human with every recording. A person sets the tier, and that action is audited.

[Acme Retail.] Here, “Migration verified” was ticked without a passing check, so the agent reopened it.

[Terminal: `make test-part4`.] The Part 4 tests (happy path, validation, both templates, Rocketlane down, existing project, no answer, ambiguous answer) all pass.

[AI usage, Tool calls.] Every model call and tool call is logged with tokens, cost and its inputs and outputs.

## 10. Assumptions and design (1 min)

Assumptions, with reasons:
- **one deal per email**;
- **the tier comes from the phone call**, never from the price;
- **CS owns the templates**, which live in configuration, not prompts;
- **overdue escalation is native Rocketlane**;
- **customer data is read-only**.

The design enforces:
- **exactly-once** side effects;
- **crash-safe** checkpoints;
- **least privilege** per agent;
- **humans in charge** of anything unclear;
- **evals** gating every prompt change.

Every decision, guardrail and issue I found is documented in the repo.

Slack is real, not mocked. The only production gap is Slack Connect, which needs a paid plan, so that invite is flagged as a manual step.

To sum up: everything the brief asked for, plus migration verification, live tracking, call recordings, observability, evals and chaos testing. Thank you.
