import * as T from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Tabs = T.Root;

export function TabsList({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <T.List
      className={cn(
        "flex gap-1 overflow-x-auto border-b border-line px-3 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        className,
      )}
    >
      {children}
    </T.List>
  );
}

export function TabsTrigger({
  value,
  children,
  badge,
}: {
  value: string;
  children: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <T.Trigger
      value={value}
      className="relative -mb-px inline-flex h-11 shrink-0 items-center gap-2 border-b-2 border-transparent px-3 text-sm font-medium text-ink-3 transition-colors hover:text-ink data-[state=active]:border-accent data-[state=active]:text-ink"
    >
      {children}
      {badge}
    </T.Trigger>
  );
}

export function TabsContent({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <T.Content value={value} className={cn("p-5 focus-visible:outline-none", className)}>
      {children}
    </T.Content>
  );
}
