import {
  ArrowDown,
  ArrowRight,
  Bell,
  Bot,
  CheckCircle2,
  Clock,
  FolderKanban,
  Mail,
  MessageSquare,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { ago, countdown, fmtDateTime, fmtInt, human, plainError } from "@/lib/format";
import { useAgentsStatus } from "@/lib/queries";
import type { AgentStatus, AgentsStatus } from "@/lib/schemas";
import { Badge, TipBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { usePageMeta } from "@/components/layout/page-meta";

const INTEGRATION_NAMES: Record<string, string> = {
  gmail: "Gmail",
  retell: "Retell",
  rocketlane: "Rocketlane",
  slack: "Slack",
  anthropic: "Anthropic",
};

const STATUS: Record<
  AgentStatus["status"],
  { label: string; dot: string; tone: "ok" | "idle" | "warn" | "bad"; explain: string }
> = {
  running: { label: "Running", dot: "bg-ok", tone: "ok", explain: "Working on something right now." },
  idle: { label: "Idle", dot: "bg-idle", tone: "idle", explain: "Up and waiting for work." },
  degraded: {
    label: "Degraded",
    dot: "bg-warn",
    tone: "warn",
    explain: "Up, but a dependency is failing or slow.",
  },
  down: {
    label: "Down",
    dot: "bg-bad",
    tone: "bad",
    explain: "Not processing work. Check the integrations below.",
  },
};

export function AgentDot({ status }: { status: AgentStatus["status"] }) {
  const s = STATUS[status] ?? STATUS.idle;
  return (
    <span className="relative flex size-2.5 shrink-0" aria-hidden>
      {status === "running" ? (
        <span className="absolute inset-0 animate-ping rounded-full bg-ok/60 motion-reduce:hidden" />
      ) : null}
      <span className={cn("relative size-2.5 rounded-full", s.dot)} />
    </span>
  );
}

const AGENT_TITLE: Record<string, string> = {
  intake: "Agent 1: Intake and routing",
  comms: "Agent 2: Communication",
  tracking: "Agent 3: Project tracking",
};

function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export function AgentsPage() {
  usePageMeta("Agents");
  const q = useAgentsStatus();
  if (q.isError) return <ErrorBox error={q.error} />;
  const d = q.data;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[14.5px] text-ink-2">
        What each agent is doing, and the services they depend on. Refreshes every 10 seconds.
      </p>
      <div className="grid gap-4 lg:grid-cols-3">
        {!d
          ? Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-56 rounded-card" />)
          : d.agents.map((a) => <AgentCard key={a.id} a={a} />)}
      </div>
      <FlowStrip />
      <div className="grid gap-4 lg:grid-cols-2">
        {d ? <SchedulerCard d={d} /> : <Skeleton className="h-52 rounded-card" />}
        {d ? <AutomationsCard d={d} /> : <Skeleton className="h-52 rounded-card" />}
      </div>
      {d ? <IntegrationsCard d={d} /> : <Skeleton className="h-52 rounded-card" />}
    </div>
  );
}

function AgentCard({ a }: { a: AgentStatus }) {
  const now = useNow();
  const s = STATUS[a.status] ?? STATUS.idle;
  const stats = Object.entries(a.stats);
  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-ink">{AGENT_TITLE[a.id] ?? a.name}</h2>
          {a.description ? <p className="mt-0.5 text-[13px] text-ink-3">{a.description}</p> : null}
        </div>
        <TipBadge tone={s.tone} tip={s.explain}>
          <span className="inline-flex items-center gap-1.5">
            <AgentDot status={a.status} />
            {s.label}
          </span>
        </TipBadge>
      </div>
      <div className="mt-3 border-t border-line px-5 py-3">
        <div className="text-[12.5px] text-ink-3">Last activity</div>
        <div className="mt-0.5 text-sm text-ink">{plainError(a.last_activity) || "No activity yet"}</div>
        {a.last_activity_at ? (
          <Tip content={fmtDateTime(a.last_activity_at)}>
            <span tabIndex={0} className="text-[12.5px] text-ink-3">
              {ago(a.last_activity_at, now)}
            </span>
          </Tip>
        ) : null}
      </div>
      {stats.length ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-line px-5 py-3">
          {stats.map(([k, v]) => (
            <div key={k}>
              <dt className="text-[12.5px] text-ink-3">{human(k)}</dt>
              <dd className="tabular text-[17px] font-semibold text-ink">{fmtInt(v)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {a.next_run_at ? (
        <div className="mt-auto flex items-center justify-between border-t border-line px-5 py-3 text-sm">
          <span className="text-ink-3">Next sync</span>
          <Tip content={fmtDateTime(a.next_run_at)}>
            <span tabIndex={0} className="tabular font-medium text-ink" aria-live="off">
              {countdown(a.next_run_at, now)}
            </span>
          </Tip>
        </div>
      ) : null}
    </Card>
  );
}

type Node = { icon: LucideIcon; label: string; tip: string; agent?: boolean };

function FlowNode({ n }: { n: Node }) {
  return (
    <Tip content={n.tip}>
      <span
        tabIndex={0}
        className={cn(
          "inline-flex h-10 shrink-0 items-center gap-2 rounded-ctl border px-3 text-sm font-medium",
          n.agent
            ? "border-accent-line bg-accent-soft text-accent-strong"
            : "border-line-strong bg-surface text-ink-2",
        )}
      >
        <n.icon className="size-4" aria-hidden />
        {n.label}
      </span>
    </Tip>
  );
}

function Arrow() {
  return (
    <>
      <ArrowRight className="hidden size-4 shrink-0 text-ink-3 md:block" aria-hidden />
      <ArrowDown className="size-4 shrink-0 text-ink-3 md:hidden" aria-hidden />
    </>
  );
}

const MAIN: Node[] = [
  {
    icon: Mail,
    label: "Gmail",
    tip: "Closed-won deal emails arrive in the CS inbox. Gmail push notifies the app; a poll is the fallback.",
  },
  {
    icon: Bot,
    label: "Agent 1",
    agent: true,
    tip: "Intake and routing: reads the email, checks fields and the sender, phones the AE to confirm the tier, creates the project.",
  },
  {
    icon: FolderKanban,
    label: "Rocketlane",
    tip: "The project is created from the tier template CS maintains. CS owns phases, durations and owners.",
  },
  {
    icon: Bot,
    label: "Agent 2",
    agent: true,
    tip: "Communication: creates the customer Slack channel and posts a welcome checked against the deal facts.",
  },
  {
    icon: MessageSquare,
    label: "Slack",
    tip: "Customer channel plus an internal handoff summary in #cs-handoffs.",
  },
];

function FlowStrip() {
  return (
    <Card>
      <CardHeader title="How it fits together" description="Hover a step for what it does." />
      <CardBody className="flex flex-col gap-4">
        <div className="flex flex-col items-start gap-2 md:flex-row md:flex-wrap md:items-center">
          {MAIN.map((n, i) => (
            <div key={n.label} className="contents">
              {i > 0 ? <Arrow /> : null}
              <FlowNode n={n} />
            </div>
          ))}
        </div>
        <div className="grid gap-3 border-t border-line pt-4 md:grid-cols-2">
          <div className="flex flex-wrap items-center gap-2 text-sm text-ink-3">
            <FlowNode
              n={{
                icon: Bot,
                label: "Agent 3",
                agent: true,
                tip: "Project tracking: syncs task progress from Rocketlane on a schedule, runs the migration check, writes the handoff.",
              }}
            />
            watches
            <FlowNode
              n={{
                icon: FolderKanban,
                label: "Rocketlane",
                tip: "Task completion and phase progress are read from here.",
              }}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-ink-3">
            <FlowNode
              n={{
                icon: Bell,
                label: "Overdue automations",
                tip: "Native Rocketlane automations notify owners about overdue tasks. Configured in Rocketlane, not in code.",
              }}
            />
            run inside Rocketlane
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function SchedulerCard({ d }: { d: AgentsStatus }) {
  const now = useNow(5000);
  const sch = d.scheduler;
  const g = d.gmail;
  const expires = g?.watch_expires_at ? new Date(g.watch_expires_at).getTime() : NaN;
  const hoursLeft = isNaN(expires) ? null : (expires - now) / 3_600_000;
  const watchTone = hoursLeft == null ? "idle" : hoursLeft <= 0 ? "bad" : hoursLeft < 48 ? "warn" : "ok";
  return (
    <Card>
      <CardHeader title="Scheduler and Gmail" />
      <CardBody>
        <dl className="grid grid-cols-[minmax(130px,auto)_1fr] gap-x-4 gap-y-3 text-sm">
          <dt className="text-ink-3">Scheduler</dt>
          <dd className="flex flex-wrap items-center gap-2">
            {sch ? (
              <TipBadge
                tone={sch.status === "running" ? "ok" : "bad"}
                dot
                tip={
                  sch.status === "running"
                    ? "Resumes call retries, reconciles missed call webhooks and syncs projects."
                    : "No tick recently. Retries and syncs are not running."
                }
              >
                {sch.status === "running" ? "Running" : "Stalled"}
              </TipBadge>
            ) : (
              <Badge tone="idle">Unknown</Badge>
            )}
            {sch?.interval_seconds ? <span className="text-ink-3">every {sch.interval_seconds}s</span> : null}
          </dd>
          <dt className="text-ink-3">Last tick</dt>
          <dd>
            <Time iso={sch?.last_tick_at} now={now} />
          </dd>
          <dt className="text-ink-3">Last inbox sync</dt>
          <dd>
            <Time iso={g?.last_sync_at} now={now} />
          </dd>
          <dt className="text-ink-3">Last Gmail push</dt>
          <dd>
            <Time iso={g?.last_push_at} now={now} />
          </dd>
          <dt className="text-ink-3">Gmail watch</dt>
          <dd className="flex flex-wrap items-center gap-2">
            {hoursLeft == null ? (
              <Badge tone="idle">Not set</Badge>
            ) : (
              <TipBadge
                tone={watchTone as "ok" | "warn" | "bad"}
                tip={`Expires ${fmtDateTime(g?.watch_expires_at)}. Gmail watches last 7 days and must be renewed.`}
              >
                {hoursLeft <= 0
                  ? "Expired"
                  : hoursLeft < 48
                    ? `Expires in ${Math.max(1, Math.round(hoursLeft))} h`
                    : `Expires in ${Math.round(hoursLeft / 24)} days`}
              </TipBadge>
            )}
            {watchTone === "warn" || watchTone === "bad" ? (
              <span className="text-[12.5px] text-warn">Renew with POST /admin/gmail/watch</span>
            ) : null}
          </dd>
        </dl>
      </CardBody>
    </Card>
  );
}

function Time({ iso, now }: { iso: string | null | undefined; now: number }) {
  if (!iso) return <span className="text-ink-3">Never</span>;
  return (
    <Tip content={fmtDateTime(iso)}>
      <span tabIndex={0} className="text-ink">
        {ago(iso, now)}
      </span>
    </Tip>
  );
}

function AutomationsCard({ d }: { d: AgentsStatus }) {
  return (
    <Card>
      <CardHeader
        title="Rocketlane automations"
        description="Configured in Rocketlane. Overdue escalation is not done in code."
      />
      {d.rocketlane_automations.length ? (
        <ul className="divide-y divide-line border-t border-line">
          {d.rocketlane_automations.map((a) => (
            <li key={a.name} className="px-5 py-3">
              <div className="flex items-center gap-2 text-sm font-medium text-ink">
                <Bell className="size-4 text-ink-3" aria-hidden />
                {a.name}
              </div>
              <div className="mt-0.5 text-[13px] text-ink-3">
                When {a.trigger || "-"}, notify {a.notify || "-"}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title="No automations listed" />
      )}
    </Card>
  );
}

function IntegrationsCard({ d }: { d: AgentsStatus }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader
        title="Integrations"
        description="Read-only health checks of the services the agents call."
      />
      {d.integrations.length ? (
        <Table>
          <thead>
            <tr>
              <Th>Service</Th>
              <Th>Status</Th>
              <Th className="hidden md:table-cell">Detail</Th>
              <Th className="text-right">Checked</Th>
            </tr>
          </thead>
          <tbody>
            {d.integrations.map((i) => {
              // Until the first health check runs the backend reports ok=false with checked_at=null: not a failure.
              const pending = !i.ok && !i.checked_at;
              const label = i.ok ? "OK" : pending ? "Pending" : "Failing";
              const Icon = i.ok ? CheckCircle2 : pending ? Clock : XCircle;
              return (
                <Tr key={i.name}>
                  <Td className="font-medium text-ink">{INTEGRATION_NAMES[i.name] ?? i.name}</Td>
                  <Td>
                    <Tip content={i.detail || (pending ? "First check pending" : label)}>
                      <span
                        tabIndex={0}
                        className={cn(
                          "inline-flex items-center gap-1.5 text-sm font-medium",
                          i.ok ? "text-ok" : pending ? "text-ink-3" : "text-bad",
                        )}
                      >
                        <Icon className="size-4" aria-hidden />
                        {label}
                      </span>
                    </Tip>
                  </Td>
                  <Td className="hidden text-sm break-words text-ink-2 md:table-cell">{i.detail || "-"}</Td>
                  <Td className="text-right whitespace-nowrap">
                    {i.checked_at ? (
                      <Tip content={fmtDateTime(i.checked_at)}>
                        <span tabIndex={0} className="text-[13px] text-ink-3">
                          {ago(i.checked_at)}
                        </span>
                      </Tip>
                    ) : (
                      "-"
                    )}
                  </Td>
                </Tr>
              );
            })}
          </tbody>
        </Table>
      ) : (
        <EmptyState title="No integrations reported" />
      )}
    </Card>
  );
}
