import { ArrowRight, Bot } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { latencyBucketTooltip, llmDayTooltip } from "@/lib/chart-tooltip";
import { fmtDateTime, fmtInt, fmtMs, fmtTime, fmtUsd, shortDay } from "@/lib/format";
import { qs, useLlmCalls, useLlmMetrics, useRuns } from "@/lib/queries";
import type { LlmCallRow, LlmGroup } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Combobox } from "@/components/ui/combobox";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { KeyValues, Mono, Stat } from "@/components/ui/misc";
import { Pagination, usePaging } from "@/components/ui/pagination";
import { Select } from "@/components/ui/select";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { SortTh, sortBy, Table, Td, Th, Tr, type Sort } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { Bars } from "@/components/charts/charts";
import { usePageMeta } from "@/components/layout/page-meta";

const DAYS = 14;
type GKey = "calls" | "cost" | "p95";

export function AiUsagePage() {
  usePageMeta("AI usage");
  const m = useLlmMetrics(DAYS);
  const [f, setF] = useState({ model: "", node: "", run_id: "" });
  const paging = usePaging("ai");
  const q = qs({ ...f, limit: paging.size, offset: paging.offset });
  const log = useLlmCalls(q);
  const runs = useRuns();
  const [open, setOpen] = useState<LlmCallRow | null>(null);
  const setFilter = (k: keyof typeof f, v: string) => {
    setF((x) => ({ ...x, [k]: v }));
    paging.setPage(0);
  };

  if (m.isError) return <ErrorBox error={m.error} />;
  const d = m.data;
  const t = d?.totals;
  const models = (d?.by_model ?? []).map((g) => g.model).filter(Boolean) as string[];
  const nodes = [...new Set((d?.by_node ?? []).map((g) => g.node).filter(Boolean))].sort() as string[];
  const series = (d?.series ?? []).map((s) => ({ ...s, calls: s.calls ?? 0 }));
  const total = (d?.latency_buckets ?? []).reduce((a, b) => a + b, 0);
  const latency = (d?.latency_bucket_labels ?? []).map((label, i) => ({
    label,
    calls: d?.latency_buckets[i] ?? 0,
  }));
  const prices = Object.entries(d?.prices_usd_per_mtok ?? {})
    .map(([k, [i, o]]) => `${k} $${i} in, $${o} out`)
    .join("; ");

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[14.5px] text-ink-2">
        Every LLM call across all runs: models, tokens, latency and estimated cost.
      </p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {!t ? (
          Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-card" />)
        ) : (
          <>
            <Stat label="LLM calls" value={fmtInt(t.calls)} />
            <Stat label="Tokens in" value={fmtInt(t.input_tokens)} />
            <Stat label="Tokens out" value={fmtInt(t.output_tokens)} />
            <Stat
              label="Cost (est.)"
              value={fmtUsd(t.cost_usd, 3)}
              sub="List prices, not billing"
              tip={prices ? `Per million tokens: ${prices}` : undefined}
            />
            <Stat label="Latency p50" value={fmtMs(t.p50_latency_ms)} />
            <Stat
              label="Latency p95"
              value={fmtMs(t.p95_latency_ms)}
              sub={t.avg_latency_ms != null ? `Average ${fmtMs(t.avg_latency_ms)}` : undefined}
            />
          </>
        )}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Calls per day"
            description={`Last ${DAYS} days. Hover a bar for cost, tokens and latency.`}
          />
          <CardBody>
            {d ? (
              <Bars
                data={series}
                x="day"
                y="calls"
                xLabel={shortDay}
                tooltip={llmDayTooltip}
                label="LLM calls per day"
              />
            ) : (
              <Skeleton className="h-[220px]" />
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Latency distribution" description="How long each call took" />
          <CardBody>
            {d ? (
              <Bars
                data={latency}
                x="label"
                y="calls"
                xLabel={(v) => v}
                tooltip={(p) => latencyBucketTooltip(p.label, p.calls, total)}
                label="LLM calls by latency"
                color="#5b7fe0"
              />
            ) : (
              <Skeleton className="h-[220px]" />
            )}
          </CardBody>
        </Card>
      </div>
      <GroupTable
        title="By model"
        rows={d?.by_model}
        first={(g) => (
          <Badge tone="accent" mono>
            {(g as { model?: string }).model ?? "-"}
          </Badge>
        )}
        keyOf={(g) => String((g as { model?: string }).model)}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <GroupTable
          title="By step"
          rows={d?.by_node}
          first={(g) => {
            const x = g as { agent?: string | null; node?: string | null };
            return <Mono>{`${x.agent ?? "-"} / ${x.node ?? "-"}`}</Mono>;
          }}
          keyOf={(g) => `${(g as { agent?: string }).agent}/${(g as { node?: string }).node}`}
          compact
        />
        <GroupTable
          title="By prompt version"
          rows={d?.by_prompt}
          first={(g) => <Mono>{(g as { prompt_version?: string | null }).prompt_version ?? "-"}</Mono>}
          keyOf={(g) => String((g as { prompt_version?: string }).prompt_version)}
          compact
        />
      </div>

      <Card className="overflow-hidden">
        <CardHeader
          title={`Call log (${fmtInt(log.data?.total ?? 0)})`}
          description="Select a row for details. Prompts and responses are not stored."
        />
        <div className="flex flex-wrap items-center gap-2 px-5 pb-3">
          <Select
            label="Model"
            allLabel="All models"
            value={f.model}
            onChange={(v) => setFilter("model", v)}
            options={models.map((x) => ({ value: x, label: x }))}
            className="w-48"
          />
          <Select
            label="Step"
            allLabel="All steps"
            value={f.node}
            onChange={(v) => setFilter("node", v)}
            options={nodes.map((x) => ({ value: x, label: x }))}
            className="w-48"
          />
          <Combobox
            label="Run"
            allLabel="All runs"
            value={f.run_id}
            onChange={(v) => setFilter("run_id", v)}
            options={(runs.data ?? []).map((r) => ({
              value: r.id,
              label: r.customer_name || r.id,
              hint: r.id,
            }))}
            className="w-56"
          />
          {f.model || f.node || f.run_id ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setF({ model: "", node: "", run_id: "" });
                paging.setPage(0);
              }}
            >
              Clear filters
            </Button>
          ) : null}
        </div>
        {log.isPending ? (
          <SkeletonRows rows={5} className="px-5 pb-5" />
        ) : log.isError ? (
          <div className="px-5 pb-5">
            <ErrorBox error={log.error} />
          </div>
        ) : log.data.rows.length ? (
          <Table>
            <thead>
              <tr>
                <Th>Time</Th>
                <Th className="hidden md:table-cell">Customer</Th>
                <Th>Step</Th>
                <Th className="hidden lg:table-cell">Model</Th>
                <Th className="hidden text-right md:table-cell">Tokens in / out</Th>
                <Th className="text-right">Latency</Th>
                <Th className="hidden text-right sm:table-cell">Est. cost</Th>
              </tr>
            </thead>
            <tbody>
              {log.data.rows.map((r) => (
                <Tr key={r.id ?? r.ts} onActivate={() => setOpen(r)} current={open?.id === r.id}>
                  <Td className="whitespace-nowrap">
                    <Tip content={fmtDateTime(r.ts)}>
                      <span>{fmtTime(r.ts, true)}</span>
                    </Tip>
                  </Td>
                  <Td className="hidden max-w-[200px] truncate md:table-cell">{r.customer_name || "-"}</Td>
                  <Td>
                    <Mono>{r.node}</Mono>
                  </Td>
                  <Td className="hidden lg:table-cell">
                    <Mono className="text-ink-2">{r.model}</Mono>
                  </Td>
                  <Td className="tabular hidden text-right whitespace-nowrap md:table-cell">
                    {fmtInt(r.input_tokens)} / {fmtInt(r.output_tokens)}
                  </Td>
                  <Td className="tabular text-right whitespace-nowrap">{fmtMs(r.latency_ms)}</Td>
                  <Td className="tabular hidden text-right sm:table-cell">{fmtUsd(r.cost_usd, 5)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState icon={Bot} title="No LLM calls match">
            {f.model || f.node || f.run_id
              ? "Try clearing the filters."
              : "Calls appear as runs are processed."}
          </EmptyState>
        )}
        <Pagination
          total={log.data?.total ?? 0}
          page={paging.page}
          size={paging.size}
          onPage={paging.setPage}
          onSize={paging.setSize}
          noun="calls"
        />
      </Card>

      <Sheet
        open={!!open}
        onOpenChange={(o) => !o && setOpen(null)}
        title={open ? `${open.agent ?? "-"} / ${open.node ?? "-"}` : ""}
        eyebrow={
          open ? (
            <Badge tone="accent" mono>
              {open.model}
            </Badge>
          ) : null
        }
      >
        {open ? (
          <div className="flex flex-col gap-5">
            <KeyValues
              items={[
                ["Time", fmtDateTime(open.ts)],
                ["Customer", open.customer_name],
                ["Run", open.run_id ? <Mono key="r">{open.run_id}</Mono> : null],
                ["Agent", open.agent],
                ["Step", <Mono key="s">{open.node}</Mono>],
                ["Model", <Mono key="m">{open.model}</Mono>],
                ["Prompt version", <Mono key="p">{open.prompt_version}</Mono>],
                ["Tokens in", fmtInt(open.input_tokens)],
                ["Tokens out", fmtInt(open.output_tokens)],
                ["Latency", fmtMs(open.latency_ms)],
                ["Cost (est.)", fmtUsd(open.cost_usd, 6)],
                ["Stop reason", open.stop_reason],
              ]}
            />
            <p className="text-[12.5px] text-ink-3">
              Prompts and responses are not stored. This is the call's metadata.
            </p>
            {open.run_id ? (
              <Link
                to={`/runs/${encodeURIComponent(open.run_id)}`}
                state={{ from: "/ai" }}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline"
              >
                Open run <ArrowRight className="size-4" aria-hidden />
              </Link>
            ) : null}
          </div>
        ) : null}
      </Sheet>
    </div>
  );
}

function GroupTable({
  title,
  rows,
  first,
  keyOf,
  compact,
}: {
  title: string;
  rows: LlmGroup[] | undefined;
  first: (g: LlmGroup) => ReactNode;
  keyOf: (g: LlmGroup) => string;
  compact?: boolean;
}) {
  const [sort, setSort] = useState<Sort<GKey>>({ key: "calls", dir: "desc" });
  const sorted = useMemo(
    () =>
      sortBy(rows ?? [], sort, (g, k) =>
        k === "calls" ? g.calls : k === "cost" ? g.cost_usd : g.p95_latency_ms,
      ),
    [rows, sort],
  );
  return (
    <Card className="overflow-hidden">
      <CardHeader title={title} />
      {!rows ? (
        <SkeletonRows rows={3} className="px-5 pb-5" />
      ) : rows.length ? (
        <Table>
          <thead>
            <tr>
              <Th>{title.replace("By ", "").replace(/^./, (c) => c.toUpperCase())}</Th>
              <SortTh label="Calls" k="calls" sort={sort} onSort={setSort} className="text-right" />
              {!compact ? <Th className="hidden text-right md:table-cell">Tokens in / out</Th> : null}
              <SortTh label="Est. cost" k="cost" sort={sort} onSort={setSort} className="text-right" />
              {!compact ? <Th className="hidden text-right md:table-cell">Avg</Th> : null}
              <SortTh label="p95" k="p95" sort={sort} onSort={setSort} className="text-right" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((g) => (
              <Tr key={keyOf(g)}>
                <Td className="max-w-[260px]">{first(g)}</Td>
                <Td className="tabular text-right">{fmtInt(g.calls)}</Td>
                {!compact ? (
                  <Td className="tabular hidden text-right md:table-cell">
                    {fmtInt(g.input_tokens)} / {fmtInt(g.output_tokens)}
                  </Td>
                ) : null}
                <Td className="tabular text-right">{fmtUsd(g.cost_usd)}</Td>
                {!compact ? (
                  <Td className="tabular hidden text-right md:table-cell">{fmtMs(g.avg_latency_ms)}</Td>
                ) : null}
                <Td className="tabular text-right">{fmtMs(g.p95_latency_ms)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <EmptyState title="No LLM calls yet" />
      )}
    </Card>
  );
}
