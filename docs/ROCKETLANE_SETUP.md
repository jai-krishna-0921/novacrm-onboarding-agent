# Rocketlane setup guide (trial account)

## What Rocketlane is

Rocketlane is a customer-onboarding / professional-services platform. Think "Asana built for working *with* customers":

| Concept | What it is | In our build |
|---|---|---|
| **Project** | One customer engagement, with phases, tasks, dates, an owner and a team (internal members plus customer users). | One per onboarding, created by Agent 1 via `POST /projects`. |
| **Project template** | A reusable plan (phases, tasks, durations, dependencies, role assignments). Projects are created *from* a template. | Enterprise (30-day) and Growth (14-day). The template ID comes from config, never from the LLM. |
| **Phase / Task** | The plan structure. Tasks have owners, due dates and dependencies. | 4 phases: Kickoff, Data Migration, Configuration, Go-Live. |
| **Customer (company)** | The external company; its users can be invited into the project portal. | `customer.companyName`, auto-created by the API. |
| **Team member vs customer user** | Internal staff vs external people. | Owner, PM and CSMs are team members; the customer contact is a customer user. |
| **Project Owner** | The accountable person on the project. | `ROCKETLANE_OWNER_EMAIL`. Notified at 4 days overdue. |
| **Automations** | No-code rules: trigger → condition → action (e.g. "task overdue > N days → notify"). UI only, no API. | The overdue-escalation requirement. |
| **Document templates** | Templates for docs/spaces inside projects. | Not used. |

## UI map (left sidebar)

- **Projects**: the list of all projects. Open one to see Plan (phases/tasks), Team, Documents/Spaces and Updates.
- **Templates**: library of project and document templates. Build ours here.
- **Customers**: companies (Acme and Modert are sample data).
- **Settings (gear) → Users/Team**: invite team members. **Settings → API**: API keys (done). **Settings → Automations**: rules.

## Current account state (read via API on 2026-11-11)

- Users: you (Jai, ACTIVE); sample team members Rachel and Paul (fake @rocketlane.com emails); 2 sample customer contacts.
- Projects: `[Sample] Acme 2 week onboarding`, `[Sample] Modert 5 week onboarding`.
- Project templates: only `Simple Onboarding` (trial seed data, created with the account). No Enterprise/Growth templates exist, and the API cannot create templates, so build them in the UI (tip: duplicate Simple Onboarding and restructure it).
- Roles (via `GET /roles`): Customer, Product Manager, Customer success, Engineer, Project Manager, Implementation Manager.
- Re-check any time with `cd backend && uv run python -m scripts.rl_inspect` (GET-only).
- Note: the sample customer **"Acme"** normalizes to the same key as "Acme Corp". Use a different demo customer (e.g. "Globex Corporation"), or delete the sample projects, so the duplicate guard doesn't fire.

## Step 1: Users (Settings → Team members → Invite members)

Templates hold the plan (phases, tasks, role placeholders); users are the real people. The API needs a real Project
Owner, the overdue automation notifies real people, and the tier difference (dedicated vs pooled CSM) is a choice of
person. The existing roles are enough; **no new roles are needed**. Only 2 invites are needed:

| Role | Who | Rocketlane role | .env variable |
|---|---|---|---|
| Project Owner (4-day alert) | you, `jaikrishna0921@gmail.com` (exists) | n/a | `ROCKETLANE_OWNER_EMAIL` |
| Project Manager (1-day alert) | **invite** `jaikrishna0921+pm@gmail.com` | Project Manager | `ROCKETLANE_PM_EMAIL` |
| Dedicated CSM (Enterprise) | **invite** `jaikrishna0921+csm@gmail.com` | Customer success | `ROCKETLANE_CSM_ENTERPRISE` |
| Pooled CSM (Growth) | Rachel, `sampleuser1@rocketlane.com` (exists) | Customer success | `ROCKETLANE_CSM_GROWTH` |

Accept both invites from your Gmail (plus-aliases deliver to your inbox) so the users become ACTIVE.

In the templates, assign tasks to the **role placeholders** (Customer success, Project Manager), not to named people.
The code picks the actual person per tier when it creates the project.

## Step 2: Templates (Templates → New project template)

Build two templates. Set the durations so the totals come to 30 and 14 days, and make **"Go-live checklist sign-off" depend on "Migration verified"**. That dependency is the fix for Priya's "migration marked done but not verified" problem. These task lists are placeholders; CS owns the real ones.

**NovaCRM Enterprise Onboarding (30 days, dedicated CSM)**

| Phase | Task | Days |
|---|---|---|
| Kickoff (d1–5) | Internal sales→CS handoff review | 1 |
| | Kickoff call | 1 |
| | Success plan & goals sign-off | 2 |
| Data Migration (d4–15) | Source data export | 3 |
| | Field mapping review | 2 |
| | Test import | 2 |
| | Full import | 2 |
| | **Migration verified** (validation checks passed) | 1 |
| Configuration (d12–24) | Pipelines & stages setup | 3 |
| | Roles & permissions | 2 |
| | Integrations setup | 3 |
| | SSO setup | 2 |
| Go-Live (d24–30) | Admin training | 1 |
| | End-user training | 2 |
| | Go-live checklist sign-off (depends on Migration verified) | 1 |
| | Handoff to support | 1 |

**NovaCRM Growth Onboarding (14 days, pooled CSM)**

| Phase | Task | Days |
|---|---|---|
| Kickoff (d1–2) | Kickoff call | 1 |
| | Success plan | 1 |
| Data Migration (d2–7) | Data export | 2 |
| | Data import | 2 |
| | **Migration verified** | 1 |
| Configuration (d6–11) | Pipelines & stages | 2 |
| | Roles & permissions | 1 |
| | Basic integrations | 2 |
| Go-Live (d11–14) | User training | 1 |
| | Go-live checklist (depends on Migration verified) | 1 |
| | Handoff to support | 1 |

Then run `uv run python -m scripts.rl_inspect`: the new templates show up under `GET /templates` with their IDs. Put them in `ROCKETLANE_TEMPLATE_ENTERPRISE` / `ROCKETLANE_TEMPLATE_GROWTH`. (`GET /templates` is undocumented but works on this account.)

## Step 3: Automations (Settings → Automations, or inside each template)

| Rule | Trigger | Condition | Action |
|---|---|---|---|
| Overdue 1d → PM | Task becomes overdue | overdue by ≥ 1 day | Notify **Project Manager** |
| Overdue 4d → Owner | Task becomes overdue | overdue by ≥ 4 days | Notify **Project Owner** |

- Attach them to **both templates** so every new project inherits them. Whether API-created projects inherit template automations is UNVERIFIED; we check it with the first real project.
- If "Project Manager" isn't offered as a recipient, choose the PM user by name (or a project role), and note that choice in the video.
- To demo it: in a project, set a task's due date 1 or 4 days in the past, then show the notification and the automation's run history.

## Step 4: Clean-up before recording
Archive or delete rehearsal projects, or use a fresh customer name for each take.
