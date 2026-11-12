import { CircleCheck, CircleX } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { predictChecks } from "@/lib/migration";
import type { MigrationSummary } from "@/lib/schemas";
import { Card } from "@/components/ui/card";

/** The five checks evaluated on the data as it is right now: the answer to "would this migration pass?". */
export function MigrationVerdict({
  summary,
  footer,
  className,
}: {
  summary: MigrationSummary;
  footer?: ReactNode;
  className?: string;
}) {
  const checks = predictChecks(summary);
  const failed = checks.filter((c) => !c.passed).length;
  const ok = failed === 0;
  return (
    <Card className={cn(ok ? "border-ok-line" : "border-bad-line", className)} aria-label="Migration verdict">
      <div
        className={cn("flex items-start gap-3 rounded-t-card px-5 py-4", ok ? "bg-ok-soft" : "bg-bad-soft")}
      >
        {ok ? (
          <CircleCheck className="mt-0.5 size-5 shrink-0 text-ok" aria-hidden />
        ) : (
          <CircleX className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden />
        )}
        <div className="min-w-0">
          <h2 className={cn("text-[15.5px] font-semibold", ok ? "text-ok" : "text-bad")}>
            {ok ? "Ready to verify" : "Not ready to verify"}
          </h2>
          <p className="mt-0.5 text-sm text-ink-2">
            {ok
              ? `All ${checks.length} checks pass on the data right now.`
              : `${failed} of ${checks.length} checks fail on the data right now. "Migration verified" stays open until they pass.`}
          </p>
        </div>
      </div>
      <ul className="divide-y divide-line">
        {checks.map((c) => (
          <li key={c.name} className="flex items-start gap-3 px-5 py-2.5">
            {c.passed ? (
              <CircleCheck className="mt-0.5 size-4 shrink-0 text-ok" aria-label="passes" />
            ) : (
              <CircleX className="mt-0.5 size-4 shrink-0 text-bad" aria-label="fails" />
            )}
            <span className="w-36 shrink-0 text-sm font-medium text-ink">{c.label}</span>
            <span className={cn("min-w-0 text-sm", c.passed ? "text-ink-3" : "text-ink-2")}>{c.detail}</span>
          </li>
        ))}
      </ul>
      {footer ? <div className="border-t border-line px-5 py-3 text-[13px] text-ink-3">{footer}</div> : null}
    </Card>
  );
}
