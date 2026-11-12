import { ChevronDown, PhoneCall } from "lucide-react";
import { useState } from "react";
import { enc } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDur, fmtTime } from "@/lib/format";
import { useRunCalls } from "@/lib/queries";
import type { Run, RunCall } from "@/lib/schemas";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";

const OUTCOME: Record<string, [BadgeTone, string]> = {
  confirmed: ["ok", "Confirmed"],
  unclear: ["warn", "Unclear"],
  no_answer: ["idle", "No answer"],
};

/** "Agent: ..." / "AE: ..." lines. Rendered as text only (the transcript is untrusted). */
export function parseTranscript(t: string): { who: "agent" | "ae" | null; text: string }[] {
  return t
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = /^(Agent|AE|User)\s*:\s*(.*)$/i.exec(l);
      if (!m) return { who: null, text: l };
      return { who: m[1].toLowerCase() === "agent" ? "agent" : "ae", text: m[2] };
    });
}

function Transcript({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const lines = parseTranscript(text);
  return (
    <div className="mt-3">
      <button
        type="button"
        className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-strong hover:underline"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden />
        {open ? "Hide transcript" : `Show transcript (${lines.length} lines)`}
      </button>
      {open ? (
        <ol className="mt-2 flex flex-col gap-1.5 rounded-ctl border border-line bg-sunken p-3">
          {lines.map((l, i) => (
            <li
              key={i}
              className={cn("flex gap-2 text-[13.5px]", l.who === "ae" ? "text-ink" : "text-ink-2")}
            >
              <span
                className={cn(
                  "w-12 shrink-0 text-[12px] font-semibold uppercase tracking-wide",
                  l.who === "ae" ? "text-accent-strong" : "text-ink-3",
                )}
              >
                {l.who === "ae" ? "AE" : l.who === "agent" ? "Agent" : ""}
              </span>
              <span
                className={cn("min-w-0 whitespace-pre-wrap break-words", l.who === "ae" && "font-medium")}
              >
                {l.text}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function CallItem({ runId, call }: { runId: string; call: RunCall }) {
  const [tone, label] = OUTCOME[call.outcome ?? ""] ?? ["neutral", "In progress"];
  const when = call.started_at != null ? fmtTime(call.started_at, true) : null;
  const dur = call.duration_ms != null ? fmtDur(call.duration_ms / 1000) : null;
  return (
    <li className="rounded-card border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-ink">Attempt {call.attempt}</span>
        <Badge tone={tone} dot>
          {label}
        </Badge>
        {when || dur ? (
          <span className="text-[13px] text-ink-3">{[when, dur].filter(Boolean).join(" · ")}</span>
        ) : null}
      </div>
      {call.has_recording ? (
        <audio
          className="mt-3 w-full max-w-xl"
          controls
          preload="none"
          src={`/runs/${enc(runId)}/calls/${enc(call.call_id)}/recording`}
          aria-label={`Recording of call attempt ${call.attempt}`}
        />
      ) : (
        <p className="mt-2 text-[13px] text-ink-3">No recording available.</p>
      )}
      {call.transcript ? <Transcript text={call.transcript} /> : null}
    </li>
  );
}

export function CallsTab({ run }: { run: Run }) {
  const q = useRunCalls(run.id, String(run.status ?? ""));
  if (q.isPending)
    return (
      <div className="flex flex-col gap-3" role="status" aria-label="Loading calls">
        <Skeleton className="h-24 w-full rounded-card" />
      </div>
    );
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data.length)
    return (
      <EmptyState icon={PhoneCall} title="No calls yet">
        The agent phones the AE to confirm the plan tier. Each attempt shows up here with its recording.
      </EmptyState>
    );
  return (
    <ul className="flex flex-col gap-3">
      {q.data.map((c) => (
        <CallItem key={c.call_id} runId={run.id} call={c} />
      ))}
    </ul>
  );
}
