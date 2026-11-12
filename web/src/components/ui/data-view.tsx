import { Check, ChevronRight, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { fmtDateTime, longDay } from "@/lib/format";
import { Badge } from "./badge";
import { Tip } from "./tooltip";

// Readable view of arbitrary tool/audit payloads. Everything renders as text nodes:
// HTML in values is stripped to plain text, never injected.

const ACRONYMS: Record<string, string> = {
  id: "ID",
  ids: "IDs",
  url: "URL",
  urls: "URLs",
  ae: "AE",
  api: "API",
  llm: "LLM",
  ms: "(ms)",
  usd: "(USD)",
  arr: "ARR",
  csv: "CSV",
  rl: "Rocketlane",
  cs: "CS",
  sla: "SLA",
  utc: "UTC",
  http: "HTTP",
};

/** "rl_project_id" -> "Rocketlane project ID", "startDate" -> "Start date". */
export function humanizeKey(k: string): string {
  const words = k
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[_\s.:-]+/)
    .filter(Boolean)
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  if (!words.length) return k;
  const first = words[0];
  words[0] = first === first.toLowerCase() ? first.charAt(0).toUpperCase() + first.slice(1) : first;
  return words.join(" ");
}

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
const HTML_RE = /<\/?[a-z][a-z0-9]*\b[^>]*>/i;

/** Rocketlane notes etc.: tags removed, block ends become line breaks, list items become bullets. */
export function stripHtml(s: string): string {
  return s
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<li\b[^>]*>/gi, "\n• ")
    .replace(/<\/(p|div|h[1-6]|li|ul|ol|tr)>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (m, e: string) => {
      if (e[0] === "#") {
        const n = e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        return Number.isFinite(n) ? String.fromCodePoint(n) : m;
      }
      return ENTITIES[e.toLowerCase()] ?? m;
    })
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isScalar = (v: unknown) => v === null || v === undefined || typeof v !== "object";
const isEmpty = (v: unknown) =>
  v === null ||
  v === undefined ||
  (typeof v === "string" && v.trim() === "") ||
  (Array.isArray(v) && v.length === 0) ||
  (isObj(v) && Object.keys(v).length === 0);

const ISO_RE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;
const URL_RE = /^https?:\/\/\S+$/i;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ID_KEY_RE = /(^|_)(id|ids|key|uuid|token|hash|sha)$/i;
const ID_VAL_RE = /^(?=.*\d)[A-Za-z0-9][\w:.\-/#]{5,}$/;

/** A string that is itself JSON (some payloads are double-encoded). */
function parseJsonString(s: string): unknown {
  const t = s.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return undefined;
  try {
    const v: unknown = JSON.parse(t);
    return typeof v === "object" && v !== null ? v : undefined;
  } catch {
    return undefined;
  }
}

function None() {
  return <span className="text-ink-3">None</span>;
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <Tip content={done ? "Copied" : "Copy"}>
      <button
        type="button"
        aria-label="Copy"
        onClick={() => {
          void navigator.clipboard?.writeText(text).then(
            () => {
              setDone(true);
              setTimeout(() => setDone(false), 1200);
            },
            () => {},
          );
        }}
        className="inline-grid size-5 shrink-0 place-items-center rounded text-ink-3 hover:bg-sunken hover:text-ink"
      >
        {done ? <Check className="size-3" aria-hidden /> : <Copy className="size-3" aria-hidden />}
      </button>
    </Tip>
  );
}

function Short({ text, display, mono }: { text: string; display?: string; mono?: boolean }) {
  const shown = display ?? (text.length > 40 ? `${text.slice(0, 20)}…${text.slice(-10)}` : text);
  return (
    <span className="inline-flex max-w-full items-center gap-1 align-middle">
      <Tip content={shown !== text ? text : null}>
        <span
          tabIndex={shown !== text ? 0 : undefined}
          className={cn(shown !== text ? "truncate" : "break-all", mono && "font-mono text-[12.5px]")}
        >
          {shown}
        </span>
      </Tip>
      <CopyButton text={text} />
    </span>
  );
}

function LongText({ text }: { text: string }) {
  const [all, setAll] = useState(false);
  const max = 360;
  const long = text.length > max;
  return (
    <span className="block">
      <span className="block break-words whitespace-pre-line">
        {long && !all ? `${text.slice(0, max)}…` : text}
      </span>
      {long ? (
        <button
          type="button"
          onClick={() => setAll((x) => !x)}
          className="mt-0.5 text-[12.5px] font-medium text-accent hover:underline"
        >
          {all ? "Show less" : "Show more"}
        </button>
      ) : null}
    </span>
  );
}

