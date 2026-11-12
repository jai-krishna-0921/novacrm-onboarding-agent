import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Database, FlaskConical, Play, RotateCcw, Wrench } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { cn } from "@/lib/cn";
import { fmtDateTime, fmtInt, fmtTime, human, str } from "@/lib/format";
import { CHECKS, problemExplain, problemLabel, rowTone, problemCount } from "@/lib/migration";
import { post, qs, useMigrationData, useMigrationSummary, useRun, useRuns } from "@/lib/queries";
import { getPref, setPref } from "@/lib/prefs";
import type { MigrationDataRow, MigrationSummary } from "@/lib/schemas";
import { Badge, TipBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Combobox } from "@/components/ui/combobox";
import { ConfirmDialog } from "@/components/ui/dialog";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Stat } from "@/components/ui/misc";
import { Pagination, usePaging } from "@/components/ui/pagination";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { Tip } from "@/components/ui/tooltip";
import { usePageMeta } from "@/components/layout/page-meta";
import { useRunMigrationCheck } from "@/components/run/MigrationTab";
import { CheckHistory, CheckTiles, splitBatches } from "@/components/run/MigrationTiles";

export function MigrationPage() {
  usePageMeta("Data migration");
  const s = useMigrationSummary();
  const [onlyProblems, setOnly] = useState(() => getPref("dash.mig.only", "0") === "1");
  const toggle = (v: boolean) => {
    setOnly(v);
    setPref("dash.mig.only", v ? "1" : "0");
  };
  const [side, setSide] = useState<Side>(() =>
    getPref("dash.mig.side", "source") === "dest" ? "dest" : "source",
  );
  const pickSide = (v: Side) => {
    setSide(v);
    setPref("dash.mig.side", v);
  };
  const sum = s.data;
  const issues = sum ? problemCount(sum) : 0;

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[14.5px] text-ink-2">
        Contacts copied from the customer's old CRM (source) into NovaCRM (destination). The check compares
        the two before a person ticks Migration verified in Rocketlane.
      </p>
      {s.isError ? <ErrorBox error={s.error} /> : null}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {!sum ? (
          Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-card" />)
        ) : (
          <>
            <Stat label="Source rows" value={fmtInt(sum.source_count)} />
            <Stat
              label="Destination rows"
              value={fmtInt(sum.dest_count)}
              tone={sum.dest_count !== sum.source_count ? "warn" : "ok"}
              sub={
                sum.dest_count === sum.source_count
                  ? "Counts match"
                  : `${fmtInt(Math.abs(sum.dest_count - sum.source_count))} ${sum.dest_count > sum.source_count ? "extra" : "short"}`
              }
            />
            <Stat
              label="Missing columns"
              value={fmtInt(sum.columns.missing.length)}
              tone={sum.columns.missing.length ? "bad" : undefined}
              sub={sum.columns.missing.length ? sum.columns.missing.join(", ") : undefined}
              tip={CHECKS.schema.explain}
            />
            <Stat
              label="Duplicate ids"
              value={fmtInt(sum.problems.duplicates.length)}
              tone={sum.problems.duplicates.length ? "bad" : undefined}
              tip={problemExplain("duplicates")}
            />
            <Stat
              label="Missing name or email"
              value={fmtInt(sum.problems.missing_required.length)}
              tone={sum.problems.missing_required.length ? "bad" : undefined}
              tip={problemExplain("missing_required")}
            />
            <Stat
              label="Not in destination"
              value={fmtInt(sum.problems.missing_in_dest.length)}
              tone={sum.problems.missing_in_dest.length ? "bad" : undefined}
              tip={problemExplain("missing_in_dest")}
            />
          </>
        )}
      </div>
      {sum ? (
        <p className="text-[13px] text-ink-3">
          {sum.last_check?.ts
            ? `Last check ${fmtDateTime(sum.last_check.ts)}${sum.last_check.passed == null ? "" : sum.last_check.passed ? ", passed" : ", failed"}.`
            : "No check has run yet."}{" "}
          {issues
            ? `${fmtInt(issues)} problems in the data right now.`
            : "No problems in the data right now."}
        </p>
      ) : null}

      {sum && (sum.columns.source.length || sum.columns.dest.length) ? (
        <ColumnsCard columns={sum.columns} />
      ) : null}

      <Card className="overflow-hidden">
        <Tabs value={side} onValueChange={(v) => pickSide(v === "dest" ? "dest" : "source")}>
          <div className="flex flex-wrap items-center justify-between gap-x-4 border-b border-line pr-5">
            <TabsList className="border-b-0">
              <TabsTrigger value="source" badge={<SideBadge n={sum?.problems.missing_in_dest.length} />}>
                <span>
                  Source<span className="hidden sm:inline"> (old CRM)</span>
                </span>
              </TabsTrigger>
              <TabsTrigger
                value="dest"
                badge={
                  <SideBadge
                    n={
                      sum ? sum.problems.duplicates.length + sum.problems.missing_required.length : undefined
                    }
                  />
                }
              >
                <span>
                  Destination<span className="hidden sm:inline"> (NovaCRM)</span>
                </span>
              </TabsTrigger>
            </TabsList>
            <label className="flex items-center gap-2 py-2 pl-5 text-sm text-ink-2">
              <Switch checked={onlyProblems} onCheckedChange={toggle} aria-label="Only rows with problems" />
              Only rows with problems
            </label>
          </div>
          <TabsContent value="source" className="p-0">
            <DataTable side="source" columns={sum?.columns.source} onlyProblems={onlyProblems} />
          </TabsContent>
          <TabsContent value="dest" className="p-0">
            <DataTable side="dest" columns={sum?.columns.dest} onlyProblems={onlyProblems} />
          </TabsContent>
        </Tabs>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <RunCheckCard />
        <DemoControls />
      </div>
    </div>
  );
}

