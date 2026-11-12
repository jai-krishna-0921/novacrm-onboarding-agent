import * as D from "@radix-ui/react-dropdown-menu";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const DropdownMenu = D.Root;
export const DropdownTrigger = D.Trigger;

export function DropdownContent({
  children,
  className,
  align = "end",
}: {
  children: ReactNode;
  className?: string;
  align?: "start" | "end";
}) {
  return (
    <D.Portal>
      <D.Content
        align={align}
        sideOffset={6}
        collisionPadding={12}
        className={cn(
          "z-50 min-w-52 rounded-ctl border border-line bg-surface p-1 shadow-pop data-[state=open]:animate-fade-up",
          className,
        )}
      >
        {children}
      </D.Content>
    </D.Portal>
  );
}

export function DropdownItem({ className, ...rest }: D.DropdownMenuItemProps) {
  return (
    <D.Item
      className={cn(
        "flex cursor-default select-none items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-sm text-ink-2 outline-none data-[highlighted]:bg-accent-soft data-[highlighted]:text-ink data-[disabled]:opacity-50",
        className,
      )}
      {...rest}
    />
  );
}

export function DropdownLabel({ children }: { children: ReactNode }) {
  return <D.Label className="px-2.5 pt-1.5 pb-2 text-[12.5px] text-ink-3">{children}</D.Label>;
}

export const DropdownSeparator = () => <D.Separator className="my-1 h-px bg-line" />;
