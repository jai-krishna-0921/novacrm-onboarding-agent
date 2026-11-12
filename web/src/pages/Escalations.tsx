import { AlertTriangle, ArrowRight, CheckCircle2 } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router";
import { cn } from "@/lib/cn";
import { ago, fmtDateTime, plainError } from "@/lib/format";
import { useEscalations, useRun, useRuns } from "@/lib/queries";
import { deriveRun, runName } from "@/lib/run-state";
import type { Escalation, Run } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { clampPage, Pagination, usePaging } from "@/components/ui/pagination";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { Tip } from "@/components/ui/tooltip";
import { usePageMeta } from "@/components/layout/page-meta";
import { escalationsFor, KindChip } from "@/components/run/chips";
import { DecisionPanels } from "@/components/run/DecisionPanel";
import { Stepper } from "@/components/run/Stepper";

export function EscalationsPage() {
  usePageMeta("Needs a human");
  const { runId } = useParams();
  const navigate = useNavigate();
  const escs = useEscalations();
  const runs = useRuns();
  const paging = usePaging("esc");
  const byId = new Map((runs.data ?? []).map((r) => [r.id, r]));
  const sorted = [...(escs.data ?? [])].sort((a, b) =>
    String(b.created_at).localeCompare(String(a.created_at)),
  );
  const page = clampPage(paging.page, sorted.length, paging.size);
  const shown = sorted.slice(page * paging.size, (page + 1) * paging.size);

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[14.5px] text-ink-2">
        Runs paused until a person decides. Nothing is guessed: no confirmed tier means no project.
      </p>
      <Card className="overflow-hidden">
        {escs.isPending ? (
          <SkeletonRows rows={4} className="p-5" />
        ) : escs.isError ? (
          <div className="p-5">
            <ErrorBox error={escs.error} />
          </div>
        ) : !shown.length ? (
          <EmptyState icon={CheckCircle2} title="All clear">
            Nothing needs a human right now.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {shown.map((e) => (
              <EscalationRow key={e.key} e={e} run={byId.get(e.run_id)} current={runId === e.run_id} />
            ))}
          </ul>
        )}
        <Pagination
          total={sorted.length}
          page={page}
          size={paging.size}
          onPage={paging.setPage}
          onSize={paging.setSize}
          noun="escalations"
        />
      </Card>
      <EscalationSheet
        runId={runId ?? null}
        escs={escs.data ?? []}
        onClose={() => navigate("/escalations")}
      />
    </div>
  );
}

function EscalationRow({ e, run, current }: { e: Escalation; run?: Run; current: boolean }) {
  // A run escalated before extraction finished has no customer_name; its audit has the subject line.
  const detail = useRun(run && !run.customer_name ? e.run_id : undefined);
  const name = run ? runName(run, detail.data?.audit) : "Run";
  return (
    <li>
      <Link
        to={`/escalations/${encodeURIComponent(e.run_id)}`}
        aria-current={current || undefined}
        className={cn(
          "group grid grid-cols-[auto_1fr_auto] items-start gap-3 px-5 py-4 transition-colors hover:bg-sunken focus-visible:bg-accent-soft focus-visible:outline-none",
          current && "bg-accent-soft",
        )}
      >
        <span className="mt-0.5 grid size-8 place-items-center rounded-full border border-warn-line bg-warn-soft text-warn">
          <AlertTriangle className="size-4" aria-hidden />
        </span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-ink">{name}</span>
            <KindChip kind={e.kind} />
            <Tip content={fmtDateTime(e.created_at)}>
              <span className="text-[12.5px] text-ink-3">Raised {ago(e.created_at)}</span>
            </Tip>
          </span>
          <span className="mt-1 line-clamp-2 block text-sm break-words text-ink-2">
            {plainError(e.reason)}
          </span>
        </span>
        <span className="hidden items-center gap-1 self-center text-sm font-medium text-accent sm:inline-flex">
          Decide
          <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
        </span>
      </Link>
    </li>
  );
}

function EscalationSheet({
  runId,
  escs,
  onClose,
}: {
  runId: string | null;
  escs: Escalation[];
  onClose: () => void;
}) {
  const q = useRun(runId ?? undefined);
  const data = q.data;
  const view = data ? deriveRun(data.run, data, escs) : null;
  const runEscs = runId ? escalationsFor(escs, runId) : [];
  return (
    <Sheet
      open={!!runId}
      onOpenChange={(o) => !o && onClose()}
      title={view?.name ?? "Escalation"}
      eyebrow={
        runEscs.length ? <KindChip kind={runEscs[0].kind} /> : data ? <Badge tone="ok">Resolved</Badge> : null
      }
      width="max-w-[640px]"
      footer={
        runId ? (
          <Link
            to={`/runs/${encodeURIComponent(runId)}`}
            state={{ from: "/escalations" }}
            className="inline-flex h-9 items-center gap-1.5 rounded-ctl border border-line-strong bg-surface px-3.5 text-sm font-medium text-ink-2 shadow-card hover:bg-sunken"
          >
            Open full run
            <ArrowRight className="size-4" aria-hidden />
          </Link>
        ) : null
      }
    >
      {q.isPending ? (
        <div className="space-y-3" role="status" aria-label="Loading">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : q.isError ? (
        <ErrorBox error={q.error} />
      ) : data && view ? (
        <div className="flex flex-col gap-5">
          {runEscs.length ? (
            <DecisionPanels run={data.run} pending={data.pending} escalations={runEscs} compact />
          ) : (
            <div className="rounded-ctl border border-ok-line bg-ok-soft px-4 py-3 text-sm text-ok">
              This escalation is closed. Nothing left to decide here.
            </div>
          )}
          <div>
            <h3 className="mb-3 text-[13px] font-medium text-ink-3">Where this run is</h3>
            <Stepper steps={view.steps} vertical />
          </div>
        </div>
      ) : null}
    </Sheet>
  );
}
