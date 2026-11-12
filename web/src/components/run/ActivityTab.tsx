import * as C from "@radix-ui/react-collapsible";
import { AlertTriangle, Check, ChevronRight, Circle, Minus, Phone, Wrench, X } from "lucide-react";
import { useMemo, useState } from "react";
import { describe, parseTranscript, speakerRole, type FeedTone } from "@/lib/activity";
import { cn } from "@/lib/cn";
import { fmtDateTime, fmtTime, plainError } from "@/lib/format";
import { getPref, setPref } from "@/lib/prefs";
import type { AuditRow } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { DataView } from "@/components/ui/data-view";
import { KeyValues, Mono } from "@/components/ui/misc";
import { clampPage, Pagination, usePaging } from "@/components/ui/pagination";
import { Tip } from "@/components/ui/tooltip";

const ICON: Record<FeedTone, { icon: typeof Check; cls: string }> = {
  ok: { icon: Check, cls: "bg-ok-soft text-ok border-ok-line" },
  warn: { icon: AlertTriangle, cls: "bg-warn-soft text-warn border-warn-line" },
  bad: { icon: X, cls: "bg-bad-soft text-bad border-bad-line" },
  info: { icon: Circle, cls: "bg-accent-soft text-accent border-accent-line" },
  idle: { icon: Minus, cls: "bg-idle-soft text-idle border-line" },
  tech: { icon: Wrench, cls: "bg-sunken text-ink-3 border-line" },
};

export function ActivityTab({ audit }: { audit: AuditRow[] }) {
  const [mode, setModeState] = useState(() => getPref("dash.feed", "key"));
  const setMode = (m: string) => {
    setModeState(m);
    setPref("dash.feed", m);
    paging.setPage(0);
  };
  const paging = usePaging("feed");
  const items = useMemo(
    () =>
      audit
        .map((row, i) => ({ row, i, d: describe(row) }))
        .filter((x) => mode === "all" || !x.d.tech)
        .reverse(),
    [audit, mode],
  );
  const latestDecision = audit.map((r) => r.step).lastIndexOf("decide_tier");
  const page = clampPage(paging.page, items.length, paging.size);
  const shown = items.slice(page * paging.size, (page + 1) * paging.size);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3">
        <span className="text-[13px] text-ink-3">Newest first</span>
        <div
          role="group"
          aria-label="Which events"
          className="inline-flex rounded-ctl border border-line bg-sunken p-0.5"
        >
          {[
            ["key", "Key events"],
            ["all", "Everything"],
          ].map(([v, l]) => (
            <button
              key={v}
              type="button"
              aria-pressed={mode === v}
              onClick={() => setMode(v)}
              className={cn(
                "h-7 rounded-[6px] px-3 text-[13px] font-medium text-ink-3 transition-colors",
                mode === v && "bg-surface text-ink shadow-card",
              )}
            >
              {l}
            </button>
          ))}
        </div>
      </div>
      {shown.length ? (
        <ol className="relative">
          {shown.map(({ row, i, d }, k) => (
            <FeedItem
              key={row.id ?? i}
              row={row}
              d={d}
              last={k === shown.length - 1}
              latestDecision={i === latestDecision}
            />
          ))}
        </ol>
      ) : (
        <EmptyState title="Nothing has happened yet" />
      )}
      <div className="-mx-5 -mb-5 mt-2">
        <Pagination
          total={items.length}
          page={page}
          size={paging.size}
          onPage={paging.setPage}
          onSize={paging.setSize}
          noun="events"
        />
      </div>
    </div>
  );
}

