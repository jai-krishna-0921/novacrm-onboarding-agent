import { AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/cn";
import type { RunView } from "@/lib/run-state";

const STYLE = {
  active: "border-accent-line bg-accent-soft text-accent-strong",
  ok: "border-ok-line bg-ok-soft text-ok",
  warn: "border-warn-line bg-warn-soft text-warn",
  bad: "border-bad-line bg-bad-soft text-bad",
  idle: "border-line bg-idle-soft text-idle",
} as const;

export function NowBanner({ view }: { view: Pick<RunView, "tone" | "title" | "sub"> }) {
  const Icon =
    view.tone === "ok"
      ? CheckCircle2
      : view.tone === "warn" || view.tone === "bad"
        ? AlertTriangle
        : view.tone === "idle"
          ? XCircle
          : Loader2;
  return (
    <div
      role="status"
      className={cn("flex items-start gap-3 rounded-card border px-4 py-3", STYLE[view.tone])}
    >
      <Icon
        className={cn(
          "mt-0.5 size-5 shrink-0",
          view.tone === "active" && "animate-spin [animation-duration:2s]",
        )}
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[15px] font-semibold">{view.title}</div>
        {view.sub ? <p className="mt-0.5 text-sm break-words text-ink-2">{view.sub}</p> : null}
      </div>
    </div>
  );
}