type Side = "source" | "dest";

function SideBadge({ n }: { n: number | undefined }) {
  if (!n) return null;
  return (
    <Badge tone="bad" className="px-1.5 py-0 text-[11.5px]">
      {fmtInt(n)}
    </Badge>
  );
}

function ColumnsCard({ columns }: { columns: MigrationSummary["columns"] }) {
  const { source, dest, missing, extra } = columns;
  const description = missing.length
    ? `${fmtInt(missing.length)} source ${missing.length === 1 ? "column is" : "columns are"} missing in the destination.`
    : `All ${fmtInt(source.length)} source columns are in the destination.`;
  return (
    <Card>
      <CardHeader
        title="Columns"
        description={
          extra.length ? `${description} ${fmtInt(extra.length)} extra, which is allowed.` : description
        }
        actions={
          <div className="flex items-center gap-3 text-[12.5px] text-ink-3">
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-bad" aria-hidden />
              Missing
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-accent" aria-hidden />
              Extra
            </span>
          </div>
        }
      />
      <CardBody className="pt-0">
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[110px_1fr] sm:items-center">
          <dt className="text-[13px] text-ink-3">Source</dt>
          <dd className="flex flex-wrap gap-1.5">
            {source.map((c) => (
              <ColumnChip
                key={c}
                name={c}
                kind={missing.includes(c) ? "missing" : "match"}
                tip={
                  missing.includes(c)
                    ? `The destination has no ${c} column, so these values were not migrated.`
                    : "In both tables."
                }
              />
            ))}
          </dd>
          <dt className="mt-1 text-[13px] text-ink-3 sm:mt-0">Destination</dt>
          <dd className="flex flex-wrap gap-1.5">
            {dest.map((c) => (
              <ColumnChip
                key={c}
                name={c}
                kind={extra.includes(c) ? "extra" : "match"}
                tip={
                  extra.includes(c)
                    ? "Only in the destination. Extra columns are allowed."
                    : "In both tables."
                }
              />
            ))}
            {missing.map((c) => (
              <ColumnChip
                key={`missing-${c}`}
                name={c}
                kind="absent"
                tip={`Missing: the source has a ${c} column, the destination does not.`}
              />
            ))}
          </dd>
        </dl>
      </CardBody>
    </Card>
  );
}

const CHIP = {
  match: "border-line-strong bg-surface text-ink-2",
  missing: "border-bad-line bg-bad-soft text-bad",
  absent: "border-dashed border-bad-line bg-surface text-bad line-through decoration-bad/60",
  extra: "border-accent-line bg-accent-soft text-accent-strong",
} as const;

function ColumnChip({ name, kind, tip }: { name: string; kind: keyof typeof CHIP; tip: string }) {
  return (
    <Tip content={tip}>
      <span
        tabIndex={0}
        data-kind={kind}
        className={cn("rounded-[6px] border px-2 py-0.5 font-mono text-[12.5px] leading-5", CHIP[kind])}
      >
        {name}
      </span>
    </Tip>
  );
}

