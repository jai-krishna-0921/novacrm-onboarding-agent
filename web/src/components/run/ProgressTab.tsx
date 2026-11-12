import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { enc } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ago, fmtDateTime } from "@/lib/format";
import { post, qk } from "@/lib/queries";
import { trackingPct } from "@/lib/run-state";
import type { Run, Tracking } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/components/ui/toast";
import { Tip } from "@/components/ui/tooltip";
import { FolderKanban } from "lucide-react";

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
              </div>
            </div>
          </div>
          {phases.length ? (
            <ul className="flex flex-col gap-2.5 md:border-l md:border-line md:pl-6">
              {phases.map((p) => {
                const f = p.total ? Math.max(0, Math.min(1, p.done / p.total)) : 0;
                const current = p.name === tracking.current_phase;
                return (
                  <li
                    key={p.name}
                    className="grid grid-cols-[minmax(96px,160px)_1fr_auto] items-center gap-3"
                  >
                    <span
                      className={cn("truncate text-sm", current ? "font-semibold text-ink" : "text-ink-2")}
                    >
                      {p.name}
                    </span>
                    <Tip
                      content={`${p.name}: ${p.done} of ${p.total} tasks done (${Math.round(f * 100)}%)${current ? ". Current phase" : ""}`}
                    >
                      <span tabIndex={0} className="h-2 overflow-hidden rounded-full bg-[#eceef2]">
                        <span
                          className={cn(
                            "block h-full origin-left rounded-full transition-transform duration-700 ease-out-soft",
                            f >= 1 ? "bg-ok" : "bg-accent",
                          )}
                          style={{ transform: `scaleX(${f})` }}
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
            {tracking?.last_synced_at ? `Synced ${ago(tracking.last_synced_at)}` : "Not synced yet"}
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
