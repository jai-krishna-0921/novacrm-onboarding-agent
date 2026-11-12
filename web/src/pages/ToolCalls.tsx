import { Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { toolBarTooltip } from "@/lib/chart-tooltip";
import { fmtDateTime, fmtInt, fmtMs, fmtPct, fmtTime } from "@/lib/format";
import { qs, useRuns, useToolCalls, useToolMetrics } from "@/lib/queries";
import type { ToolCallRow } from "@/lib/schemas";
import { TipBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Combobox } from "@/components/ui/combobox";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Mono, Stat } from "@/components/ui/misc";
import { Pagination, usePaging } from "@/components/ui/pagination";
import { Select } from "@/components/ui/select";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { SortTh, sortBy, Table, Td, Th, Tr, type Sort } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { Bars } from "@/components/charts/charts";
import { usePageMeta } from "@/components/layout/page-meta";
import { ToolCallSheet, toolTone } from "@/components/run/ToolCallSheet";

const AGENTS = ["intake", "comms", "tracking", "migration", "root"];
type Key = "tool" | "calls" | "failed" | "rate" | "p95";

export function ToolCallsPage() {
  usePageMeta("Tool calls");
  const m = useToolMetrics();
  const runs = useRuns();
  const [f, setF] = useState({ tool: "", agent: "", status: "", run_id: "" });
  const paging = usePaging("tools");
  const log = useToolCalls(qs({ ...f, limit: paging.size, offset: paging.offset }));
  const [open, setOpen] = useState<ToolCallRow | null>(null);
  const [sort, setSort] = useState<Sort<Key>>({ key: "calls", dir: "desc" });
  const setFilter = (k: keyof typeof f, v: string) => {
    setF((x) => ({ ...x, [k]: v }));
    paging.setPage(0);
  };
  const tools = useMemo(() => m.data?.tools ?? [], [m.data]);
  const sorted = useMemo(
    () =>
      sortBy(tools, sort, (t, k) =>
        k === "tool"
          ? t.tool
          : k === "calls"
            ? t.calls
            : k === "failed"
              ? t.failed
              : k === "rate"
                ? t.failure_rate
                : t.p95_latency_ms,
      ),
    [tools, sort],
  );

  if (m.isError) return <ErrorBox error={m.error} />;
  const calls = tools.reduce((a, t) => a + t.calls, 0);
  const failed = tools.reduce((a, t) => a + t.failed, 0);
  const anyFilter = Object.values(f).some(Boolean);
  const chart = [...tools]
    .sort((a, b) => b.calls - a.calls)
    .slice(0, 12)
    .map((t) => ({ ...t, name: t.tool }));

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[14.5px] text-ink-2">
        Every external action the agents took, through the tool registry. Inputs and outputs load when you
        open a call.
      </p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {!m.data ? (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-card" />)
        ) : (
          <>
            <Stat label="Tool calls" value={fmtInt(calls)} />
            <Stat
              label="Failed"
              value={fmtInt(failed)}
              sub={calls ? `${((100 * failed) / calls).toFixed(1)}% of calls` : undefined}
              tone={failed ? "warn" : undefined}
            />
            <Stat
              label="Replayed"
              value={fmtInt(tools.reduce((a, t) => a + t.replayed, 0))}
              sub="Idempotent repeats, not re-sent"
              tip="The same call with the same idempotency key returned the stored result instead of acting twice."
            />
            <Stat
              label="Blocked"
              value={fmtInt(tools.reduce((a, t) => a + t.blocked, 0))}
              sub="Not on the agent's allowlist"
              tip="An agent asked for a tool it is not allowed to use. The registry refused it."
            />
          </>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1.3fr]">
        <Card>
          <CardHeader title="Calls by tool" description="Hover a bar for failures and latency" />
          <CardBody>
            {m.data ? (
              chart.length ? (
                <Bars
                  data={chart}
                  x="name"
                  y="calls"
                  xLabel={(v) => (v.length > 14 ? `${v.slice(0, 13)}...` : v)}
                  tooltip={toolBarTooltip}
                  label="Tool calls per tool"
                  colorOf={(t) => (t.failed ? "#c07a2c" : "#2f5bd3")}
                  height={240}
                />
              ) : (
                <EmptyState title="No tool calls yet" />
              )
            ) : (
              <Skeleton className="h-[240px]" />
            )}
          </CardBody>
        </Card>
        <Card className="overflow-hidden">
          <CardHeader title="Health by tool" description="Select a tool to filter the log" />
          {!m.data ? (
            <SkeletonRows rows={4} className="px-5 pb-5" />
          ) : sorted.length ? (
            <Table>
              <thead>
                <tr>
                  <SortTh label="Tool" k="tool" sort={sort} onSort={setSort} />
                  <SortTh label="Calls" k="calls" sort={sort} onSort={setSort} className="text-right" />
                  <SortTh
                    label="Failed"
                    k="failed"
                    sort={sort}
                    onSort={setSort}
                    className="hidden text-right sm:table-cell"
                  />
                  <SortTh label="Failure rate" k="rate" sort={sort} onSort={setSort} className="text-right" />
                  <SortTh
                    label="p95"
                    k="p95"
                    sort={sort}
                    onSort={setSort}
                    className="hidden text-right md:table-cell"
                  />
                </tr>
              </thead>
              <tbody>
                {sorted.map((t) => (
                  <Tr key={t.tool} onActivate={() => setFilter("tool", t.tool)} current={f.tool === t.tool}>
                    <Td>
                      <Mono>{t.tool}</Mono>
                    </Td>
                    <Td className="tabular text-right">{fmtInt(t.calls)}</Td>
                    <Td className="tabular hidden text-right sm:table-cell">{fmtInt(t.failed)}</Td>
                    <Td className="text-right">
                      <TipBadge
                        tone={t.failed ? "bad" : "ok"}
                        tip={`${fmtInt(t.failed)} of ${fmtInt(t.calls)} failed. ${fmtInt(t.replayed)} replayed, ${fmtInt(t.blocked)} blocked.`}
                      >
                        {fmtPct(t.failure_rate)}
                      </TipBadge>
                    </Td>
                    <Td className="tabular hidden text-right md:table-cell">{fmtMs(t.p95_latency_ms)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <EmptyState title="No tool calls yet" />
          )}
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader title={`Call log (${fmtInt(log.data?.total ?? 0)})`} />
        <div className="flex flex-wrap items-center gap-2 px-5 pb-3">
          <Combobox
            label="Tool"
            allLabel="All tools"
            value={f.tool}
            onChange={(v) => setFilter("tool", v)}
            options={tools
              .map((t) => t.tool)
              .sort()
              .map((t) => ({ value: t, label: t }))}
            className="w-56"
          />
          <Select
            label="Agent"
            allLabel="All agents"
            value={f.agent}
            onChange={(v) => setFilter("agent", v)}
            options={AGENTS.map((a) => ({ value: a, label: a }))}
            className="w-40"
          />
          <Select
            label="Status"
            allLabel="Any status"
            value={f.status}
            onChange={(v) => setFilter("status", v)}
            options={[
              { value: "ok", label: "ok" },
              { value: "failed", label: "failed" },
            ]}
            className="w-36"
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
          {anyFilter ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setF({ tool: "", agent: "", status: "", run_id: "" });
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
                <Th className="hidden sm:table-cell">Agent</Th>
                <Th>Tool</Th>
                <Th>Status</Th>
                <Th className="hidden text-right sm:table-cell">Try</Th>
                <Th className="text-right">Duration</Th>
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
                  <Td className="hidden sm:table-cell">{r.agent}</Td>
                  <Td>
                    <Mono>{r.tool}</Mono>
                  </Td>
                  <Td>
                    <TipBadge
                      tone={toolTone(r.status)}
                      tip={r.idempotency_key ? `Idempotency key ${r.idempotency_key}` : "No idempotency key"}
                    >
                      {r.status}
                    </TipBadge>
                  </Td>
                  <Td className="tabular hidden text-right sm:table-cell">{r.attempt ?? "-"}</Td>
                  <Td className="tabular text-right">{fmtMs(r.latency_ms)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState icon={Wrench} title="No tool calls match">
            {anyFilter ? "Try clearing the filters." : "Calls appear as runs are processed."}
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
      <ToolCallSheet row={open} onClose={() => setOpen(null)} />
    </div>
  );
}
