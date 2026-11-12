import { z } from "zod";

// Response shapes of the FastAPI backend (backend/app/main.py, services/store.py). Lenient on purpose:
// optional/nullable where the store can omit a value, unknown keys stripped, JSON payloads kept as unknown.

const str = z.string().nullish();
const num = z.number().nullish();

export const Run = z.object({
  id: z.string(),
  seq: num,
  status: str,
  gmail_message_id: str,
  opportunity_id: str,
  customer_key: str,
  customer_name: str,
  plan_tier: str,
  rocketlane_project_id: z
    .union([z.string(), z.number()])
    .nullish()
    .transform((v) => (v == null ? null : String(v))),
  slack_channel_id: str,
  error: str,
  created_at: str,
  updated_at: str,
});
export type Run = z.infer<typeof Run>;
export const Runs = z.array(Run);

export const Escalation = z.object({
  key: z.string(),
  run_id: z.string(),
  reason: str,
  kind: str,
  agent: str,
  status: str,
  resolved_by: str,
  resolution: z.unknown().optional(),
  created_at: str,
});
export type Escalation = z.infer<typeof Escalation>;
export const Escalations = z.array(Escalation);

export const AuditRow = z.object({
  id: num,
  run_id: str,
  ts: str,
  agent: str,
  step: str,
  input: z.unknown().optional(),
  output: z.unknown().optional(),
  rationale: str,
  status: str,
});
export type AuditRow = z.infer<typeof AuditRow>;

export const ToolCallRow = z.object({
  id: num,
  run_id: str,
  customer_name: str,
  ts: str,
  agent: str,
  tool: str,
  status: str,
  attempt: num,
  latency_ms: num,
  idempotency_key: str,
  args: z.unknown().optional(),
  result: z.unknown().optional(),
});
export type ToolCallRow = z.infer<typeof ToolCallRow>;

export const LlmCallRow = z.object({
  id: num,
  run_id: str,
  customer_name: str,
  ts: str,
  agent: str,
  node: str,
  model: str,
  prompt_version: str,
  input_tokens: num,
  output_tokens: num,
  latency_ms: num,
  cost_usd: z.coerce.number().nullish(),
  stop_reason: str,
});
export type LlmCallRow = z.infer<typeof LlmCallRow>;

export const MigrationCheckRow = z.object({
  run_id: str,
  batch_id: str,
  ts: str,
  check_name: z.string(),
  passed: z.boolean(),
  details: z
    .object({
      expected: z.unknown().optional(),
      actual: z.unknown().optional(),
      sample_ids: z.array(z.unknown()).nullish(),
    })
    .nullish(),
});
export type MigrationCheckRow = z.infer<typeof MigrationCheckRow>;

export const Phase = z.object({ name: z.string(), done: z.number(), total: z.number() });
export const Tracking = z.object({
  current_phase: str,
  percent_done: num,
  tasks_done: num,
  tasks_total: num,
  phases: z.array(Phase).nullish(),
  last_synced_at: str,
  handoff_summary: str,
  handed_off_at: str,
});
export type Tracking = z.infer<typeof Tracking>;

export const Pending = z
  .object({
    type: z.string(),
    kind: str,
    reason: str,
    attempt: num,
    provider_call_id: str,
    until: z.union([z.number(), z.string()]).nullish(),
    since: z.union([z.number(), z.string()]).nullish(),
  })
  .nullish();
export type Pending = z.infer<typeof Pending>;

export const RunDetail = z.object({
  run: Run,
  pending: Pending,
  audit: z.array(AuditRow),
  tool_calls: z.array(ToolCallRow).default([]),
  migration_checks: z.array(MigrationCheckRow).default([]),
  tracking: Tracking.nullish(),
  llm: z
    .object({
      calls: z.number().default(0),
      input_tokens: z.number().default(0),
      output_tokens: z.number().default(0),
      cost_usd: z.coerce.number().default(0),
      by_node: z.array(LlmCallRow).default([]),
    })
    .default({ calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, by_node: [] }),
});
export type RunDetail = z.infer<typeof RunDetail>;

export const Me = z.object({ user: z.string().nullish() });
export const UiConfig = z.object({ rocketlane_app_url: z.string().default("") });

export const Overview = z.object({
  runs_total: z.number().default(0),
  runs_by_status: z.record(z.string(), z.number()).default({}),
  runs_by_day: z.array(z.object({ day: z.string(), runs: z.number() })).default([]),
  escalations_open: z.number().default(0),
  email_to_project: z.object({ runs: z.number().default(0), avg_seconds: num }).default({ runs: 0 }),
  calls: z
    .object({
      placed: z.number().default(0),
      confirmed: z.number().default(0),
      unclear: z.number().default(0),
      no_answer: z.number().default(0),
    })
    .default({ placed: 0, confirmed: 0, unclear: 0, no_answer: 0 }),
  llm_calls: z.number().default(0),
  llm_cost_usd: z.coerce.number().default(0),
});
export type Overview = z.infer<typeof Overview>;

