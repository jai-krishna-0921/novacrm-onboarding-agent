import { AlertTriangle, Check, X } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Step } from "@/lib/run-state";
import { Tip } from "@/components/ui/tooltip";

const DOT: Record<Step["state"], string> = {
  done: "bg-accent text-white border-accent",
  active: "bg-surface text-accent border-accent animate-pulse-ring",
  warn: "bg-warn-soft text-warn border-warn",
  bad: "bg-bad-soft text-bad border-bad",
  idle: "bg-idle-soft text-idle border-line-strong",
  todo: "bg-surface text-ink-3 border-line-strong",
};

const STATE_WORD: Record<Step["state"], string> = {
  done: "Done",
  active: "In progress",
  warn: "Needs attention",
  bad: "Failed",
  idle: "Stopped",
  todo: "Not started",
};

function Mark({ s, i }: { s: Step; i: number }) {
  if (s.state === "done") return <Check className="size-3.5" strokeWidth={2.5} aria-hidden />;
  if (s.state === "warn" || s.state === "bad") return <AlertTriangle className="size-3.5" aria-hidden />;
  if (s.state === "idle") return <X className="size-3.5" aria-hidden />;
  return <span className="text-[12px] font-semibold">{i + 1}</span>;
}

/** The 7-step pipeline. Horizontal from md up, vertical on phones and in side panels. */
export function Stepper({ steps, vertical }: { steps: Step[]; vertical?: boolean }) {
  return (
    <ol
      aria-label="Pipeline"
      className={cn("grid gap-0", vertical ? "grid-cols-1" : "grid-cols-1 md:grid-cols-7")}
    >
      {steps.map((s, i) => (
        <li
          key={s.key}
          aria-current={s.state === "active" ? "step" : undefined}
          className={cn(
            "relative flex min-w-0 gap-3",
            vertical
              ? "pb-4 last:pb-0"
              : "pb-4 last:pb-0 md:flex-col md:items-center md:gap-2 md:pb-0 md:text-center",
          )}
        >
          {i > 0 ? (
            <span
              aria-hidden
              className={cn(
                "absolute bg-line-strong",
                s.reached && "bg-accent",
                vertical
                  ? "top-[-16px] left-[13px] h-4 w-0.5"
                  : "top-[-16px] left-[13px] h-4 w-0.5 md:top-[13px] md:right-[calc(50%+18px)] md:left-[calc(-50%+18px)] md:h-0.5 md:w-auto",
              )}
            />
          ) : null}
          <Tip content={`${s.label}: ${STATE_WORD[s.state]}${s.sub ? `. ${s.sub}` : ""}`}>
            <span
              tabIndex={0}
              className={cn(
                "relative z-[1] grid size-7 shrink-0 place-items-center rounded-full border-2 transition-colors duration-300",
                DOT[s.state],
              )}
            >
              <Mark s={s} i={i} />
              <span className="sr-only">{STATE_WORD[s.state]}</span>
            </span>
          </Tip>
          <span className="flex min-w-0 flex-col">
            <span
              className={cn(
                "text-[13.5px] leading-5 font-medium",
                s.state === "todo" ? "text-ink-3" : "text-ink",
              )}
            >
              {s.label}
            </span>
            <span
              className={cn(
                "text-[12.5px] leading-4 text-ink-3",
                s.state === "warn" && "text-warn",
                s.state === "bad" && "text-bad",
                s.state === "active" && "text-accent",
              )}
            >
              {s.sub}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Tiny 7-segment bar for list rows. */
export function MiniPipeline({ steps }: { steps: Step[] }) {
  const COLOR: Record<Step["state"], string> = {
    done: "bg-accent",
    active: "bg-accent/40 animate-pulse",
    warn: "bg-warn",
    bad: "bg-bad",
    idle: "bg-line-strong",
    todo: "bg-line",
  };
  return (
    <Tip
      content={
        <ul className="space-y-0.5">
          {steps.map((s) => (
            <li key={s.key}>
              {s.label}: {STATE_WORD[s.state].toLowerCase()}
            </li>
          ))}
        </ul>
      }
    >
      <span tabIndex={0} className="flex h-2 w-full max-w-[160px] gap-[3px]" aria-label="Pipeline progress">
        {steps.map((s) => (
          <span key={s.key} className={cn("h-full flex-1 rounded-full", COLOR[s.state])} />
        ))}
      </span>
    </Tip>
  );
}
