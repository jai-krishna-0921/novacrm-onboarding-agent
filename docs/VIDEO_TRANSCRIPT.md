# Video transcript (about 15 minutes)

Read it as written. **[Brackets]** are actions, not speech. Slide numbers match the deck.

Before recording:
- dashboard open and logged in: https://13-127-207-21.sslip.io/ui
- Rocketlane, Slack and Gmail open in other tabs, phone nearby
- these files open: the Mavis database details (host `postgres`, port 5432, database `mavis_crm`, user `mavis_ro`) and the deal emails below

---

## Slide 1: Intro (0:00, 30 s)

Hi, I’m Jai Krishna. This is my submission for the Rocketlane Forward Deployed Engineer assignment.

I built an **agentic, multi-agent system** for NovaCRM that takes a closed-deal email from Sales and runs the whole customer onboarding end to end.

It confirms the plan with the account executive on a real phone call, creates the Rocketlane project from the right template, opens a personalised Slack channel, and then keeps watching the project until the customer is handed over to support.

Three AI agents do the work. Each has its own job, its own tools and its own guardrails. They are coordinated by LangGraph, and a human is brought in whenever something is unclear.

Everything you’ll see is live: real Gmail, a real Retell voice call, the real Rocketlane API, a real Slack workspace and a real customer database, deployed on AWS.

I also went beyond the brief:
- **automatic data-migration verification** against the customer’s own database;
- **live project tracking**, with progress posted to the customer;
- **call recordings and transcripts** in the dashboard;
- a full observability dashboard;
- evals on real Claude;
- chaos testing.

## Slide 2: The problem (0:30, 1 min)

Priya’s team does this by hand today, and it breaks in four places.

- Deal emails from Sales are messy and often missing details.
- The plan tier is often unclear, so the wrong template gets used.
- Setting up the project and the Slack channel is manual, so steps get missed.
- And “migration verified” gets ticked without anyone actually checking the data.

With the agents, each of those changes:
- fields are extracted and validated, and the AE is asked when something is missing;
- the tier is confirmed with the AE by phone;
- the project and channel are created in about a minute, exactly once;
- migration is verified by code, and progress is posted to the customer as it happens.

## Slide 3: Technical architecture (1:30, 1.5 min)

Here is how it fits together.

1. A deal email lands in Gmail. Gmail pushes a notification through Google Pub/Sub to my FastAPI service, so there’s no polling delay.
2. FastAPI starts a **LangGraph** run. The graph holds the three agents as subgraphs, and every step is checkpointed in Postgres.
3. That checkpointing matters for two reasons. The run can pause, while we wait for the phone call or for a human decision, and resume exactly where it stopped. And if the server crashes mid-step, it resumes from the last checkpoint instead of repeating work.
4. The agents never call an external service directly. Every action goes through one **tool registry**, and I’ll explain why in a moment.
5. The external services are Retell AI for the voice call, Rocketlane, Slack and Gmail. Retell, Rocketlane and Gmail also call back into the service through webhooks.
6. The React dashboard reads the same Postgres database.
7. It all runs on an AWS EC2 instance with Docker and HTTPS.

**Model choices.** I use **Claude Haiku** for extraction, reading the call transcript and drafting the welcome. They are short, structured tasks, and I measured Haiku against Sonnet on my evals: same accuracy at roughly a seventh of the cost. I use **Sonnet** only for the handoff summary, which is longer reasoning over project facts.

## Slide 4: The three agents (3:00, 1.5 min)

This is a multi-agent system. Each agent is agentic within its own lane: it reads unstructured input, decides its next step, calls its tools and hands off to the next agent. But each one runs inside hard guardrails. The agents are permission boundaries, not three chatbots that can each do anything.

- **Agent 1, Intake and Routing.**
  - It reads the email, checks the sender, calls the AE, picks the template and creates the Rocketlane project.
  - It decides the tier, but only when the tier is confirmed.
  - It can never post to Slack, and it can never guess a tier.
- **Agent 2, Communication.**
  - It opens the customer’s channel and writes the welcome.
  - It is the only agent that can send anything customer-facing, and every message is checked before it goes out.
  - It can’t change the tier, the project or any task.
- **Agent 3, Project Tracking.**
  - It watches Rocketlane after setup, checks the migration, flags problems and writes the handoff.
  - When the customer needs an update, it asks Agent 2 to post. It cannot write in the customer channel itself.

Why split it this way? The agent that reads untrusted email can never message a customer, and one place controls what customers are told.

The rule across all three: **the LLM reads and drafts, code makes every decision, and anything unclear goes to a human.**

