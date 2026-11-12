import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { getPref, setPref } from "@/lib/prefs";
import { Button } from "./button";
import { Select } from "./select";

export const PAGE_SIZES = [5, 10, 25, 50] as const;
const DEFAULT_SIZE = 10;

export function readPageSize(key: string): number {
  const n = Number(getPref(`dash.pp.${key}`, String(DEFAULT_SIZE)));
  return (PAGE_SIZES as readonly number[]).includes(n) ? n : DEFAULT_SIZE;
}

/** Page index + rows per page, remembered per list in localStorage. */
export function usePaging(key: string) {
  const [size, setSizeState] = useState(() => readPageSize(key));
  const [page, setPage] = useState(0);
  const setSize = (n: number) => {
    setSizeState(n);
    setPage(0);
    setPref(`dash.pp.${key}`, String(n));
  };
  return { page, setPage, size, setSize, offset: page * size };
}

export function clampPage(page: number, total: number, size: number): number {
  const pages = Math.max(1, Math.ceil(total / size));
  return Math.max(0, Math.min(page, pages - 1));
}

export function Pagination({
  total,
  page,
  size,
  onPage,
  onSize,
  noun = "rows",
}: {
  total: number;
  page: number;
  size: number;
  onPage: (p: number) => void;
  onSize: (n: number) => void;
  noun?: string;
}) {
  if (total === 0) return null;
  const pages = Math.max(1, Math.ceil(total / size));
  const p = clampPage(page, total, size);
  const from = p * size + 1;
  const to = Math.min(total, (p + 1) * size);
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-line px-4 py-2.5 text-[13px] text-ink-3">
      <div className="flex items-center gap-2">
        <span className="hidden sm:inline">Rows per page</span>
        <Select
          size="sm"
          label="Rows per page"
          value={String(size)}
          onChange={(v) => onSize(Number(v))}
          options={PAGE_SIZES.map((n) => ({ value: String(n), label: String(n) }))}
          className="w-[72px]"
        />
      </div>
      <span className="tabular" aria-live="polite">
        {from}-{to} of {total.toLocaleString("en-US")} {noun}
      </span>
      <div className="flex items-center gap-1">
        <Button
          size="icon"
          variant="ghost"
          aria-label="Previous page"
          disabled={p <= 0}
          onClick={() => onPage(p - 1)}
        >
          <ChevronLeft className="size-4" aria-hidden />
        </Button>
        <span className="tabular min-w-14 text-center">
          {p + 1} / {pages}
        </span>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Next page"
          disabled={p >= pages - 1}
          onClick={() => onPage(p + 1)}
        >
          <ChevronRight className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
