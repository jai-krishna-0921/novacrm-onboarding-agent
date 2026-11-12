import { forwardRef, type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Tip } from "./tooltip";

export type BadgeTone = "active" | "ok" | "warn" | "bad" | "idle" | "accent" | "neutral";

const TONE: Record<BadgeTone, string> = {
  active: "bg-accent-soft text-accent-strong border-accent-line",
  accent: "bg-accent-soft text-accent-strong border-accent-line",
  ok: "bg-ok-soft text-ok border-ok-line",
  warn: "bg-warn-soft text-warn border-warn-line",
  bad: "bg-bad-soft text-bad border-bad-line",
  idle: "bg-idle-soft text-idle border-line",
  neutral: "bg-surface text-ink-2 border-line-strong",
};
const DOT: Record<BadgeTone, string> = {
  active: "bg-accent",
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  idle: "bg-idle",
  neutral: "bg-ink-3",
};

type Props = HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; dot?: boolean; mono?: boolean };

export const Badge = forwardRef<HTMLSpanElement, Props>(function Badge(
  { tone = "neutral", dot, mono, className, children, ...rest },
  ref,
) {
  return (
    <span
      ref={ref}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[12.5px] font-medium leading-5",
        TONE[tone],
        mono && "font-mono text-[12px]",
        className,
      )}
      {...rest}
    >
      {dot ? <span className={cn("size-1.5 shrink-0 rounded-full", DOT[tone])} aria-hidden /> : null}
      <span className="truncate">{children}</span>
    </span>
  );
});

/** A badge with an explanation on hover/focus. */
export function TipBadge({ tip, ...props }: Props & { tip: ReactNode }) {
  return (
    <Tip content={tip}>
      <Badge tabIndex={0} {...props} />
    </Tip>
  );
}
