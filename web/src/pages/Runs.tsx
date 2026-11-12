import { Inbox, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { cn } from "@/lib/cn";
import { ago, fmtDateTime } from "@/lib/format";
import { getPref, setPref } from "@/lib/prefs";
import { useEscalations, useRunDetails, useRuns } from "@/lib/queries";
import { deriveRun, type Group } from "@/lib/run-state";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { clampPage, Pagination, usePaging } from "@/components/ui/pagination";
import { SkeletonRows } from "@/components/ui/skeleton";
import { SortTh, sortBy, Table, Td, Th, Tr, type Sort } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { usePageMeta } from "@/components/layout/page-meta";
import { StatusChip, TierChip } from "@/components/run/chips";
import { MiniPipeline } from "@/components/run/Stepper";

const FILTERS: { value: "all" | Group; label: string }[] = [
  { value: "all", label: "All" },
  { value: "attn", label: "Needs attention" },
  { value: "active", label: "In progress" },
  { value: "done", label: "Done" },
];

type Key = "customer" | "updated";

export function RunsPage() {
  usePageMeta("Onboarding runs");
  const runs = useRuns();
  const escs = useEscalations();
  const navigate = useNavigate();
  const [filter, setFilterState] = useState(() => getPref("dash.filter", "all"));
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort<Key>>({ key: "updated", dir: "desc" });
  const paging = usePaging("runs");
  const setFilter = (f: string) => {
    setFilterState(f);
    setPref("dash.filter", f);
    paging.setPage(0);
  };

  const base = useMemo(
    () => (runs.data ?? []).map((r) => ({ run: r, view: deriveRun(r, null, escs.data ?? []) })),
    [runs.data, escs.data],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: base.length, attn: 0, active: 0, done: 0 };
    for (const x of base) c[x.view.group]++;
    return c;
  }, [base]);
  const filtered = useMemo(() => {
    const s = search.trim().toLowerCase();
    const rows = base.filter(
      (x) =>
        (filter === "all" || x.view.group === filter) &&
        (!s ||
          [x.view.name, x.run.opportunity_id, x.run.id].some((v) =>
            String(v ?? "")
              .toLowerCase()
              .includes(s),
          )),
    );
    return sortBy(rows, sort, (x, k) =>
      k === "customer" ? x.view.name.toLowerCase() : (x.run.updated_at ?? x.run.created_at),
    );
  }, [base, filter, search, sort]);
  const page = clampPage(paging.page, filtered.length, paging.size);
  const visible = filtered.slice(page * paging.size, (page + 1) * paging.size);
  const details = useRunDetails(visible.map((x) => x.run));

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[14.5px] text-ink-2">
        Each closed-won deal email starts a run. Select one to follow it step by step.
      </p>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label="Filter runs"
          className="inline-flex flex-wrap gap-1 rounded-ctl border border-line bg-sunken p-0.5"
        >
          {FILTERS.map((f) => (
            <button
              key={f.value}
              role="tab"
              type="button"
              aria-selected={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-[6px] px-3 text-[13.5px] font-medium text-ink-3 transition-colors hover:text-ink",
                filter === f.value && "bg-surface text-ink shadow-card",
              )}
            >
              {f.label}
              <span
                className={cn(
                  "tabular rounded-full px-1.5 text-[12px]",
                  f.value === "attn" && counts.attn > 0 ? "bg-warn text-white" : "bg-idle-soft text-ink-3",
                )}
              >
                {counts[f.value] ?? 0}
              </span>
            </button>
          ))}
        </div>
        <div className="relative w-full sm:w-64">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-3"
            aria-hidden
          />
          <Input
            type="search"
            aria-label="Search runs"
            placeholder="Customer, opportunity or run id"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              paging.setPage(0);
            }}
            className="pl-8"
          />
        </div>
      </div>

      <Card className="overflow-hidden">
        {runs.isPending ? (
          <SkeletonRows rows={6} className="p-5" />
        ) : runs.isError ? (
          <div className="p-5">
            <ErrorBox error={runs.error} />
          </div>
        ) : !visible.length ? (
          <EmptyState icon={Inbox} title={base.length ? "Nothing here right now" : "No runs yet"}>
            {base.length
              ? "Try another filter or search."
              : "Forward a closed-won deal email to the CS inbox to start one."}
          </EmptyState>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <SortTh label="Customer" k="customer" sort={sort} onSort={setSort} />
                  <Th>Status</Th>
                  <Th className="hidden md:table-cell">Pipeline</Th>
                  <SortTh
                    label="Updated"
                    k="updated"
                    sort={sort}
                    onSort={setSort}
                    className="hidden text-right sm:table-cell"
                  />
                </tr>
              </thead>
              <tbody>
                {visible.map((x, i) => {
                  const d = details[i]?.data;
                  const v = d ? deriveRun(x.run, d, escs.data ?? []) : x.view;
                  const hot = v.tone === "warn" || v.tone === "bad";
                  return (
                    <Tr
                      key={x.run.id}
                      onActivate={() => navigate(`/runs/${encodeURIComponent(x.run.id)}`)}
                      aria-label={`${v.name}: ${v.title}`}
                      className={cn(hot && "bg-warn-soft/35")}
                    >
                      <Td className="max-w-[280px]">
                        <div className="truncate font-medium text-ink">{v.name}</div>
                        <div className="mt-1 flex items-center gap-2">
                          <TierChip tier={v.tier} />
                          {x.run.opportunity_id ? (
                            <span className="truncate font-mono text-[12px] text-ink-3">
                              {x.run.opportunity_id}
                            </span>
                          ) : null}
                        </div>
                      </Td>
                      <Td>
                        <StatusChip run={x.run} view={v} />
                      </Td>
                      <Td className="hidden max-w-[420px] md:table-cell">
                        <MiniPipeline steps={v.steps} />
                        <div className={cn("mt-1.5 truncate text-[13px]", hot ? "text-warn" : "text-ink-3")}>
                          {hot && v.sub ? `${v.title}. ${v.sub}` : v.title}
                        </div>
                      </Td>
                      <Td className="hidden text-right whitespace-nowrap sm:table-cell">
                        <Tip
                          content={`Received ${fmtDateTime(x.run.created_at)}. Updated ${fmtDateTime(x.run.updated_at)}.`}
                        >
                          <span tabIndex={0} className="tabular text-[13px] text-ink-3">
                            {ago(x.run.updated_at ?? x.run.created_at)}
                          </span>
                        </Tip>
                      </Td>
                    </Tr>
                  );
                })}
              </tbody>
            </Table>
          </>
        )}
        <Pagination
          total={filtered.length}
          page={page}
          size={paging.size}
          onPage={paging.setPage}
          onSize={paging.setSize}
          noun="runs"
        />
      </Card>
    </div>
  );
}
