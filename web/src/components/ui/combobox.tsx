import * as P from "@radix-ui/react-popover";
import { Command } from "cmdk";
import { Check, ChevronsUpDown, Search } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import type { Option } from "./select";

/** Select with a search box, for long lists (runs, tools). */
export function Combobox({
  value,
  onChange,
  options,
  label,
  placeholder,
  allLabel,
  empty = "No matches",
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  options: Option[];
  label: string;
  placeholder?: string;
  allLabel?: string;
  empty?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find((o) => o.value === value);
  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
  };
  return (
    <P.Root open={open} onOpenChange={setOpen}>
      <P.Trigger
        aria-label={label}
        role="combobox"
        aria-expanded={open}
        className={cn(
          "inline-flex h-9 min-w-0 items-center justify-between gap-2 rounded-ctl border border-line-strong bg-surface px-3 text-left text-sm shadow-card transition-colors hover:bg-sunken",
          className,
        )}
      >
        <span className={cn("truncate", !current && !(allLabel && value === "") && "text-ink-3")}>
          {current?.label ?? (value === "" && allLabel ? allLabel : (placeholder ?? label))}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-ink-3" aria-hidden />
      </P.Trigger>
      <P.Portal>
        <P.Content
          align="start"
          sideOffset={6}
          collisionPadding={12}
          className="z-50 w-[min(420px,calc(100vw-32px))] min-w-[var(--radix-popover-trigger-width)] rounded-ctl border border-line bg-surface shadow-pop data-[state=open]:animate-fade-up"
        >
          <Command label={label} className="flex flex-col">
            <div className="flex items-center gap-2 border-b border-line px-3">
              <Search className="size-4 text-ink-3" aria-hidden />
              <Command.Input
                autoFocus
                placeholder="Search"
                className="h-10 w-full bg-transparent text-sm outline-none placeholder:text-ink-3"
              />
            </div>
            <Command.List className="max-h-72 overflow-y-auto p-1">
              <Command.Empty className="px-3 py-6 text-center text-sm text-ink-3">{empty}</Command.Empty>
              {allLabel ? <Row value="" label={allLabel} selected={value === ""} onPick={pick} /> : null}
              {options.map((o) => (
                <Row key={o.value} {...o} selected={o.value === value} onPick={pick} />
              ))}
            </Command.List>
          </Command>
        </P.Content>
      </P.Portal>
    </P.Root>
  );
}

function Row({
  value,
  label,
  hint,
  selected,
  onPick,
}: Option & { selected: boolean; onPick: (v: string) => void }) {
  return (
    <Command.Item
      value={`${label} ${hint ?? ""} ${value}`}
      onSelect={() => onPick(value)}
      className="flex cursor-default items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-sm text-ink-2 data-[selected=true]:bg-accent-soft data-[selected=true]:text-ink"
    >
      <Check className={cn("size-4 shrink-0 text-accent", !selected && "invisible")} aria-hidden />
      <span className="flex min-w-0 flex-col">
        <span className="truncate">{label}</span>
        {hint ? <span className="truncate text-[12px] text-ink-3">{hint}</span> : null}
      </span>
    </Command.Item>
  );
}