// Which columns stay visible as the screen narrows: id, name and problems always; email from sm; the rest later.
const ALWAYS = new Set(["id", "name"]);
const COL_HIDE: Record<string, string> = {
  email: "hidden sm:table-cell",
  phone: "hidden lg:table-cell",
  company: "hidden lg:table-cell",
};
const colHide = (c: string) => (ALWAYS.has(c) ? "" : (COL_HIDE[c] ?? "hidden xl:table-cell"));

/** Columns to show: the table's own (from the summary), else whatever the rows carry. */
function tableColumns(columns: string[] | undefined, rows: MigrationDataRow[]): string[] {
  if (columns?.length) return columns;
  const seen = new Set<string>();
  for (const r of rows) for (const k of Object.keys(r)) if (k !== "problems") seen.add(k);
  return [...seen];
}

function DataTable({
  side,
  columns,
  onlyProblems,
}: {
  side: Side;
  columns: string[] | undefined;
  onlyProblems: boolean;
}) {
  const paging = usePaging(`mig-${side}`);
  const q = useMigrationData(
    qs({ side, limit: paging.size, offset: paging.offset, only_problems: onlyProblems }),
  );
  const [prevOnly, setPrevOnly] = useState(onlyProblems);
  if (prevOnly !== onlyProblems) {
    setPrevOnly(onlyProblems);
    paging.setPage(0);
  }
  const cols = tableColumns(columns, q.data?.rows ?? []);
  return (
    <>
      <p className="px-5 pt-3 pb-2 text-[13px] text-ink-3">
        {q.data ? `${fmtInt(q.data.total)} ${onlyProblems ? "rows with problems" : "rows"}` : "\u00a0"}
      </p>
      {q.isPending ? (
        <SkeletonRows rows={5} className="px-5 pb-5" />
      ) : q.isError ? (
        <div className="px-5 pb-5">
          <ErrorBox error={q.error} />
        </div>
      ) : q.data.rows.length ? (
        <Table>
          <thead>
            <tr>
              {cols.map((c) => (
                <Th key={c} className={cn(colHide(c), c === "id" && "w-16")}>
                  {c === "id" ? "Id" : human(c)}
                </Th>
              ))}
              <Th>Problems</Th>
            </tr>
          </thead>
          <tbody>
            {q.data.rows.map((r, i) => (
              <DataRow key={`${r.id}-${i}`} r={r} cols={cols} />
            ))}
          </tbody>
        </Table>
      ) : (
        <EmptyState icon={Database} title={onlyProblems ? "No problem rows" : "No rows"}>
          {onlyProblems ? "Every row on this side passes the checks." : undefined}
        </EmptyState>
      )}
      <Pagination
        total={q.data?.total ?? 0}
        page={paging.page}
        size={paging.size}
        onPage={paging.setPage}
        onSize={paging.setSize}
      />
    </>
  );
}

const ROW_TONE = { ok: "", warn: "bg-warn-soft/60", bad: "bg-bad-soft/70" } as const;
const EDGE = {
  ok: "",
  warn: "shadow-[inset_3px_0_0_var(--color-warn)]",
  bad: "shadow-[inset_3px_0_0_var(--color-bad)]",
} as const;

function Cell({ col, value }: { col: string; value: unknown }) {
  if (col === "id")
    return (
      <Td className="font-mono text-[13px] text-ink-2">
        {value == null ? <span className="font-sans text-bad italic">Missing</span> : str(value)}
      </Td>
    );
  const text = str(value).trim();
  return (
    <Td
      className={cn(
        colHide(col),
        "whitespace-nowrap",
        col === "email" ? "max-w-[240px] truncate" : col !== "name" && "max-w-[180px] truncate",
        !text && "text-ink-3 italic",
      )}
    >
      {text || "(blank)"}
    </Td>
  );
}

function DataRow({ r, cols }: { r: MigrationDataRow; cols: string[] }) {
  const tone = rowTone(r);
  return (
    <Tr className={cn(ROW_TONE[tone], EDGE[tone])} data-tone={tone}>
      {cols.map((c) => (
        <Cell key={c} col={c} value={r[c]} />
      ))}
      <Td>
        {r.problems.length ? (
          <div className="flex flex-wrap gap-1">
            {r.problems.map((p) => (
              <TipBadge key={p} tone={p === "missing_in_dest" ? "bad" : "warn"} tip={problemExplain(p)}>
                {problemLabel(p)}
              </TipBadge>
            ))}
          </div>
        ) : (
          <span className="text-[13px] text-ink-3">None</span>
        )}
      </Td>
    </Tr>
  );
}

