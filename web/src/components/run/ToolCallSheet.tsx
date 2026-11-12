import { ArrowRight } from "lucide-react";
import { Link } from "react-router";
import { fmtDateTime, fmtMs } from "@/lib/format";
import { useToolCall } from "@/lib/queries";
import type { ToolCallRow } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";
import { ErrorBox } from "@/components/ui/empty-state";
import { CodeBlock, KeyValues, Mono } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

export function toolTone(status: string | null | undefined) {
  return status === "ok" ? "ok" : status === "failed" ? "bad" : "idle";
}

/** Metadata from the list row; input and output load on demand from GET /tool-calls/{id}. */
export function ToolCallSheet({
  row,
  onClose,
  showRunLink = true,
}: {
  row: ToolCallRow | null;
  onClose: () => void;
  showRunLink?: boolean;
}) {
  const detail = useToolCall(row?.id ?? null);
  return (
    <Sheet
      open={!!row}
      onOpenChange={(o) => !o && onClose()}
      title={row?.tool ?? "Tool call"}
      eyebrow={
        row ? (
          <Badge tone={toolTone(row.status)} dot>
            {row.status}
          </Badge>
        ) : null
      }
    >
      {row ? (
        <div className="flex flex-col gap-5">
          <KeyValues
            items={[
              ["Time", fmtDateTime(row.ts)],
              ["Customer", row.customer_name],
              ["Run", row.run_id ? <Mono key="r">{row.run_id}</Mono> : null],
              ["Agent", row.agent],
              ["Attempt", row.attempt != null ? String(row.attempt) : null],
              ["Duration", fmtMs(row.latency_ms)],
              ["Idempotency key", row.idempotency_key ? <Mono key="k">{row.idempotency_key}</Mono> : null],
            ]}
          />
          {detail.isPending ? (
            <div className="space-y-2" role="status" aria-label="Loading input and output">
              <Skeleton className="h-4 w-16" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-4 w-16" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : detail.isError ? (
            <ErrorBox error={detail.error} />
          ) : (
            <>
              <div>
                <div className="mb-1 text-[13px] font-medium text-ink-3">Input</div>
                <CodeBlock value={detail.data?.args ?? null} />
              </div>
              <div>
                <div className="mb-1 text-[13px] font-medium text-ink-3">Output</div>
                <CodeBlock value={detail.data?.result ?? null} />
              </div>
              <p className="text-[12.5px] text-ink-3">
                Phone numbers are masked to the last 4 digits. Secrets are redacted.
              </p>
            </>
          )}
          {showRunLink && row.run_id ? (
            <Link
              to={`/runs/${encodeURIComponent(row.run_id)}`}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline"
            >
              Open run
              <ArrowRight className="size-4" aria-hidden />
            </Link>
          ) : null}
        </div>
      ) : null}
    </Sheet>
  );
}
