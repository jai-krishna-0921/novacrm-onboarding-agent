import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center px-6 py-10 text-center", className)}>
      {Icon ? (
        <span className="mb-3 grid size-10 place-items-center rounded-full border border-line bg-sunken text-ink-3">
          <Icon className="size-5" strokeWidth={1.75} aria-hidden />
        </span>
      ) : null}
      <p className="font-semibold text-ink">{title}</p>
      {children ? <p className="mt-1 max-w-[52ch] text-sm text-ink-3">{children}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorBox({ error, className }: { error: unknown; className?: string }) {
  const msg = error instanceof Error ? error.message : String(error ?? "Something went wrong");
  return (
    <div
      role="alert"
      className={cn(
        "rounded-ctl border border-bad-line bg-bad-soft px-3.5 py-2.5 text-sm text-bad",
        className,
      )}
    >
      {msg}
    </div>
  );
}
