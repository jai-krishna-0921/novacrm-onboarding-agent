import { describe, expect, it } from "vitest";
import { latencyBucketTooltip, llmDayTooltip, runsDayTooltip, toolBarTooltip } from "../chart-tooltip";
import { countdown, fmtMs, fmtUsd } from "../format";

const val = (c: { rows: { label: string; value: string }[] }, label: string) =>
  c.rows.find((r) => r.label === label)?.value;

describe("chart tooltip content", () => {
  it("LLM day shows date, calls, cost, tokens and latency", () => {
    const c = llmDayTooltip({
      day: "2026-11-03",
      calls: 7,
      cost_usd: 0.01234,
      input_tokens: 12000,
      output_tokens: 900,
      avg_latency_ms: 1840,
      p95_latency_ms: 5210,
    });
    expect(c.title).toMatch(/3 Nov 2026/);
    expect(val(c, "LLM calls")).toBe("7");
    expect(val(c, "Cost (est.)")).toBe("$0.0123");
    expect(val(c, "Tokens in / out")).toBe("12,000 / 900");
    expect(val(c, "p95 latency")).toBe("5.21 s");
    expect(c.note).toBeUndefined();
  });
  it("an empty day says so and omits latency", () => {
    const c = llmDayTooltip({ day: "2026-11-04", calls: 0 });
    expect(c.note).toBe("No LLM calls that day");
    expect(val(c, "p95 latency")).toBeUndefined();
  });
  it("lists the models when given", () => {
    expect(val(llmDayTooltip({ day: "2026-11-04", calls: 1, models: ["claude-haiku-5-5"] }), "Models")).toBe(
      "claude-haiku-5-5",
    );
  });
  it("runs per day", () => {
    expect(val(runsDayTooltip({ day: "2026-11-05", runs: 3 }), "Runs started")).toBe("3");
  });
  it("latency bucket shows share of calls", () => {
    expect(val(latencyBucketTooltip("2-5 s", 5, 20), "Share of all calls")).toBe("25%");
    expect(val(latencyBucketTooltip("2-5 s", 0, 0), "Share of all calls")).toBe("0%");
  });
  it("tool bar shows successes, failures and latency", () => {
    const c = toolBarTooltip({
      tool: "slack_post_message",
      calls: 10,
      failed: 2,
      failure_rate: 0.2,
      avg_latency_ms: 300,
      p95_latency_ms: 1500,
    });
    expect(val(c, "Succeeded")).toBe("8");
    expect(val(c, "Failure rate")).toBe("20.0%");
    expect(val(c, "p95 latency")).toBe("1.50 s");
  });
  it("formatters", () => {
    expect(fmtMs(null)).toBe("-");
    expect(fmtMs(420)).toBe("420 ms");
    expect(fmtUsd(0.5, 2)).toBe("$0.50");
    const now = Date.parse("2026-11-12T10:00:00Z");
    expect(countdown("2026-11-12T10:04:12Z", now)).toBe("in 4m 12s");
    expect(countdown("2026-11-12T09:59:00Z", now)).toBe("due now");
  });
});