function Scalar({ value, name }: { value: unknown; name?: string }) {
  if (isEmpty(value)) return <None />;
  if (typeof value === "boolean")
    return (
      <Badge tone={value ? "ok" : "idle"} className="px-1.5 py-0 text-[12px] leading-[18px]">
        {value ? "Yes" : "No"}
      </Badge>
    );
  if (typeof value === "number")
    return (
      <span className="tabular">
        {Number.isFinite(value) ? value.toLocaleString("en-US") : String(value)}
      </span>
    );
  if (typeof value !== "string") return <span>{String(value)}</span>;
  const s = value.trim();
  if (ISO_RE.test(s)) {
    const shown = s.length === 10 ? longDay(s) : fmtDateTime(s);
    if (shown) return <span className="tabular">{shown}</span>;
  }
  if (URL_RE.test(s)) {
    let label = s;
    try {
      const u = new URL(s);
      const path = u.pathname === "/" ? "" : u.pathname;
      label = u.host + (path.length > 24 ? `${path.slice(0, 22)}…` : path);
    } catch {
      /* keep raw */
    }
    return (
      <span className="inline-flex max-w-full items-center gap-1 align-middle">
        <a
          href={s}
          target="_blank"
          rel="noreferrer noopener"
          className="truncate text-accent hover:underline"
        >
          {label}
        </a>
        <CopyButton text={s} />
      </span>
    );
  }
  if (EMAIL_RE.test(s)) return <Short text={s} display={s} />;
  if (!/\s/.test(s) && s.length <= 80 && ((name && ID_KEY_RE.test(name)) || ID_VAL_RE.test(s)))
    return <Short text={s} mono />;
  const text = HTML_RE.test(s) ? stripHtml(s) : s;
  return text ? <LongText text={text} /> : <None />;
}

function Chips({ items }: { items: unknown[] }) {
  const strs = items.map((x) => (typeof x === "string" ? (HTML_RE.test(x) ? stripHtml(x) : x) : x));
  const short = strs.length <= 12 && strs.every((x) => typeof x !== "string" || x.length <= 40);
  if (!short)
    return (
      <ul className="list-disc space-y-0.5 pl-4">
        {strs.map((x, i) => (
          <li key={i} className="break-words">
            <Scalar value={x} />
          </li>
        ))}
      </ul>
    );
  return (
    <span className="flex flex-wrap gap-1">
      {strs.map((x, i) => (
        <span
          key={i}
          className="inline-flex items-center rounded-[6px] border border-line bg-sunken px-1.5 text-[12.5px] leading-5 text-ink-2"
        >
          {typeof x === "boolean" ? (x ? "Yes" : "No") : isEmpty(x) ? "None" : String(x)}
        </span>
      ))}
    </span>
  );
}

function ObjectList({ items, depth }: { items: unknown[]; depth: number }) {
  const objs = items.filter(isObj);
  const keys = [...new Set(objs.flatMap((o) => Object.keys(o)))];
  const flat =
    objs.length === items.length && keys.length <= 5 && objs.every((o) => Object.values(o).every(isScalar));
  if (flat)
    return (
      <div className="max-w-full overflow-x-auto rounded-ctl border border-line">
        <table className="w-full text-[13px]">
          <thead className="bg-sunken text-left text-ink-3">
            <tr>
              {keys.map((k) => (
                <th key={k} className="px-2 py-1 font-medium whitespace-nowrap">
                  {humanizeKey(k)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {objs.map((o, i) => (
              <tr key={i} className="border-t border-line align-top">
                {keys.map((k) => (
                  <td key={k} className="px-2 py-1">
                    <Scalar value={o[k]} name={k} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  return (
    <ol className="space-y-1.5">
      {items.map((x, i) => (
        <li key={i} className="rounded-ctl border border-line bg-surface px-2.5 py-1.5">
          {isObj(x) ? <Rows obj={x} depth={depth + 1} plain /> : <Value value={x} depth={depth + 1} />}
        </li>
      ))}
    </ol>
  );
}

function Value({ value, name, depth }: { value: unknown; name?: string; depth: number }): ReactNode {
  if (isEmpty(value)) return <None />;
  if (typeof value === "string") {
    const parsed = parseJsonString(value);
    if (parsed !== undefined) return <Value value={parsed} name={name} depth={depth} />;
  }
  if (isScalar(value)) return <Scalar value={value} name={name} />;
  if (Array.isArray(value))
    return value.every(isScalar) ? <Chips items={value} /> : <ObjectList items={value} depth={depth} />;
  const obj = value as Obj;
  if (depth >= 2) return <Disclosure obj={obj} depth={depth} />;
  return <Rows obj={obj} depth={depth} />;
}

function Disclosure({ obj, depth }: { obj: Obj; depth: number }) {
  const [open, setOpen] = useState(false);
  const n = Object.keys(obj).length;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-[12.5px] font-medium text-ink-3 hover:text-ink"
      >
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} aria-hidden />
        {open ? "Hide" : "Show"} {n} {n === 1 ? "field" : "fields"}
      </button>
      {open ? <Rows obj={obj} depth={depth} /> : null}
    </div>
  );
}

function Rows({ obj, depth, plain }: { obj: Obj; depth: number; plain?: boolean }) {
  return (
    <dl
      className={cn(
        "grid grid-cols-[minmax(110px,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm",
        depth > 0 && !plain && "mt-1 border-l border-line pl-3",
      )}
    >
      {Object.entries(obj).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{humanizeKey(k)}</dt>
          <dd className="min-w-0 break-words text-ink">
            <Value value={v} name={k} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Structured, human-readable rendering of a JSON payload. No raw JSON, no markup. */
export function DataView({ value, className }: { value: unknown; className?: string }) {
  return (
    <div className={cn("min-w-0 text-sm text-ink", className)}>
      <Value value={value} depth={0} />
    </div>
  );
}
