import type { AuditRow, Escalation, Pending, Run, Tracking } from "./schemas";
import { ago, cap, fmtTime, human, str } from "./format";

// Mirrors backend/app/graph/root.py ALLOWED (the API re-validates every resolution).
export const ALLOWED: Record<string, string[]> = {
  tier: ["set_tier", "retry", "cancel"],
  input: ["retry", "cancel"],
  duplicate: ["retry", "cancel"],
  system: ["retry", "cancel"],
};
export const TIERS = ["enterprise", "growth"] as const;

export const KIND: Record<string, { label: string; explain: string }> = {
  tier: {
    label: "Plan tier not confirmed",
    explain: "The AE call did not confirm a plan tier. No project is created until a tier is confirmed.",
  },
  input: {
    label: "Deal email needs a fix",
    explain: "The deal email is missing details or covers more than one deal.",
  },
  duplicate: {
    label: "Possible duplicate project",
    explain: "A project for this customer may already exist. A person checks before a second one is made.",
  },
  system: {
    label: "System problem",
    explain: "A step failed or the worker stopped. Retry resumes from the last checkpoint.",
  },
  migration: {
    label: "Migration check failed",
    explain: "The data migration check found problems in the destination data.",
  },
};
export const kindLabel = (k: string | null | undefined) => KIND[k ?? ""]?.label ?? "Needs review";

export type Tone = "active" | "ok" | "warn" | "bad" | "idle";
export type Group = "attn" | "active" | "done";

export const STATUS: Record<string, { label: string; tone: Tone; group: Group; explain: string }> = {
  RECEIVED: {
    label: "Checking",
    tone: "active",
    group: "active",
    explain: "Reading and validating the deal email.",
  },
  AWAITING_CLARIFICATION: {
    label: "Waiting on AE",
    tone: "warn",
    group: "attn",
    explain: "Details were missing. The AE was emailed and the run continues when they reply.",
  },
  AWAITING_CALL: {
    label: "On a call",
    tone: "active",
    group: "active",
    explain: "Calling the AE to confirm the plan tier.",
  },
  AWAITING_RETRY: {
    label: "Retrying call",
    tone: "active",
    group: "active",
    explain: "The AE did not answer. Another call is scheduled.",
  },
  TIER_CONFIRMED: {
    label: "Creating project",
    tone: "active",
    group: "active",
    explain: "Tier confirmed. Creating the Rocketlane project from the tier template.",
  },
  PROJECT_CREATED: {
    label: "Setting up Slack",
    tone: "active",
    group: "active",
    explain: "Project created. Setting up the customer Slack channel.",
  },
  COMPLETED: {
    label: "In onboarding",
    tone: "ok",
    group: "done",
    explain: "Project and Slack channel are ready. Progress syncs from Rocketlane.",
  },
  HANDED_OFF: {
    label: "Handed off",
    tone: "ok",
    group: "done",
    explain: "All phases done. Handed off to support.",
  },
  ESCALATED: {
    label: "Needs a human",
    tone: "warn",
    group: "attn",
    explain: "The run is paused until a person decides what to do.",
  },
  FAILED: { label: "Error", tone: "bad", group: "attn", explain: "The run stopped with an error." },
  CANCELLED: { label: "Cancelled", tone: "idle", group: "done", explain: "A person cancelled this run." },
  NOT_A_DEAL: {
    label: "Not a deal email",
    tone: "idle",
    group: "done",
    explain: "The email was not a closed-won deal, so nothing was started.",
  },
};
export const TERMINAL = new Set(["HANDED_OFF", "CANCELLED", "FAILED", "NOT_A_DEAL"]);

export function statusInfo(status: string | null | undefined) {
  const s = String(status ?? "").toUpperCase();
  return (
    STATUS[s] ?? {
      label: human(status || "unknown"),
      tone: "idle" as Tone,
      group: "active" as Group,
      explain: "",
    }
  );
}

export const STEPS = [
  ["email", "Email received"],
  ["checked", "Checked"],
  ["call", "Calling AE"],
  ["tier", "Tier confirmed"],
  ["project", "Rocketlane project"],
  ["slack", "Slack channel"],
  ["tracking", "Tracking and handoff"],
] as const;

export type StepState = "done" | "todo" | "active" | "warn" | "bad" | "idle";
export type Step = { key: string; label: string; state: StepState; sub: string; reached: boolean };

export type RunView = {
  steps: Step[];
  current: number; // index of the step being worked on, 7 when all done
  tone: Tone;
  title: string;
  sub: string;
  tier: string;
  ae: string;
  name: string;
  esc: Escalation | null;
  migrationEsc: Escalation | null;
  pct: number;
  tracking: Tracking | null;
  group: Group;
};

