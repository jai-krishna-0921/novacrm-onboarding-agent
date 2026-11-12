import { CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/cn";
import { fmtDateTime, fmtTime, human, str } from "@/lib/format";
import { CHECKS } from "@/lib/migration";
import type { MigrationCheckRow } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";

export function checkDetail(c: Pick<MigrationCheckRow, "details">): string {
  const d = c.details ?? {};
  const parts: string[] = [];
  if (d.expected !== undefined || d.actual !== undefined)
    parts.push(`Expected ${str(d.expected)}, found ${str(d.actual)}`);
  if (Array.isArray(d.sample_ids) && d.sample_ids.length)
    parts.push(`ids ${d.sample_ids.slice(0, 5).map(str).join(", ")}`);
  return parts.join(". ");
}

/** Latest batch as pass/fail tiles; older batches as a history table. */
export function splitBatches(checks: MigrationCheckRow[]) {
  const lastBatch = checks.length ? checks[checks.length - 1].batch_id : null;
  return {
    latest: checks.filter((c) => c.batch_id === lastBatch),
    older: checks.filter((c) => c.batch_id !== lastBatch),
  };
}

export function CheckTiles({ checks }: { checks: MigrationCheckRow[] }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {checks.map((c) => {
        const meta = CHECKS[c.check_name];
        const detail = checkDetail(c);
        return (
          <Tip key={c.check_name} content={meta?.explain}>
            <div
              tabIndex={0}
              className={cn(
                "rounded-card border p-3.5",
                c.passed ? "border-ok-line bg-ok-soft/60" : "border-bad-line bg-bad-soft/70",
              )}
            >
              <div className="text-[13px] text-ink-2">{meta?.label ?? human(c.check_name)}</div>
              <div
                className={cn(
                  "mt-1 flex items-center gap-1.5 text-[17px] font-semibold",
                  c.passed ? "text-ok" : "text-bad",
                )}
              >
                {c.passed ? (
                  <CheckCircle2 className="size-[18px]" aria-hidden />
                ) : (
                  <XCircle className="size-[18px]" aria-hidden />
                )}
                {c.passed ? "Pass" : "Fail"}
              </div>
              {detail ? <div className="mt-1 text-[12.5px] break-words text-ink-3">{detail}</div> : null}
            </div>
          </Tip>
        );
      })}
    </div>
  );
}

export function CheckHistory({ checks }: { checks: MigrationCheckRow[] }) {
  const batches = new Map<string, MigrationCheckRow[]>();
  for (const c of checks) {
    const k = c.batch_id ?? "";
    batches.set(k, [...(batches.get(k) ?? []), c]);
  }
  const rows = [...batches.entries()].reverse();
  return (
    <Table>
      <thead>
        <tr>
          <Th>When</Th>
          <Th>Result</Th>
          <Th>Failed checks</Th>
          <Th className="hidden sm:table-cell">Batch</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([batch, cs]) => {
          const failed = cs.filter((c) => !c.passed);
          return (
            <Tr key={batch}>
              <Td className="whitespace-nowrap">
                <Tip content={fmtDateTime(cs[0].ts)}>
                  <span tabIndex={0}>{fmtTime(cs[0].ts, true)}</span>
                </Tip>
              </Td>
              <Td>
                <Badge tone={failed.length ? "bad" : "ok"}>
                  {failed.length ? `${failed.length} of ${cs.length} failed` : "All passed"}
                </Badge>
              </Td>
              <Td className="text-sm text-ink-2">
                {failed.map((c) => CHECKS[c.check_name]?.label ?? human(c.check_name)).join(", ") || "-"}
              </Td>
              <Td className="hidden font-mono text-[12.5px] text-ink-3 sm:table-cell">{batch}</Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}