const LlmGroup = z.object({
  calls: z.number().default(0),
  input_tokens: z.number().default(0),
  output_tokens: z.number().default(0),
  cost_usd: z.coerce.number().default(0),
  avg_latency_ms: num,
  p50_latency_ms: num,
  p95_latency_ms: num,
});
export type LlmGroup = z.infer<typeof LlmGroup>;

export const LlmMetrics = z.object({
  totals: LlmGroup,
  by_model: z.array(LlmGroup.extend({ model: str })).default([]),
  by_node: z.array(LlmGroup.extend({ agent: str, node: str })).default([]),
  by_prompt: z.array(LlmGroup.extend({ prompt_version: str })).default([]),
  series: z.array(LlmGroup.partial().extend({ day: z.string() })).default([]),
  latency_buckets: z.array(z.number()).default([]),
  latency_bucket_labels: z.array(z.string()).default([]),
  prices_usd_per_mtok: z.record(z.string(), z.tuple([z.number(), z.number()])).default({}),
});
export type LlmMetrics = z.infer<typeof LlmMetrics>;

export const ToolStat = z.object({
  tool: z.string(),
  calls: z.number(),
  ok: z.number().default(0),
  failed: z.number().default(0),
  replayed: z.number().default(0),
  blocked: z.number().default(0),
  failure_rate: z.number().default(0),
  avg_latency_ms: num,
  p95_latency_ms: num,
});
export type ToolStat = z.infer<typeof ToolStat>;
export const ToolMetrics = z.object({ tools: z.array(ToolStat).default([]) });

export const page = <T extends z.ZodType>(row: T) =>
  z.object({ total: z.number(), limit: num, offset: num, rows: z.array(row) });
export const LlmCallsPage = page(LlmCallRow);
export const ToolCallsPage = page(ToolCallRow);

export const ResolveResult = z.object({ ok: z.boolean().optional(), status: str });
export const MigrationCheckResult = z.object({
  batch_id: str,
  passed: z.boolean(),
  results: z
    .array(z.object({ name: z.string(), passed: z.boolean(), details: z.unknown().optional() }))
    .default([]),
  escalation: z.unknown().optional(),
});

// ---------- Account Executives (new endpoints) ----------
export const Ae = z.object({
  email: z.string(),
  name: z.string(),
  phone_masked: str,
  active: z.boolean(),
  updated_at: str,
});
export type Ae = z.infer<typeof Ae>;
export const Aes = z.array(Ae);

// ---------- Migration demo data (new endpoints) ----------
export const MigrationSummary = z.object({
  source_count: z.number(),
  dest_count: z.number(),
  problems: z.object({
    duplicates: z.array(z.union([z.string(), z.number(), z.null()])).default([]),
    missing_required: z.array(z.union([z.string(), z.number(), z.null()])).default([]),
    missing_in_dest: z.array(z.union([z.string(), z.number(), z.null()])).default([]),
    not_in_source: z.array(z.union([z.string(), z.number(), z.null()])).default([]),
  }),
  // Table columns per side; `missing` (source columns the destination lacks) fails the schema check, `extra`
  // destination columns are allowed. Defaults keep payloads from before the column check parsing.
  columns: z
    .object({
      source: z.array(z.string()).default([]),
      dest: z.array(z.string()).default([]),
      missing: z.array(z.string()).default([]),
      extra: z.array(z.string()).default([]),
    })
    .default({ source: [], dest: [], missing: [], extra: [] }),
  last_check: z
    .object({
      run_id: str,
      batch_id: str,
      ts: str,
      passed: z.boolean().nullish(),
    })
    .loose()
    .nullish(),
});
export type MigrationSummary = z.infer<typeof MigrationSummary>;

// Every column of that side's table comes through (values may be null); only these are typed.
export const MigrationDataRow = z
  .object({
    id: z.union([z.string(), z.number()]).nullish(),
    name: str,
    email: str,
    problems: z.array(z.string()).default([]),
  })
  .loose();
export type MigrationDataRow = z.infer<typeof MigrationDataRow>;
export const MigrationData = z.object({ total: z.number(), rows: z.array(MigrationDataRow) });

// ---------- Agents status (new endpoint) ----------
export const AgentStatus = z.object({
  id: z.string(),
  name: z.string(),
  description: str,
  status: z.enum(["running", "idle", "degraded", "down"]).catch("idle"),
  last_activity_at: str,
  last_activity: str,
  stats: z.record(z.string(), z.number()).default({}),
  next_run_at: str,
});
export type AgentStatus = z.infer<typeof AgentStatus>;
export const AgentsStatus = z.object({
  agents: z.array(AgentStatus).default([]),
  scheduler: z
    .object({
      last_tick_at: str,
      interval_seconds: num,
      status: z.enum(["running", "stalled"]).catch("stalled"),
    })
    .nullish(),
  gmail: z.object({ last_sync_at: str, last_push_at: str, watch_expires_at: str }).nullish(),
  integrations: z
    .array(z.object({ name: z.string(), ok: z.boolean(), detail: str, checked_at: str }))
    .default([]),
  rocketlane_automations: z.array(z.object({ name: z.string(), trigger: str, notify: str })).default([]),
});
export type AgentsStatus = z.infer<typeof AgentsStatus>;
