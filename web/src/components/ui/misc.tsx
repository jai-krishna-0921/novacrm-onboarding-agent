import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Tip } from "./tooltip";

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-[13px] break-all", className)}>{children}</span>;
}

export function KeyValues({ items, className }: { items: [string, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-2 text-sm", className)}>
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd className="min-w-0 break-words text-ink">
            {v === null || v === undefined || v === "" ? "-" : v}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone,
  tip,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "ok" | "warn" | "bad";
  tip?: string;
}) {
  const body = (
    <div
      tabIndex={tip ? 0 : undefined}
      className={cn(
        "relative flex min-w-0 flex-col gap-1 rounded-card border border-line bg-surface px-4 py-3.5 shadow-card",
        tone === "warn" && "border-warn-line",
        tone === "bad" && "border-bad-line",
      )}
    >
      <span className="text-[13px] text-ink-3">{label}</span>
      <span
        className={cn(
          "tabular text-[24px] leading-7 font-semibold tracking-[-0.02em] text-ink",
          tone === "warn" && "text-warn",
          tone === "bad" && "text-bad",
          tone === "ok" && "text-ok",
        )}
      >
        {value}
      </span>
      {sub ? <span className="truncate text-[12.5px] text-ink-3">{sub}</span> : null}
    </div>
  );
  return tip ? <Tip content={tip}>{body}</Tip> : body;
}