function FeedItem({
  row,
  d,
  last,
  latestDecision,
}: {
  row: AuditRow;
  d: ReturnType<typeof describe>;
  last: boolean;
  latestDecision: boolean;
}) {
  const [open, setOpen] = useState(false);
  const why = row.rationale && row.rationale !== "tool call succeeded" ? plainError(row.rationale) : "";
  const input = row.input && typeof row.input === "object" ? (row.input as Record<string, unknown>) : null;
  const transcript = row.step === "decide_tier" ? parseTranscript(input?.transcript) : [];
  const I = ICON[d.tone];
  const inputShown = input && "transcript" in input ? { ...input, transcript: "Shown above" } : row.input;
  return (
    <li className="relative grid grid-cols-[28px_1fr] gap-x-3 pb-4">
      {!last ? <span aria-hidden className="absolute top-7 bottom-0 left-[13.5px] w-px bg-line" /> : null}
      <span className={cn("relative z-[1] mt-0.5 grid size-7 place-items-center rounded-full border", I.cls)}>
        <I.icon className="size-3.5" strokeWidth={2.25} aria-hidden />
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span
            className={cn(
              "text-[14.5px] font-medium",
              d.tech ? "font-mono text-[13px] text-ink-2" : "text-ink",
            )}
          >
            {d.title}
          </span>
          <Tip content={fmtDateTime(row.ts)}>
            <time tabIndex={0} className="tabular text-[12.5px] text-ink-3">
              {fmtTime(row.ts)}
            </time>
          </Tip>
        </div>
        {why && !d.tech ? (
          <p className="mt-0.5 text-sm break-words whitespace-pre-wrap text-ink-2">{why}</p>
        ) : null}
        {transcript.length ? <Transcript lines={transcript} defaultOpen={latestDecision} /> : null}
        <C.Root open={open} onOpenChange={setOpen}>
          <C.Trigger className="mt-1 inline-flex items-center gap-1 rounded text-[12.5px] font-medium text-ink-3 hover:text-ink">
            <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} aria-hidden />
            Details
          </C.Trigger>
          <C.Content className="mt-2 space-y-3 rounded-ctl border border-line bg-sunken/60 p-3">
            <KeyValues
              items={[
                ["Step", <Mono key="s">{row.step}</Mono>],
                ["Agent", row.agent],
                [
                  "Status",
                  <Badge key="b" tone="neutral">
                    {row.status || "ok"}
                  </Badge>,
                ],
                ...(why && d.tech ? ([["Why", why]] as [string, string][]) : []),
              ]}
            />
            {row.input != null ? (
              <div>
                <div className="mb-1 text-[12.5px] font-medium text-ink-3">Input</div>
                <DataView value={inputShown} />
              </div>
            ) : null}
            {row.output != null ? (
              <div>
                <div className="mb-1 text-[12.5px] font-medium text-ink-3">Output</div>
                <DataView value={row.output} />
              </div>
            ) : null}
          </C.Content>
        </C.Root>
      </div>
    </li>
  );
}

export function Transcript({
  lines,
  defaultOpen = true,
}: {
  lines: { speaker: string; text: string }[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [all, setAll] = useState(false);
  const max = 6;
  const shown = all ? lines : lines.slice(0, max);
  return (
    <figure className="mt-2 rounded-ctl border border-line bg-surface">
      <figcaption>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className={cn(
            "flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px] font-medium text-ink-3 hover:text-ink",
            open && "border-b border-line",
          )}
        >
          <Phone className="size-3.5" aria-hidden />
          Call transcript
          <span className="hidden font-normal sm:inline">
            ({lines.length} lines, the AE's words are the evidence for the tier)
          </span>
          <ChevronRight
            className={cn("ml-auto size-3.5 transition-transform", open && "rotate-90")}
            aria-hidden
          />
        </button>
      </figcaption>
      {open ? (
        <ol className="space-y-2 p-3">
          {shown.map((l, i) => {
            const role = speakerRole(l.speaker);
            return (
              <li key={i} className={cn("flex", role === "ae" ? "justify-end" : "justify-start")}>
                <div
                  className={cn(
                    "max-w-[85%] rounded-[10px] px-3 py-1.5 text-sm",
                    role === "ae" ? "bg-accent-soft text-ink" : "bg-sunken text-ink-2",
                  )}
                >
                  {l.speaker ? (
                    <div className="text-[11.5px] font-medium text-ink-3">
                      {role === "agent" ? "Voice agent" : role === "ae" ? "AE" : l.speaker}
                    </div>
                  ) : null}
                  <p className="break-words whitespace-pre-wrap">{l.text}</p>
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
      {open && lines.length > max ? (
        <button
          type="button"
          onClick={() => setAll((x) => !x)}
          className="w-full border-t border-line px-3 py-1.5 text-[12.5px] font-medium text-accent hover:bg-sunken"
        >
          {all ? "Show less" : `Show all ${lines.length} lines`}
        </button>
      ) : null}
    </figure>
  );
}
