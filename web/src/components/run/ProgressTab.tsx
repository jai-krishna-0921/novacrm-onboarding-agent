import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { enc } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ago, fmtDateTime } from "@/lib/format";
import { post, qk } from "@/lib/queries";
import { trackingPct } from "@/lib/run-state";
import type { Run, Tracking, TrackedTask } from "@/lib/schemas";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/components/ui/toast";
import { Tip } from "@/components/ui/tooltip";
import { ArrowRight, FolderKanban } from "lucide-react";
import { Link } from "react-router";

export function Ring({ pct, size = 112 }: { pct: number; size?: number }) {
  const r = (size - 12) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${pct}% of tasks done`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e8ebf0" strokeWidth={10} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#2f5bd3"
          strokeWidth={10}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct / 100)}
          className="transition-[stroke-dashoffset] duration-700 ease-out-soft"
        />
      </svg>
      <span className="tabular absolute inset-0 grid place-items-center text-[22px] font-semibold text-ink">
        {pct}%
      </span>
    </div>
  );
}

export function ProgressTab({ run, tracking }: { run: Run; tracking: Tracking | null | undefined }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  if (!run.rocketlane_project_id && !tracking)
    return (
      <EmptyState icon={FolderKanban} title="No project yet">
        Progress shows here once the Rocketlane project exists.
      </EmptyState>
    );
  const sync = async () => {
    setBusy(true);
    try {
      await post(`/runs/${enc(run.id)}/sync`, {});
      toast({ title: "Synced from Rocketlane" });
      await qc.invalidateQueries({ queryKey: qk.run(run.id) });
    } catch (e) {
      toast({ title: "Sync failed", body: e instanceof Error ? e.message : String(e), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };
  const pct = trackingPct(tracking);
  const phases = (tracking?.phases ?? []).filter((p) => p.name);
  return (
    <div className="flex flex-col gap-5">
      {tracking ? (
        <div className="grid gap-6 md:grid-cols-[auto_1fr] md:items-center">
          <div className="flex items-center gap-4">
            <Ring pct={pct} />
            <div>
              <div className="text-[16px] font-semibold text-ink">
                {tracking.handed_off_at
                  ? "All phases done"
                  : tracking.current_phase
                    ? `${tracking.current_phase} phase`
                    : "All tasks done"}
              </div>
              <div className="text-sm text-ink-3">
                {tracking.tasks_done ?? 0} of {tracking.tasks_total ?? 0} tasks complete
                {tracking.tasks_in_progress ? `, ${tracking.tasks_in_progress} in progress` : ""}
              </div>
            </div>
          </div>
          {phases.length ? (
            <ul className="flex flex-col gap-2.5 md:border-l md:border-line md:pl-6">
              {phases.map((p) => {
                const f = p.total ? Math.max(0, Math.min(1, p.done / p.total)) : 0;
                const g = p.total ? Math.max(0, Math.min(1 - f, (p.in_progress ?? 0) / p.total)) : 0;
                const current = p.name === tracking.current_phase;
                const st = phaseState(p);
                return (
                  <li
                    key={p.name}
                    className="grid grid-cols-[minmax(96px,150px)_auto_1fr_auto] items-center gap-3"
                  >
                    <span
                      className={cn("truncate text-sm", current ? "font-semibold text-ink" : "text-ink-2")}
                    >
                      {p.name}
                    </span>
                    <Tip content={st.tip}>
                      <Badge tone={st.tone} tabIndex={0} className="cursor-default">
                        {st.label}
                      </Badge>
                    </Tip>
                    <Tip
                      content={`${p.name}: ${p.done} done, ${p.in_progress ?? 0} in progress, ${p.total} tasks${current ? ". Current phase" : ""}`}
                    >
                      <span tabIndex={0} className="flex h-2 overflow-hidden rounded-full bg-[#eceef2]">
                        <span
                          className={cn(
                            "block h-full transition-[width] duration-700 ease-out-soft",
                            f >= 1 || st.label === "Done" ? "bg-ok" : "bg-accent",
                          )}
                          style={{ width: `${f * 100}%` }}
                        />
                        <span
                          className="block h-full bg-[#9db3ee] transition-[width] duration-700 ease-out-soft"
                          style={{ width: `${g * 100}%` }}
                        />
                      </span>
                    </Tip>
                    <span className="tabular text-[13px] text-ink-3">
                      {p.done}/{p.total}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-ink-3">Waiting for the first sync from Rocketlane.</p>
      )}
      {tracking?.tasks?.length ? (
        <TaskList tasks={tracking.tasks} phases={phases.map((p) => p.name)} />
      ) : null}
      {tracking?.handoff_summary ? (
        <div>
          <div className="mb-1.5 text-[13px] font-medium text-ink-3">Handoff summary</div>
          <p className="rounded-ctl border border-line bg-sunken p-3 text-sm break-words whitespace-pre-wrap text-ink-2">
            {tracking.handoff_summary}
          </p>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <Tip content={tracking?.last_synced_at ? fmtDateTime(tracking.last_synced_at) : "No sync yet"}>
          <span tabIndex={0} className="text-[13px] text-ink-3">
            {tracking?.last_synced_at
              ? `Synced ${ago(tracking.last_synced_at)}. Updates by itself when Rocketlane changes.`
              : "Not synced yet"}
          </span>
        </Tip>
        {run.rocketlane_project_id ? (
          <Button onClick={sync} loading={busy}>
            <RefreshCw className="size-4" aria-hidden />
            Sync now
          </Button>
        ) : null}
      </div>
    </div>
  );
}

type PhaseRow = NonNullable<Tracking["phases"]>[number];

const lower = (s: string | null | undefined) => (s ?? "").trim().toLowerCase();

/** The phase's chip: its own Rocketlane status when a person set one, else derived from its tasks. */
export function phaseState(p: PhaseRow): { label: string; tone: BadgeTone; tip: string } {
  const s = lower(p.status);
  if (p.blocked || s === "blocked")
    return { label: "Blocked", tone: "bad", tip: "Marked Blocked in Rocketlane. The CS team is flagged." };
  if (p.total && p.done >= p.total)
    return { label: "Done", tone: "ok", tip: "Every task in this phase is completed." };
  if (s === "completed" || s === "done")
    return {
      label: "Marked done",
      tone: "ok",
      tip: `Marked Completed in Rocketlane with ${p.total - p.done} task(s) still open.`,
    };
  if (p.started || p.done || p.in_progress)
    return { label: "In progress", tone: "active", tip: "Work has started in this phase." };
  return { label: "Not started", tone: "idle", tip: "No task in this phase has started yet." };
}

const TASK_TONE: Record<string, BadgeTone> = {
  completed: "ok",
  done: "ok",
  "in progress": "active",
  blocked: "bad",
};

function TaskList({ tasks, phases }: { tasks: TrackedTask[]; phases: string[] }) {
  const order = [...phases, ...new Set(tasks.map((t) => t.phase ?? "No phase"))].filter(
    (p, i, a) => a.indexOf(p) === i,
  );
  return (
    <div className="flex flex-col gap-3">
      <div className="text-[13px] font-medium text-ink-3">Tasks in Rocketlane</div>
      {order.map((ph) => {
        const mine = tasks.filter((t) => (t.phase ?? "No phase") === ph);
        if (!mine.length) return null;
        const top = mine.filter((t) => !t.parent);
        const open = mine.some((t) => !t.completed);
        return (
          <details key={ph} open={open} className="group rounded-ctl border border-line">
            <summary className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm font-medium text-ink">
              <span>{ph}</span>
              <span className="flex items-center gap-3">
                {ph === "Data Migration" ? (
                  <Link
                    to="/migration"
                    className="inline-flex items-center gap-1 text-[13px] font-normal text-accent hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    Migration data <ArrowRight className="size-3.5" aria-hidden />
                  </Link>
                ) : null}
                <span className="tabular text-[13px] font-normal text-ink-3">
                  {mine.filter((t) => t.completed).length}/{mine.length}
                </span>
              </span>
            </summary>
            <ul className="divide-y divide-line border-t border-line">
              {top.map((t) => (
                <TaskRow key={t.name} t={t} subs={mine.filter((s) => s.parent === t.name)} />
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}

function TaskRow({ t, subs }: { t: TrackedTask; subs: TrackedTask[] }) {
  return (
    <li className="px-3 py-2">
      <TaskLine t={t} />
      {subs.length ? (
        <ul className="mt-1.5 flex flex-col gap-1.5 border-l border-line pl-3">
          {subs.map((s) => (
            <li key={s.name}>
              <TaskLine t={s} sub />
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function TaskLine({ t, sub }: { t: TrackedTask; sub?: boolean }) {
  const label = t.completed ? "Completed" : t.status_label || "To do";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span
        className={cn("min-w-0 flex-1 text-sm", sub ? "text-ink-2" : "text-ink", t.completed && "text-ink-3")}
      >
        {t.name}
        {t.depends_on?.length ? (
          <span className="ml-2 text-[12.5px] text-ink-3">waits on {t.depends_on.join(", ")}</span>
        ) : null}
      </span>
      {t.due_date ? <span className="tabular text-[12.5px] text-ink-3">due {fmtDay(t.due_date)}</span> : null}
      <Badge tone={TASK_TONE[lower(label)] ?? "idle"}>{label}</Badge>
    </div>
  );
}

function fmtDay(d: string): string {
  const x = new Date(`${d.slice(0, 10)}T00:00:00`);
  return Number.isNaN(x.getTime()) ? d : x.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