## Slide 5: Flow 1, email to project (4:30, 1 min)

This is Agent 1’s decision path. There are four gates, and each “no” goes somewhere safe.

1. **Is the sender a known AE, with a genuine Gmail DMARC signature?** If not, we stop and a human looks at it. This stops spoofed emails before any AI call.
2. **Are all the fields valid?** That means customer name, contact email, the AE, and a real Salesforce opportunity link. If not, the agent replies to the AE listing exactly what is missing, and nothing is created.
3. **Is this a new deal?** I check for duplicates by customer and Salesforce id, both before the call and again before creating the project.
4. **Is the tier confirmed on the call?** That needs three signals to agree:
   - Retell’s call analysis names one plan;
   - the AE says “yes” when the plan is read back;
   - my own LLM reading of the transcript agrees.

   If not, it retries, up to three calls. After that it goes to a human. It never defaults a tier.

When all four gates pass, it creates the project from the template that CS owns. Enterprise gets the 30-day template with a dedicated CSM; Growth gets the 14-day template with a pooled CSM. Then Agent 2 opens the channel.

## Slide 6: Flow 2, tracking (5:30, 45 s)

After setup, Agent 3 keeps watching the project. It gets a Rocketlane webhook when a task changes, and it also polls every 15 seconds.

Each change maps to one rule:
- a phase starts or finishes, and the customer gets an update;
- the data import task is done, and it runs the migration checks;
- someone ticks “Migration verified” without a passing check, and it reopens the task;
- something is marked Blocked, and the team is flagged internally;
- Go-Live is complete, and it writes the handoff summary for support.

Overdue tasks are handled by a native Rocketlane automation, on purpose. After one day overdue it notifies the project manager, after four days the project owner. That keeps working even if my service is down.

## Slide 7: Tools, guardrails and idempotency (6:15, 1.5 min)

Every external action passes through the tool registry, in five steps:
1. **Allowlist:** is this agent allowed to use this tool? For example, Agent 1 has no Slack tools at all.
2. **Validation:** the input is checked against a strict schema.
3. **Idempotency key:** a key is reserved *before* the side effect happens.
4. **Call, retry, reconcile:** the call is retried safely.
5. **Audit:** every call is written to the audit log.

Idempotency is the part I spent the most time on. The dangerous case is an API that times out *after* it already did the work. A naive retry would create a second Rocketlane project, a second Slack channel, or phone the AE twice. Here, a retry first looks up whether the action already happened, and only acts if it didn’t. So it’s one call per attempt, one project, one channel and one welcome, even through crashes and timeouts. I proved this with a chaos test that injects 10 to 30 percent failures across 120 deals.

The guardrails:
- no confirmed tier, no project;
- known AE and DMARC before anything runs;
- emails and transcripts are treated as data, never instructions, which protects against prompt injection;
- duplicate checks;
- every customer message is fact-checked, so nothing internal leaks;
- migration is checked by code, read-only;
- anything unclear goes to a human;
- everything is audited, phone numbers are masked, and secrets are never logged.

Evals run against real Claude:
- extraction: 34 out of 34;
- call transcripts: 31 out of 31, with **zero false confirmations**, including trap cases where the AE only agrees to a plan the bot suggested;
- welcome messages: 8 out of 8;
- handoff summaries: 6 out of 6.

On top of that there are over 900 automated tests, plus stress and chaos suites.

## Slide 8: Data migration (7:45, 45 s)

This part goes beyond what the brief asked for. Priya’s biggest pain was migration being marked done without being verified, so I made the agent prove it.

The CSM connects the customer’s old database in the dashboard with a read-only user. The connection is tested before saving, the password is stored encrypted, and it is never shown again.

When the import task is ticked, Agent 3 compares the customer’s data with what landed in NovaCRM, using five checks:
- the row count matches;
- every id was copied;
- there are no duplicates;
- name and email are filled;
- no column was dropped.

If they pass, the evidence goes on the task and the CSM can close it. If they fail, the CSM is escalated to, and ticking the task anyway gets it reopened.

In production, the source would be the customer’s export or a read replica, with credentials in a secrets manager per customer, and the agent never writes to customer data.

## Slide 9: Live walkthrough (8:30, about 5 min)

**Positive run.**

[Open the **Agents** page.] All three agents are healthy. You can see the Gmail watch, the scheduler and Agent 3’s sync.

[Send this email from your AE address:]
```
Subject: Closed – Mavis Analytics
Hi team, Mavis Analytics just signed. Main contact is Anna Mavis (anna@mavis.example).
Salesforce: https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00007MvSqAIAQ/view
Plan to be confirmed on the call. – Jai
```

