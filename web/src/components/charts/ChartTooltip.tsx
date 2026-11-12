import type { TooltipContent } from "@/lib/chart-tooltip";
import { cn } from "@/lib/cn";

const TONE = {
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  muted: "bg-line-strong",
} as const;

export function ChartTooltipCard({ content }: { content: TooltipContent }) {
  return (
    <div className="min-w-[200px] max-w-[300px] rounded-ctl border border-line bg-surface px-3 py-2.5 text-[13px] shadow-pop">
      <div className="mb-1.5 font-semibold text-ink">{content.title}</div>
      <dl className="space-y-1">
        {content.rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-4">
            <dt className="flex items-center gap-1.5 text-ink-3">
              {r.tone && r.tone !== "muted" ? (
                <span className={cn("size-2 rounded-[2px]", TONE[r.tone])} aria-hidden />
              ) : null}
              {r.label}
            </dt>
            <dd
              className={cn(
                "tabular text-right font-medium text-ink",
                r.tone === "muted" && "font-normal text-ink-2",
              )}
            >
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
      {content.note ? (
        <p className="mt-1.5 border-t border-line pt-1.5 text-[12px] text-ink-3">{content.note}</p>
      ) : null}
    </div>
  );
}

/** Adapter for Recharts' `content` prop: builds the card from the hovered datum. */
export function rechartsTooltip<T>(build: (datum: T) => TooltipContent) {
  return function RechartsTooltipContent(props: {
    active?: boolean;
    payload?: ReadonlyArray<{ payload?: unknown }>;
  }) {
    const datum = props.active && props.payload && props.payload.length ? props.payload[0].payload : null;
    if (!datum) return null;
    return <ChartTooltipCard content={build(datum as T)} />;
  };
}