function RunCheckCard() {
  const runs = useRuns();
  // Enterprise projects have the migration phase, so list them first.
  const withProject = (runs.data ?? [])
    .filter((r) => r.rocketlane_project_id)
    .sort((a, b) => Number(b.plan_tier === "enterprise") - Number(a.plan_tier === "enterprise"));
  const [runId, setRunId] = useState("");
  const selected = runId || withProject[0]?.id || "";
  const detail = useRun(selected || undefined);
  const { run, busy } = useRunMigrationCheck(selected || null);
  const checks = detail.data?.migration_checks ?? [];
  const { latest, older } = splitBatches(checks);
  const failed = latest.filter((c) => !c.passed).length;
  return (
    <Card>
      <CardHeader
        title="Check a run"
        description="Runs the five checks against the data above and records the result on that run's project."
      />
      <CardBody className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Combobox
            label="Run"
            placeholder={withProject.length ? "Pick a run" : "No runs with a project yet"}
            value={selected}
            onChange={setRunId}
            options={withProject.map((r) => ({
              value: r.id,
              label: r.customer_name || r.id,
              hint: `Project #${r.rocketlane_project_id}`,
            }))}
            className="w-full sm:w-72"
          />
          <Button variant="primary" onClick={run} loading={busy} disabled={!selected}>
            <Play className="size-4" aria-hidden />
            Run check for this run
          </Button>
          {selected ? (
            <Link
              to={`/runs/${encodeURIComponent(selected)}`}
              state={{ from: "/migration" }}
              className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline"
            >
              Open run
              <ArrowRight className="size-4" aria-hidden />
            </Link>
          ) : null}
        </div>
        {!selected ? null : detail.isPending ? (
          <Skeleton className="h-28" />
        ) : latest.length ? (
          <>
            <div className="flex items-center gap-2 text-sm">
              <Badge tone={failed ? "bad" : "ok"}>
                {failed ? `${failed} of ${latest.length} failed` : "All checks passed"}
              </Badge>
              <Tip content={fmtDateTime(latest[0].ts)}>
                <span tabIndex={0} className="text-ink-3">
                  {fmtTime(latest[0].ts, true)}
                </span>
              </Tip>
            </div>
            <CheckTiles checks={latest} />
            {older.length ? (
              <details className="group">
                <summary className="cursor-pointer text-sm font-medium text-ink-2 hover:text-ink">
                  Earlier checks ({new Set(older.map((c) => c.batch_id)).size})
                </summary>
                <div className="-mx-5 mt-2">
                  <CheckHistory checks={older} />
                </div>
              </details>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-ink-3">No check has run for this run yet.</p>
        )}
      </CardBody>
    </Card>
  );
}

function DemoControls() {
  const toast = useToast();
  const qc = useQueryClient();
  const act = (path: string, ok: string) => async () => {
    try {
      await post(path, {});
      toast({ title: ok });
      await qc.invalidateQueries({ queryKey: ["migration"] });
    } catch (e) {
      toast({
        title: "Could not change the demo data",
        body: e instanceof Error ? e.message : String(e),
        tone: "bad",
      });
      throw e;
    }
  };
  return (
    <Card className="border-dashed border-line-strong bg-sunken/50">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <FlaskConical className="size-4 text-ink-3" aria-hidden />
            Demo data controls
          </span>
        }
        description="For demos only. These change the sample databases, not a customer's data."
      />
      <CardBody className="flex flex-col gap-3">
        <ConfirmDialog
          title="Fix the destination data?"
          description="Adds missing columns, removes duplicate ids, fills blank names and emails, and copies missing source rows. The next check should pass."
          confirmLabel="Fix destination data"
          onConfirm={act("/migration/demo/fix", "Destination data fixed")}
          trigger={(open) => (
            <Button onClick={open} className="justify-start">
              <Wrench className="size-4" aria-hidden />
              Fix destination data
            </Button>
          )}
        />
        <ConfirmDialog
          title="Reset the demo data?"
          description="Puts the original problems back: a missing column, duplicates, blank fields and missing rows. The next check will fail."
          confirmLabel="Reset demo data"
          tone="danger"
          onConfirm={act("/migration/demo/reset", "Demo data reset")}
          trigger={(open) => (
            <Button variant="danger-ghost" onClick={open} className="justify-start">
              <RotateCcw className="size-4" aria-hidden />
              Reset demo data
            </Button>
          )}
        />
      </CardBody>
    </Card>
  );
}
