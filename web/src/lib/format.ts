// Display helpers. Everything returns plain strings; React renders them as text.

export const cap = (s: unknown) => {
  const t = String(s ?? "");
  return t.charAt(0).toUpperCase() + t.slice(1);
};
export const human = (s: unknown) =>
  cap(
    String(s ?? "")
      .replace(/[_:]+/g, " ")
      .trim(),
  );
export const str = (v: unknown) => (v == null ? "" : typeof v === "string" ? v : JSON.stringify(v));

function toDate(iso: string | number | null | undefined): Date | null {
  if (iso == null || iso === "") return null;
  const d = typeof iso === "number" ? new Date(iso < 1e12 ? iso * 1000 : iso) : new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

export function fmtTime(iso: string | number | null | undefined, withDate = false): string {
  const d = toDate(iso);
  if (!d) return iso == null ? "" : String(iso);
  const t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return withDate ? `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${t}` : t;
}

export function fmtDateTime(iso: string | null | undefined): string {
  const d = toDate(iso);
  if (!d) return "";
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  const d = toDate(iso);
  if (!d) return "";
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const fmtInt = (n: number | null | undefined) => Number(n ?? 0).toLocaleString("en-US");

export function fmtUsd(v: number | null | undefined, digits = 4): string {
  return `$${Number(v ?? 0).toFixed(digits)}`;
}

export function fmtMs(v: number | null | undefined): string {
  if (v == null) return "-";
  return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(v < 10000 ? 2 : 1)} s`;
}

export function fmtDur(s: number | null | undefined): string {
  if (s == null) return "-";
  const r = Math.round(s);
  if (r < 60) return `${r}s`;
  if (r < 3600) return `${Math.floor(r / 60)}m ${r % 60}s`;
  return `${Math.floor(r / 3600)}h ${Math.floor((r % 3600) / 60)}m`;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  return `${(Number(v ?? 0) * 100).toFixed(digits)}%`;
}

/** "2026-11-03" -> "3 Nov" (local calendar day, no timezone shift). */
export function shortDay(day: string): string {
  const d = new Date(`${day}T00:00:00`);
  return isNaN(d.getTime()) ? day : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function longDay(day: string): string {
  const d = new Date(`${day}T00:00:00`);
  return isNaN(d.getTime())
    ? day
    : d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

/** The last n UTC days as YYYY-MM-DD, oldest first. */
export function lastDays(n: number, today = new Date()): string[] {
  const out: string[] = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

export function pretty(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/** "in 4m 12s" / "due now". Pure for testing. */
export function countdown(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const s = Math.round((t - now) / 1000);
  if (s <= 0) return "due now";
  if (s < 60) return `in ${s}s`;
  if (s < 3600) return `in ${Math.floor(s / 60)}m ${s % 60}s`;
  return `in ${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
