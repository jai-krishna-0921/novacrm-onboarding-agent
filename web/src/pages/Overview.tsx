import { ArrowRight, Activity } from "lucide-react";
import { Link } from "react-router";
import { llmDayTooltip, runsDayTooltip, statusBarTooltip } from "@/lib/chart-tooltip";
import { cn } from "@/lib/cn";
import { ago, fmtDur, fmtInt, fmtUsd, lastDays, shortDay } from "@/lib/format";
import {
  useAgentsStatus,
  useEscalations,
  useLlmMetrics,
  useOverview,
  useRunDetails,
  useRuns,
} from "@/lib/queries";
import { deriveRun, STATUS, statusInfo } from "@/lib/run-state";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Stat } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Tip } from "@/components/ui/tooltip";
import { ChartTooltipCard } from "@/components/charts/ChartTooltip";
import { AreaLine, Bars } from "@/components/charts/charts";
import { usePageMeta } from "@/components/layout/page-meta";
import { StatusChip } from "@/components/run/chips";
import { AgentDot } from "./Agents";

const DAYS = 14;

const BAR: Record<string, string> = {
  active: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  idle: "bg-line-strong",
};

export function OverviewPage() {
  usePageMeta("Overview");
  const ov = useOverview();
  const llm = useLlmMetrics(DAYS);
  const runs = useRuns();
  const escs = useEscalations();
  const recent = [...(runs.data ?? [])]
    .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
    .slice(0, 6);
  // Runs that failed before extraction have no customer_name yet; the detail (audit) gives the subject line.
  const recentDetails = useRunDetails(recent);

  if (ov.isError) return <ErrorBox error={ov.error} />;
  const o = ov.data;
  const st = o?.runs_by_status ?? {};
  const n = (k: string) => st[k] ?? 0;
  const active = Object.entries(st)
    .filter(([k]) => (STATUS[k]?.group ?? "active") === "active")
    .reduce((a, [, v]) => a + v, 0);
  const perDay = Object.fromEntries((o?.runs_by_day ?? []).map((d) => [d.day, d.runs]));
  const runSeries = lastDays(DAYS).map((day) => ({ day, runs: perDay[day] ?? 0 }));
  const costSeries = (llm.data?.series ?? []).map((d) => ({ ...d, cost_usd: d.cost_usd ?? 0 }));
  const statusRows = Object.entries(st).sort((a, b) => b[1] - a[1]);
  const total = statusRows.reduce((a, [, v]) => a + v, 0);
  const max = Math.max(1, ...statusRows.map(([, v]) => v));

  return (
    <div className="flex flex-col gap-4">
      <AgentsStrip />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {!o
          ? Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-card" />)
          : [
              <Stat
                key="r"
                label="Runs"
                value={fmtInt(o.runs_total)}
                sub={`${fmtInt(active)} in progress`}
              />,
              <Stat
                key="c"
                label="Completed"
                value={fmtInt(n("COMPLETED") + n("HANDED_OFF"))}
                sub={`${fmtInt(n("HANDED_OFF"))} handed off`}
                tone="ok"
              />,
              <Stat
                key="h"
                label="Needs a human"
                value={fmtInt(o.escalations_open)}
                sub={o.escalations_open ? "Open escalations" : "Nothing waiting"}
                tone={o.escalations_open ? "warn" : undefined}
              />,
              <Stat
                key="e"
                label="Email to project"
                value={fmtDur(o.email_to_project.avg_seconds)}
                sub={
                  o.email_to_project.runs
                    ? `Average over ${fmtInt(o.email_to_project.runs)} runs`
                    : "No project yet"
                }
                tip="Time from the deal email arriving to the Rocketlane project being created, including the AE call."
              />,
              <Stat
                key="l"
                label="LLM cost (est.)"
                value={fmtUsd(o.llm_cost_usd, 3)}
                sub={`${fmtInt(o.llm_calls)} calls`}
                tip="Estimated from list prices per million tokens. Not a bill."
              />,
              <Stat
                key="k"
                label="Calls confirmed"
                value={`${fmtInt(o.calls.confirmed)} / ${fmtInt(o.calls.placed)}`}
                sub={`${fmtInt(o.calls.unclear)} unclear, ${fmtInt(o.calls.no_answer)} unanswered`}
                tip="AE calls where the plan tier was confirmed, out of all calls placed."
              />,
            ]}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Runs started" description={`Last ${DAYS} days`} />
          <CardBody>
            {o ? (
              <Bars
                data={runSeries}
                x="day"
                y="runs"
                xLabel={shortDay}
                tooltip={runsDayTooltip}
                label={`Runs started per day, last ${DAYS} days`}
              />
            ) : (
              <Skeleton className="h-[220px]" />
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="LLM cost per day" description="Estimated from list prices" />
          <CardBody>
            {llm.data ? (
              <AreaLine
                data={costSeries}
                x="day"
                y="cost_usd"
                xLabel={shortDay}
                yFormat={(v) => fmtUsd(v, 3)}
                tooltip={llmDayTooltip}
                label="Estimated LLM cost per day"
              />
            ) : (
              <Skeleton className="h-[220px]" />
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Runs by status" />
          <CardBody>
            {!o ? (
              <Skeleton className="h-40" />
            ) : statusRows.length ? (
              <ul className="flex flex-col gap-2">
                {statusRows.map(([k, v]) => {
                  const s = statusInfo(k);
                  return (
                    <li key={k}>
                      <Tip
                        content={
                          <ChartTooltipCard content={statusBarTooltip(s.label, v, total, s.explain)} />
                        }
                        bare
                        side="right"
                      >
                        <div
                          tabIndex={0}
                          className="grid grid-cols-[140px_1fr_40px] items-center gap-3 rounded-ctl px-1 py-1 hover:bg-sunken"
                        >
                          <span className="truncate text-sm text-ink-2">{s.label}</span>
                          <span className="h-2.5 overflow-hidden rounded-full bg-[#eef0f3]">
                            <span
                              className={cn("block h-full origin-left rounded-full", BAR[s.tone])}
                              style={{ transform: `scaleX(${v / max})` }}
                            />
                          </span>
                          <span className="tabular text-right text-sm font-medium text-ink">{fmtInt(v)}</span>
                        </div>
                      </Tip>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <EmptyState title="No runs yet">Runs appear when a deal email arrives.</EmptyState>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader
            title="Recent activity"
            actions={
              <Link
                to="/runs"
                className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline"
              >
                All runs
                <ArrowRight className="size-4" aria-hidden />
              </Link>
            }
          />
          {!runs.data ? (
            <CardBody>
              <Skeleton className="h-40" />
            </CardBody>
          ) : recent.length ? (
            <ul className="divide-y divide-line border-t border-line">
              {recent.map((r, i) => {
                const v = deriveRun(r, recentDetails[i]?.data ?? null, escs.data ?? []);
                return (
                  <li key={r.id}>
                    <Link
                      to={`/runs/${encodeURIComponent(r.id)}`}
                      state={{ from: "/overview" }}
                      className="grid grid-cols-[1fr_auto_auto] items-center gap-3 px-5 py-2.5 hover:bg-sunken"
                    >
                      <span className="truncate font-medium text-ink">{v.name}</span>
                      <StatusChip run={r} view={v} />
                      <span className="tabular w-16 text-right text-[13px] text-ink-3">
                        {ago(r.updated_at ?? r.created_at)}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState title="Nothing yet" />
          )}
        </Card>
      </div>
    </div>
  );
}

function AgentsStrip() {
  const q = useAgentsStatus();
  const agents = q.data?.agents ?? [];
  return (
    <Card className="flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3.5">
      <span className="flex items-center gap-2 text-sm font-medium text-ink">
        <Activity className="size-4 text-ink-3" aria-hidden />
        Agents
      </span>
      {q.isPending ? (
        <Skeleton className="h-5 w-72" />
      ) : q.isError ? (
        <span className="text-sm text-ink-3">Status unavailable</span>
      ) : (
        agents.map((a) => (
          <Tip
            key={a.id}
            content={`${a.last_activity || "No activity yet"}${a.last_activity_at ? `, ${ago(a.last_activity_at)}` : ""}`}
          >
            <span tabIndex={0} className="flex items-center gap-2 text-sm text-ink-2">
              <AgentDot status={a.status} />
              {a.name}
            </span>
          </Tip>
        ))
      )}
      <Link
        to="/agents"
        className="ml-auto inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline"
      >
        Agent status
        <ArrowRight className="size-4" aria-hidden />
      </Link>
    </Card>
  );
}
