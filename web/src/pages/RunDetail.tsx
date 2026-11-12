import { FileQuestion } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { fmtDateTime } from "@/lib/format";
import { getPref, setPref } from "@/lib/prefs";
import { useEscalations, useRun } from "@/lib/queries";
import { deriveRun } from "@/lib/run-state";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Mono } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/ui/tooltip";
import { usePageMeta, type Crumb } from "@/components/layout/page-meta";
import { ActivityTab } from "@/components/run/ActivityTab";
import { AiToolsTab } from "@/components/run/AiToolsTab";
import { escalationsFor, ProjectLink, StatusChip, TierChip } from "@/components/run/chips";
import { DecisionPanels } from "@/components/run/DecisionPanel";
import { MigrationTab } from "@/components/run/MigrationTab";
import { NowBanner } from "@/components/run/NowBanner";
import { ProgressTab } from "@/components/run/ProgressTab";
import { Stepper } from "@/components/run/Stepper";

const TABS = ["activity", "progress", "migration", "tech"] as const;
const FROM: Record<string, Crumb> = {
  "/escalations": { label: "Needs a human", to: "/escalations" },
  "/overview": { label: "Overview", to: "/overview" },
  "/ai": { label: "AI usage", to: "/ai" },
  "/tools": { label: "Tool calls", to: "/tools" },
  "/migration": { label: "Data migration", to: "/migration" },
};

export function RunDetailPage() {
  const { id = "" } = useParams();
  const loc = useLocation();
  const from = (loc.state as { from?: string } | null)?.from;
  const crumb = (from && FROM[from]) || { label: "Runs", to: "/runs" };
  const q = useRun(id);
  const escs = useEscalations();
  const [tab, setTabState] = useState(() => {
    const t = getPref("dash.tab", "activity");
    return (TABS as readonly string[]).includes(t) ? t : "activity";
  });
  const setTab = (t: string) => {
    setTabState(t);
    setPref("dash.tab", t);
  };
  const view = q.data ? deriveRun(q.data.run, q.data, escs.data ?? []) : null;
  usePageMeta(view?.name ?? "Run details", [crumb]);

  if (q.isPending)
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Loading run">
        <Skeleton className="h-36 w-full rounded-card" />
        <Skeleton className="h-24 w-full rounded-card" />
        <Skeleton className="h-80 w-full rounded-card" />
      </div>
    );
  if (q.isError) {
    if ((q.error as { status?: number }).status === 404)
      return (
        <Card>
          <EmptyState
            icon={FileQuestion}
            title="Run not found"
            action={
              <Link
                to="/runs"
                className="inline-flex h-9 items-center rounded-ctl border border-line-strong bg-surface px-3.5 text-sm font-medium text-ink-2 shadow-card hover:bg-sunken"
              >
                Go to all runs
              </Link>
            }
          >
            It may have been removed, or the link is wrong.
          </EmptyState>
        </Card>
      );
    return <ErrorBox error={q.error} />;
  }
  const data = q.data;
  const run = data.run;
  const v = view!;
  const runEscs = escalationsFor(escs.data, run.id);
  const checks = data.migration_checks;
  const lastBatch = checks.length ? checks[checks.length - 1].batch_id : null;
  const migFail = checks.some((c) => c.batch_id === lastBatch && !c.passed);

  return (
    <div className="flex flex-col gap-4">
      <Card className="p-5">
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] lg:items-start">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip run={run} view={v} />
              <Tip content={fmtDateTime(run.created_at)}>
                <span tabIndex={0} className="text-[13px] text-ink-3">
                  Received {fmtDateTime(run.created_at)}
                </span>
              </Tip>
            </div>
            <h2 className="mt-2 truncate text-[22px] font-semibold tracking-[-0.02em] text-ink">{v.name}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-ink-2">
              <TierChip tier={v.tier} />
              <ProjectLink id={run.rocketlane_project_id} />
              {run.slack_channel_id ? (
                <span>
                  Slack <Mono>{run.slack_channel_id}</Mono>
                </span>
              ) : null}
              {run.opportunity_id ? (
                <span>
                  Opportunity <Mono>{run.opportunity_id}</Mono>
                </span>
              ) : null}
            </div>
          </div>
          <NowBanner view={v} />
        </div>
        <div className="mt-6 border-t border-line pt-5">
          <Stepper steps={v.steps} />
        </div>
      </Card>

      <DecisionPanels run={run} pending={data.pending} escalations={runEscs} />

      <Card className="overflow-hidden">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="activity">Activity</TabsTrigger>
            <TabsTrigger value="progress" badge={v.tracking ? <Badge tone="accent">{v.pct}%</Badge> : null}>
              Project progress
            </TabsTrigger>
            <TabsTrigger
              value="migration"
              badge={migFail || v.migrationEsc ? <Badge tone="bad">Fail</Badge> : null}
            >
              Migration check
            </TabsTrigger>
            <TabsTrigger value="tech">AI and tools</TabsTrigger>
          </TabsList>
          <TabsContent value="activity">
            <ActivityTab audit={data.audit} />
          </TabsContent>
          <TabsContent value="progress">
            <ProgressTab run={run} tracking={data.tracking} />
          </TabsContent>
          <TabsContent value="migration">
            <MigrationTab run={run} checks={checks} />
          </TabsContent>
          <TabsContent value="tech">
            <AiToolsTab data={data} />
          </TabsContent>
        </Tabs>
      </Card>
    </div>
  );
}
