import * as S from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

export type Option = { value: string; label: string; hint?: string };

const ALL = "__all__";

/** Custom select. `allLabel` adds a first "All ..." option that maps to "". */
export function Select({
  value,
  onChange,
  options,
  label,
  allLabel,
  className,
  size = "md",
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: Option[];
  label: string;
  allLabel?: string;
  className?: string;
  size?: "sm" | "md";
  placeholder?: string;
}) {
  const v = value === "" && allLabel ? ALL : value;
  return (
    <S.Root value={v || undefined} onValueChange={(x) => onChange(x === ALL ? "" : x)}>
      <S.Trigger
        aria-label={label}
        className={cn(
          "inline-flex min-w-0 items-center justify-between gap-2 rounded-ctl border border-line-strong bg-surface px-3 text-left text-ink shadow-card transition-colors hover:bg-sunken focus-visible:border-accent data-[placeholder]:text-ink-3",
          size === "sm" ? "h-8 text-[13px]" : "h-9 text-sm",
          className,
        )}
      >
        <span className="truncate">
          <S.Value placeholder={placeholder ?? label} />
        </span>
        <S.Icon>
          <ChevronDown className="size-4 text-ink-3" aria-hidden />
        </S.Icon>
      </S.Trigger>
      <S.Portal>
        <S.Content
          position="popper"
          sideOffset={6}
          collisionPadding={12}
          className="z-50 max-h-[min(360px,var(--radix-select-content-available-height))] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-ctl border border-line bg-surface shadow-pop data-[state=open]:animate-fade-up"
        >
          <S.Viewport className="p-1">
            {allLabel ? <Item value={ALL} label={allLabel} /> : null}
            {options.map((o) => (
              <Item key={o.value} value={o.value} label={o.label} hint={o.hint} />
            ))}
          </S.Viewport>
        </S.Content>
      </S.Portal>
    </S.Root>
  );
}

function Item({ value, label, hint }: Option) {
  return (
    <S.Item
      value={value}
      className="relative flex cursor-default select-none items-center gap-2 rounded-[6px] py-1.5 pr-8 pl-2.5 text-sm text-ink-2 outline-none data-[highlighted]:bg-accent-soft data-[highlighted]:text-ink data-[state=checked]:font-medium data-[state=checked]:text-ink"
    >
      <span className="flex min-w-0 flex-col">
        <S.ItemText>{label}</S.ItemText>
        {hint ? <span className="text-[12px] text-ink-3">{hint}</span> : null}
      </span>
      <S.ItemIndicator className="absolute right-2.5">
        <Check className="size-4 text-accent" aria-hidden />
      </S.ItemIndicator>
    </S.Item>
  );
}
