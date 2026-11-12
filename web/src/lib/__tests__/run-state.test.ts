import { describe, expect, it } from "vitest";
import { deriveRun, trackingPct } from "../run-state";
import type { AuditRow, Escalation, Run } from "../schemas";

const run = (p: Partial<Run>): Run =>
  ({
    id: "r1",
    status: "RECEIVED",
    customer_name: "Larkspur Health",
    created_at: "2026-11-12T10:00:00Z",
    ...p,
  }) as Run;
const row = (step: string, status = "ok", extra: Partial<AuditRow> = {}): AuditRow =>
  ({ step, status, ts: "2026-11-12T10:00:00Z", ...extra }) as AuditRow;
const states = (v: ReturnType<typeof deriveRun>) => v.steps.map((s) => s.state);

describe("stepper state mapping", () => {
  it("falls back to the status when the detail is not loaded", () => {
    const v = deriveRun(run({ status: "PROJECT_CREATED" }), null);
    expect(states(v)).toEqual(["done", "done", "done", "done", "done", "active", "todo"]);
    expect(v.current).toBe(5);
  });

  it("puts the call step in progress while a call is pending", () => {
    const v = deriveRun(run({ status: "AWAITING_CALL" }), {
      audit: [row("start"), row("validate"), row("lookup_ae")],
      pending: { type: "call", attempt: 2 } as never,
    });
    expect(v.steps[2].state).toBe("active");
    expect(v.steps[2].sub).toBe("Ringing, attempt 2");
    expect(v.title).toMatch(/Calling .* to confirm the plan tier/);
  });

  it("marks the blocking step as needing a human on a tier escalation", () => {
    const esc: Escalation = {
      key: "k",
      run_id: "r1",
      kind: "tier",
      status: "open",
      reason: "No clear tier",
    } as Escalation;
    const v = deriveRun(
      run({ status: "ESCALATED" }),
      {
        audit: [row("start"), row("validate"), row("decide_tier", "unclear"), row("decide_tier", "unclear")],
        pending: { type: "escalation", kind: "tier" } as never,
      },
      [esc],
    );
    expect(v.tone).toBe("warn");
    expect(v.steps[3].state).toBe("warn");
    expect(v.title).toBe("Needs a human: plan tier not confirmed");
    expect(v.group).toBe("attn");
  });

  it("never shows a tier that nobody confirmed", () => {
    const v = deriveRun(run({ status: "ESCALATED" }), {
      audit: [row("decide_tier", "unclear", { output: { tier: "enterprise" } })],
    });
    expect(v.tier).toBe("");
    expect(v.steps[3].state).not.toBe("done");
  });

  it("credits a tier set by a person", () => {
    const v = deriveRun(run({ status: "TIER_CONFIRMED" }), {
      audit: [
        row("start"),
        row("validate"),
        row("resolve", "ok", { input: { action: "set_tier", tier: "growth", by: "Dana" } }),
      ],
    });
    expect(v.tier).toBe("growth");
    expect(v.steps[3].sub).toBe("Growth, set by Dana");
    expect(v.steps[2].sub).toBe("Skipped by a person");
  });

  it("marks the current step failed when the run failed", () => {
    const v = deriveRun(run({ status: "FAILED", error: "boom" }), { audit: [row("start"), row("validate")] });
    expect(v.tone).toBe("bad");
    expect(v.steps.find((s) => s.state === "bad")).toBeTruthy();
    expect(v.sub).toBe("boom");
  });

  it("shows tracking progress once onboarding runs", () => {
    const v = deriveRun(
      run({ status: "COMPLETED", rocketlane_project_id: "1", slack_channel_id: "C1", plan_tier: "growth" }),
      {
        audit: [row("start"), row("validate")],
        tracking: { percent_done: 40, current_phase: "Training", phases: [] } as never,
      },
    );
    expect(v.steps[6]).toMatchObject({ state: "active", sub: "40% done" });
    expect(v.title).toBe("In onboarding: Training phase");
  });

  it("connectors light up only after a finished step", () => {
    const v = deriveRun(run({ status: "TIER_CONFIRMED", plan_tier: "growth" }), null);
    expect(v.steps.map((s) => s.reached)).toEqual([false, true, true, true, true, false, false]);
  });

  it("clamps tracking percentages", () => {
    expect(trackingPct({ percent_done: 140 } as never)).toBe(100);
    expect(trackingPct({ tasks_done: 1, tasks_total: 4 } as never)).toBe(25);
    expect(trackingPct(null)).toBe(0);
  });
});

describe("tracking flags from Rocketlane", () => {
  it("shows a project flag as a warning on a running onboarding, not as a decision", () => {
    const flag = {
      key: "r1:tracking:phase-done:Kickoff:ab12",
      run_id: "r1",
      kind: "tracking",
      status: "open",
      reason: "The Kickoff phase was marked Completed in Rocketlane but 2 of its tasks are still open.",
    } as Escalation;
    const v = deriveRun(run({ status: "COMPLETED" }), { audit: [], pending: null }, [flag]);
    expect(v.tone).toBe("warn");
    expect(v.title).toBe("Onboarding running. Project needs a look");
    expect(v.sub).toContain("Kickoff");
    expect(v.steps[6].state).toBe("warn");
  });
});