[The run appears on **Overview**. Open it.] The email arrived through Gmail push. The stepper shows the sender check and the extraction passing.

[Phone rings. Say “Enterprise”, then “Yes”.] The page shows the call in progress, then “call ended, reading the answer”.

[Open the **AE calls** tab and play the recording.] Here is the recording and transcript of that call. The tier was confirmed by all three signals.

[Open **Rocketlane**.] The project was created from the Enterprise template: 30 days, dedicated CSM, phases and tasks from CS’s template.

[Open **Slack**.] Agent 2 created the personalised channel, set the topic and posted the welcome. Notice there’s no price and nothing internal in it.

[In Rocketlane, tick the Kickoff tasks.] Within about 15 seconds, Agent 2 posts the Kickoff update in Slack, and the **Project progress** tab updates on its own.

[Run → **Migration check** tab → Customer database: enter the Mavis details → **Test connection & save**.] I connect Mavis’s database with a read-only user. The verdict shows 5 of 5 checks failing against what landed in NovaCRM: rows missing, a duplicate, blank emails, and a dropped phone column.

[**Data migration** page → **Fix destination data**. Back on the run → **Run check**.] After the import is fixed, all five checks pass.

[Rocketlane → Settings → Automations.] And here is the native overdue automation: 1 day goes to the project manager, 4 days to the project owner.

**Negative runs.**

[Send this, with no contact email:]
```
Subject: closed – Pied Piper
Pied Piper signed today. https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00004PpQrSIAX/view
- jai
```
The contact email is missing, so the agent replied to the AE asking for it. [Show the Gmail reply.] Nothing was created: no call, no project, no channel.

[Re-send the Mavis email.] Same customer, same Salesforce opportunity, so it goes to **Needs a human** as a possible duplicate. There’s no call and no second project.

[Send a new valid deal and, on the call, say “Sorry, wrong number”, or “maybe Enterprise, I’m not sure”.] An unclear answer is never accepted. It retries, up to three calls, and then the run goes to **Needs a human** as “plan tier not confirmed”. [Open the **AE calls** tab to show each attempt.] A person can set the tier here, retry or cancel, and every action is audited.

[Open **Acme Retail**.] This project shows the migration guard live: someone ticked “Migration verified” without a passing check, and Agent 3 reopened it and escalated to the CSM.

[Terminal: `make test-part4`.] And the Part 4 tests: the happy path, validation of incomplete emails, template accuracy for Enterprise and Growth, Rocketlane being down, an existing project, the AE not answering, and an ambiguous answer. All 20 pass. The full suite is over 900 tests.

[Open **AI usage** and **Tool calls**.] Every LLM call is logged with model, prompt version, tokens and cost, and every tool call with its input and output.

## Slide 10: Assumptions and system design (13:30, 1.5 min)

A few assumptions, each with a reason.

- **One deal per AE email**, because that’s how Sales announces closes.
- **The tier is confirmed by phone, never inferred from the price** in the email, because prices in emails are noisy and the mapping belongs to CS.
- **CS owns the process**: templates, durations and the CSM model live in configuration, not in prompts.
- **Overdue escalation is native Rocketlane**, so it works even if my service is down.
- **The customer’s data is read-only** for the migration checks.

And what the design enforces:
- **exactly once**: one call per attempt, one project, one channel, one welcome;
- **crash-safe**: LangGraph checkpoints, and stuck runs escalate instead of hanging;
- **least privilege**: each agent can only use its own tools;
- **humans stay in charge**: an escalation queue with audited, limited actions;
- **evals gate every prompt change**, and every issue I found while testing is written down with its fix in `docs/ISSUES.md`. The full decision log is in `docs/DECISIONS.md`, and every guardrail with the test that proves it is in `docs/GUARDRAILS.md`.

Slack is real here, not mocked. The one production difference is Slack Connect: inviting the customer’s workspace needs a paid plan, so on the free plan that invite is a manual step, which the system flags.

To sum up, this is a production-minded, agentic, multi-agent system.

**Everything the brief asked for:**
- Gmail trigger;
- a real voice call to confirm the tier;
- the Rocketlane project from the right template;
- the personalised Slack channel;
- the Rocketlane overdue automation;
- guardrails;
- the automated tests.

**What I added on top:**
- automatic data-migration verification against the customer’s own database;
- a tracking agent that keeps the customer updated and reopens unverified work;
- call recordings and transcripts;
- a full observability dashboard;
- evals on real Claude with zero false tier confirmations;
- idempotency tested under chaos.

Thank you for watching.
