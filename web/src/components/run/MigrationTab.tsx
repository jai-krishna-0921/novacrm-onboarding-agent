import { useQueryClient } from "@tanstack/react-query";
import { Database, Play } from "lucide-react";
import { useState } from "react";
import { api, enc } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { qk } from "@/lib/queries";
import { MigrationCheckResult, type MigrationCheckRow, type Run } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/components/ui/toast";
import { CheckHistory, CheckTiles, splitBatches } from "./MigrationTiles";

export function useRunMigrationCheck(runId: string | null) {
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!runId) return;
    setBusy(true);
    try {
      const r = await api(`/runs/${enc(runId)}/migration-check`, MigrationCheckResult, {
        method: "POST",
        body: {},
      });
      toast(
        r.passed
          ? { title: "All migration checks passed" }
          : { title: "Some checks failed", body: "The CSM has an escalation with the details.", tone: "bad" },
      );
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.run(runId) }),
        qc.invalidateQueries({ queryKey: qk.escalations }),
        qc.invalidateQueries({ queryKey: qk.migSummary }),
      ]);
    } catch (e) {
      toast({ title: "Check could not run", body: e instanceof Error ? e.message : String(e), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };
  return { run, busy };
}

export function MigrationTab({ run, checks }: { run: Run; checks: MigrationCheckRow[] }) {
  const { run: check, busy } = useRunMigrationCheck(run.id);
  if (!run.rocketlane_project_id && !checks.length)
    return (
      <EmptyState icon={Database} title="No project yet">
        The data migration check runs after the Rocketlane project exists.
      </EmptyState>
    );
  const { latest, older } = splitBatches(checks);
  const failed = latest.filter((c) => !c.passed).length;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {latest.length ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={failed ? "bad" : "ok"}>
              {failed ? `${failed} of ${latest.length} checks failed` : `All ${latest.length} checks passed`}
            </Badge>
            <span className="text-[13px] text-ink-3">{fmtTime(latest[0].ts, true)}</span>
          </div>
        ) : (
          <span className="text-sm text-ink-3">
            No check has run yet. It runs by itself when the import task is completed in Rocketlane.
          </span>
        )}
        {run.rocketlane_project_id ? (
          <Button onClick={check} loading={busy}>
            <Play className="size-4" aria-hidden />
            Run check
          </Button>
        ) : null}
      </div>
      {latest.length ? <CheckTiles checks={latest} /> : null}
      {older.length ? (
        <div>
          <h3 className="mb-2 text-[13px] font-medium text-ink-3">Earlier checks</h3>
          <div className="-mx-5">
            <CheckHistory checks={older} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