type Detail = { audit?: AuditRow[]; pending?: Pending; tracking?: Tracking | null } | null | undefined;

const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" ? (v as Record<string, unknown>) : {};

function lastAudit(audit: AuditRow[], step: string, pred?: (a: AuditRow) => boolean): AuditRow | null {
  for (let i = audit.length - 1; i >= 0; i--)
    if (audit[i].step === step && (!pred || pred(audit[i]))) return audit[i];
  return null;
}

export function runName(run: Run, audit: AuditRow[] = []): string {
  if (run.customer_name) return run.customer_name;
  const ex = lastAudit(audit, "extract_fields");
  const exName = obj(ex?.output).customer_name;
  if (exName) return String(exName);
  const subj = obj(lastAudit(audit, "start")?.input).subject;
  if (subj) return String(subj).replace(/^\s*(re:|fwd?:)?\s*closed[\s-]*won\s*[:-]?\s*/i, "") || String(subj);
  return "New deal";
}

export function trackingPct(tr: Tracking | null | undefined): number {
  if (!tr) return 0;
  let p = tr.percent_done;
  if (p == null && tr.tasks_total) p = (100 * (tr.tasks_done ?? 0)) / tr.tasks_total;
  const n = Number(p);
  if (!isFinite(n)) return 0;
  return Math.round(Math.max(0, Math.min(100, n > 0 && n < 1 ? n * 100 : n)));
}

