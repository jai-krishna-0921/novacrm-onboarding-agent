import { fmtInt, fmtMs, fmtUsd, longDay } from "./format";

// What every chart tooltip shows, as label/value rows. Kept pure so it is testable and the same everywhere.

export type TooltipRow = { label: string; value: string; tone?: "accent" | "ok" | "warn" | "bad" | "muted" };
export type TooltipContent = { title: string; rows: TooltipRow[]; note?: string };

export type DayPoint = {
  day: string;
  calls?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd?: number | null;
  p95_latency_ms?: number | null;
  avg_latency_ms?: number | null;
  runs?: number | null;
  models?: string[];
};

export function runsDayTooltip(p: DayPoint): TooltipContent {
  const n = p.runs ?? 0;
  return {
    title: longDay(p.day),
    rows: [{ label: "Runs started", value: fmtInt(n), tone: "accent" }],
    note: n === 0 ? "No deal emails that day" : undefined,
  };
}

export function llmDayTooltip(p: DayPoint): TooltipContent {
  const calls = p.calls ?? 0;
  const rows: TooltipRow[] = [
    { label: "LLM calls", value: fmtInt(calls), tone: "accent" },
    { label: "Cost (est.)", value: fmtUsd(p.cost_usd ?? 0) },
    { label: "Tokens in / out", value: `${fmtInt(p.input_tokens)} / ${fmtInt(p.output_tokens)}` },
  ];
  if (calls > 0) {
    rows.push({ label: "Avg latency", value: fmtMs(p.avg_latency_ms) });
    rows.push({ label: "p95 latency", value: fmtMs(p.p95_latency_ms) });
  }
  if (p.models && p.models.length) rows.push({ label: "Models", value: p.models.join(", "), tone: "muted" });
  return { title: longDay(p.day), rows, note: calls === 0 ? "No LLM calls that day" : undefined };
}

export function latencyBucketTooltip(label: string, count: number, total: number): TooltipContent {
  const share = total ? Math.round((100 * count) / total) : 0;
  return {
    title: `Latency ${label}`,
    rows: [
      { label: "Calls", value: fmtInt(count), tone: "accent" },
      { label: "Share of all calls", value: `${share}%` },
    ],
  };
}

export function toolBarTooltip(t: {
  tool: string;
  calls: number;
  ok?: number;
  failed: number;
  failure_rate?: number;
  avg_latency_ms?: number | null;
  p95_latency_ms?: number | null;
}): TooltipContent {
  return {
    title: t.tool,
    rows: [
      { label: "Succeeded", value: fmtInt(t.ok ?? t.calls - t.failed), tone: "ok" },
      { label: "Failed", value: fmtInt(t.failed), tone: t.failed ? "bad" : "muted" },
      { label: "Failure rate", value: `${((t.failure_rate ?? 0) * 100).toFixed(1)}%` },
      { label: "Avg latency", value: fmtMs(t.avg_latency_ms) },
      { label: "p95 latency", value: fmtMs(t.p95_latency_ms) },
    ],
  };
}

export function statusBarTooltip(
  label: string,
  count: number,
  total: number,
  explain: string,
): TooltipContent {
  return {
    title: label,
    rows: [
      { label: "Runs", value: fmtInt(count), tone: "accent" },
      { label: "Share", value: `${total ? Math.round((100 * count) / total) : 0}%` },
    ],
    note: explain,
  };
}
