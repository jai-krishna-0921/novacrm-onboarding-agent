import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export function Table({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse text-left text-[14px]", className)}>{children}</table>
    </div>
  );
}

export function Th({ className, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "h-10 border-y border-line bg-sunken px-4 text-[12.5px] font-medium whitespace-nowrap text-ink-3 first:pl-5 last:pr-5",
        className,
      )}
      {...rest}
    />
  );
}

export function Td({ className, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-4 py-2.5 align-middle first:pl-5 last:pr-5", className)} {...rest} />;
}

export function Tr({
  className,
  onActivate,
  current,
  ...rest
}: HTMLAttributes<HTMLTableRowElement> & { onActivate?: () => void; current?: boolean }) {
  const clickable = !!onActivate;
  return (
    <tr
      className={cn(
        "border-b border-line last:border-b-0 transition-colors",
        clickable && "cursor-pointer hover:bg-sunken focus-visible:bg-accent-soft focus-visible:outline-none",
        current && "bg-accent-soft hover:bg-accent-soft",
        className,
      )}
      tabIndex={clickable ? 0 : undefined}
      onClick={onActivate}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onActivate?.();
              }
            }
          : undefined
      }
      aria-selected={current || undefined}
      {...rest}
    />
  );
}

export type Sort<K extends string> = { key: K; dir: "asc" | "desc" };

export function SortTh<K extends string>({
  label,
  k,
  sort,
  onSort,
  className,
}: {
  label: string;
  k: K;
  sort: Sort<K>;
  onSort: (s: Sort<K>) => void;
  className?: string;
}) {
  const active = sort.key === k;
  const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <Th className={className} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={() => onSort({ key: k, dir: active && sort.dir === "desc" ? "asc" : "desc" })}
        className={cn("inline-flex items-center gap-1 rounded hover:text-ink", active && "text-ink")}
      >
        {label}
        <Icon className="size-3.5" aria-hidden />
      </button>
    </Th>
  );
}

export function sortBy<T, K extends string>(
  rows: T[],
  sort: Sort<K>,
  get: (r: T, k: K) => string | number | null | undefined,
): T[] {
  const m = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = get(a, sort.key);
    const y = get(b, sort.key);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * m;
  });
}