/** The 7-step pipeline, a plain-language headline and a tone, from a run and (optionally) its detail. */
export function deriveRun(run: Run, d: Detail, openEscs: Escalation[] = []): RunView {
  const audit = d?.audit ?? [];
  const pending = d?.pending ?? null;
  const tr = d?.tracking ?? null;
  const status = String(run.status ?? "").toUpperCase();
  const escs = openEscs.filter((e) => e.run_id === run.id && (e.status ?? "open") === "open");
  const esc = escs.find((e) => e.kind !== "migration") ?? null;
  const migrationEsc = escs.find((e) => e.kind === "migration") ?? null;
  const decisions = audit.filter((a) => a.step === "decide_tier");
  const confirmed = decisions.find((a) => a.status === "confirmed");
  const connected = decisions.filter((a) => a.status !== "no_answer");
  const manual = lastAudit(
    audit,
    "resolve",
    (a) => obj(a.input).action === "set_tier" && !!obj(a.input).tier,
  );
  const tier = String(run.plan_tier || obj(confirmed?.output).tier || obj(manual?.input).tier || "");
  const aeRow = lastAudit(audit, "tool:ae_lookup", (a) => !!obj(a.output).name);
  const ae = aeRow ? String(obj(aeRow.output).name) : "";
  const onCall =
    (pending && (pending.type === "call" || pending.type === "wait")) ||
    status === "AWAITING_CALL" ||
    status === "AWAITING_RETRY";
  const has = (step: string) => audit.some((a) => a.step === step && a.status !== "failed");
  const statusIdx: Record<string, number> = {
    RECEIVED: 1,
    AWAITING_CLARIFICATION: 1,
    AWAITING_CALL: 2,
    AWAITING_RETRY: 2,
    TIER_CONFIRMED: 4,
    PROJECT_CREATED: 5,
    COMPLETED: 6,
    HANDED_OFF: 7,
  };

  const done = [
    true,
    (has("validate") && !lastAudit(audit, "validate", (a) => a.status !== "ok")) ||
      has("lookup_ae") ||
      decisions.length > 0 ||
      !!tier,
    !onCall && (connected.length > 0 || !!tier),
    !!tier,
    !!run.rocketlane_project_id || has("create_project"),
    !!run.slack_channel_id || has("setup_channel"),
    status === "HANDED_OFF" || !!tr?.handed_off_at,
  ];
  // The runs list may not have the detail yet: fall back to the status.
  if (!audit.length && statusIdx[status] !== undefined)
    for (let i = 0; i < 7; i++) done[i] = i < statusIdx[status];
  if (onCall) done[2] = false;
  let cur = done.indexOf(false);
  if (onCall) cur = 2;
  if (cur < 0) cur = 7;

  const steps: Step[] = STEPS.map(([key, label], i) => ({
    key,
    label,
    state: done[i] ? "done" : "todo",
    sub: "",
    reached: false,
  }));
  const st = lastAudit(audit, "start");
  steps[0].sub = fmtTime(st?.ts ?? run.created_at);
  if (done[1]) steps[1].sub = "Sender and fields OK";
  if (done[2])
    steps[2].sub =
      !connected.length && manual ? "Skipped by a person" : ae ? `Spoke with ${ae}` : "Call connected";
  if (done[3])
    steps[3].sub =
      manual && !confirmed ? `${cap(tier)}, set by ${String(obj(manual.input).by || "a person")}` : cap(tier);
  if (done[4]) steps[4].sub = run.rocketlane_project_id ? `Project #${run.rocketlane_project_id}` : "Created";
  if (done[5]) steps[5].sub = has("slack_connect") ? "Ready, invite customer by hand" : "Channel ready";
  if (done[6]) steps[6].sub = "Handed off to support";

  const pct = trackingPct(tr);
  const reason = esc?.reason || pending?.reason || lastAudit(audit, "escalate")?.rationale || "";
  let tone: Tone = "active";
  let title = "Working on it";
  let sub = "";
  const c = cur < 7 ? steps[cur] : null;

  if (status === "FAILED") {
    tone = "bad";
    title = "Stopped with an error";
    sub = run.error || "";
    if (c) Object.assign(c, { state: "bad", sub: "Error" });
  } else if (status === "CANCELLED") {
    tone = "idle";
    title = "Cancelled";
    sub = "Someone cancelled this run. Nothing was created after that point.";
    if (c) Object.assign(c, { state: "idle", sub: "Cancelled" });
  } else if (status === "NOT_A_DEAL") {
    tone = "idle";
    title = "Not a deal email";
    sub = "Nothing to onboard.";
    if (c) Object.assign(c, { state: "idle", sub: "Skipped" });
  } else if (status === "ESCALATED" || (esc && pending?.type === "escalation")) {
    const kind = esc?.kind || pending?.kind || "system";
    tone = "warn";
    title = `Needs a human: ${kindLabel(kind).toLowerCase()}`;
    sub = reason;
    if (c) {
      c.state = "warn";
      if (kind === "tier" && cur === 2)
        c.sub = `No answer after ${decisions.length || "several"} ${decisions.length === 1 ? "try" : "tries"}`;
      else if (kind === "tier") c.sub = "Tier unclear on the call";
      else c.sub = kindLabel(kind);
    }
  } else if (status === "AWAITING_CLARIFICATION") {
    tone = "warn";
    title = "Waiting for the AE to send missing details";
    const issues = obj(lastAudit(audit, "validate")?.output).issues;
    sub =
      Array.isArray(issues) && issues.length
        ? `Missing or invalid: ${issues.map(str).join(", ")}`
        : "The AE was emailed. The run continues when they reply.";
    if (c) Object.assign(c, { state: "warn", sub: "Asked AE for details" });
  } else if (onCall) {
    const attempt = pending?.attempt || decisions.length + 1;
    if (pending?.type === "wait") {
      title = "Call not answered. Trying again soon";
      sub =
        pending.until != null
          ? `Next attempt at ${fmtTime(pending.until)}`
          : "The next attempt is scheduled.";
      steps[2].sub = "Retrying";
    } else {
      title = `Calling ${ae || "the AE"} to confirm the plan tier`;
      sub = `Attempt ${attempt}. This page updates when the call ends.`;
      steps[2].sub = `Ringing, attempt ${attempt}`;
    }
    steps[2].state = "active";
  } else if (status === "HANDED_OFF" || cur === 7) {
    tone = "ok";
    title = "Handed off to support";
    sub = tr?.handed_off_at ? `Handed off ${fmtTime(tr.handed_off_at, true)}` : "All phases complete.";
  } else if (status === "COMPLETED" || cur === 6) {
    if (migrationEsc) {
      tone = "warn";
      title = "Onboarding running. Migration needs review";
      sub = migrationEsc.reason || "";
      Object.assign(steps[6], { state: "warn", sub: "Migration check failed" });
    } else if (tr) {
      tone = "ok";
      title = tr.current_phase ? `In onboarding: ${tr.current_phase} phase` : "In onboarding";
      sub = `${pct}% of tasks done. Last synced ${ago(tr.last_synced_at) || "recently"}.`;
      Object.assign(steps[6], { state: "active", sub: `${pct}% done` });
    } else {
      tone = "ok";
      title = "Onboarding is set up";
      sub = "Rocketlane project and Slack channel are ready.";
      steps[6].sub = "Starts at next sync";
    }
  } else if (c) {
    Object.assign(c, { state: "active", sub: "In progress" });
    title = `Working: ${c.label.toLowerCase()}`;
  }
  steps.forEach((s, i) => {
    s.reached = i > 0 && steps[i - 1].state === "done";
  });
  const group: Group = esc || migrationEsc ? "attn" : statusInfo(status).group;
  return {
    steps,
    current: cur,
    tone,
    title,
    sub,
    tier,
    ae,
    name: runName(run, audit),
    esc,
    migrationEsc,
    pct,
    tracking: tr,
    group,
  };
}
