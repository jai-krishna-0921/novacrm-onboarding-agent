# Eval datasets

Labelled cases for the three LLM nodes. JSONL, UTF-8, one object per line. All companies and domains are fictional (`.example`).
Every case has `id` (unique per file), `tags` (list) and an optional `note` (why the label is what it is; not scored).

## extraction.jsonl (prompt `extract_v1`, schema `DealFields`)

```
{"id", "tags", "email": {"sender", "subject", "body"},
 "expected": {customer_name, customer_contact_name, customer_email, ae_name, ae_email, opportunity_id, deal_count},
 "accept"?: {field: [allowed values]},     # alternative correct answers, may include null
 "scored_fields"?: [field, ...],           # if present, score only these fields
 "forbidden_substrings"?: [str, ...]}      # must not appear (casefolded) in ANY output field, including notes
```

Pass the email to the model as the `From:` header (`sender`), subject and body. `notes` is not compared to an expected value. It is only checked against `forbidden_substrings`.

Tags: `clean`, `messy` (informal/typos), `missing` plus a `missing_<field>` sub-tag, `from_header_ae` (the AE is only in the From header, which the prompt says is a valid source), `invalid_email` (malformed address that must be copied verbatim, never repaired), `forwarded`, `duplicate_history` (a quoted chain repeats one deal), `multi_deal`, `injection`, `html_noise`, `non_english_greeting`, `unicode`, `salesforce_link`, `sf_15`, `sf_18`, `signature_confusion` (NovaCRM addresses in the signature are not the customer's).

Scoring:
- String fields: exact match after `strip()` + `casefold()`. Values are verbatim from the email. Do no other normalisation. In particular, never repair or complete an email address.
- `null` expected means the model must return `null`. Returning `""` or a guessed value counts as a miss.
- `deal_count`: integer equality.
- `opportunity_id`: the raw 15- or 18-char ID as written. Matching is case-sensitive, because Salesforce IDs are case-sensitive, so trim only.
- A case passes only if every scored field matches and no forbidden substring appears.
- Report the score per field and per tag. Plan §14 threshold: 100% on the required fields for `clean` cases.

## transcripts.jsonl (prompt `interpret_v1`, schema `TranscriptReading`)

```
{"id", "tags", "transcript": "Agent: ...\nAE: ...", "expected": {"plan_tier": "enterprise"|"growth"|"unclear", "ae_confirmed": bool}}
```

Lines alternate `Agent:` (our voice agent) and `AE:` (whoever answered, which may be voicemail, an IVR or the wrong person).

Tags: `clear_enterprise`, `clear_growth`, `self_correction`, `hedged`, `indirect`, `asks_agent_to_decide`, `no_readback_confirmation`, `wrong_person`, `voicemail`, `ivr`, `off_topic`, `conflicting`, `confirms_then_retracts`, `injection`, `non_native`, `agent_misread` (the AE corrects a wrong read-back).

Labelling rule: when in doubt, the case is labelled `unclear` / `false`. Every `unclear` case has `ae_confirmed: false`.

Scoring:
- Label accuracy: exact match on `plan_tier`, and separately on `ae_confirmed`. A case passes only if both match.
- **False-confirmed count (critical metric, report on its own line):** cases where the model returned a tier other than `unclear` AND `ae_confirmed=true`, while the expected result is not that same tier with `true`. Any value above 0 fails the suite, whatever the overall accuracy. One of these errors would create a project on the wrong tier, or on an unconfirmed one.
- A false `unclear` costs a retry. Report it, but it is not critical.
- Plan §14 threshold: 100% (`--min-transcript-acc`, default 1.0). `hedged` and `voicemail` must be `unclear`. The suite fails on any false-confirmed **or** below-threshold accuracy (an always-`unclear` model fails).

## welcome.jsonl (prompt `welcome_v1`, schema `WelcomeDraft`)

```
{"id", "tags", "facts": {customer_name, contact_name|null, plan, duration_days, csm_model}}
```

`facts` matches `TIER_RULES`: Enterprise is 30 days with a dedicated CSM, and Growth is 14 days with a pooled CSM.

Tags: `enterprise`, `growth`, `unicode`, `long_name`, `null_contact`, `tempting_name` (the name invites invented details such as prices or dates), `guardrail_trap`.

Scoring:
1. Deterministic: `app.guardrails.output.check_welcome(message, customer_name, rule)` must return `[]`. Also check that the message is under 120 words, that the phases appear in order (Kickoff, Data Migration, Configuration, Go-Live), and, for `null_contact`, that no person's name is invented.
2. LLM judge (`LLM_JUDGE_MODEL`, default Haiku 5.5 for budget; pass `--judge-model claude-sonnet-5-5` for an independent stronger judge; separate prompt `judge_v1`): personalization, tone, clarity and no invented commitments, each scored 1 to 4 with a one-line reason. Threshold: an average of at least 3, and every fact check passes.

`guardrail_trap` cases (`wel-07` "Monday Labs", `wel-08` "14-Day Fitness Studios") used to fail `check_welcome` on the customer name itself. Fixed: the name is masked before the date/duration/price checks. They are now ordinary cases that must pass.

Not implemented yet: the `null_contact` "no invented person name" check (the judge's `no_invented_commitments` score covers it only indirectly).

## handoff.jsonl (prompt `handoff_v1`, schema `HandoffDraft`)

```
{"id", "tags", "facts": <Tracker.handoff_facts() output: customer_name, plan, duration_days, csm_model,
                         tasks_done, tasks_total, phases[{name, tasks[{name, completed, due_date}]}], dates,
                         migration{result, last_check_at, failed_checks, csm_note}>}
```

Tags: `enterprise`, `growth`, `passed`, `failed`, `accepted`, `not_run`, `unicode`, `injection` (instructions planted in a task name or the CSM note).

Scoring (deterministic, no judge): `app.guardrails.output.check_handoff(summary, facts)` must return `[]` (customer, tier and migration result present; no date, price, email or person that isn't in the data) and the summary is at most 180 words. Threshold: every case passes. `make eval ARGS="--suite handoff"`.
