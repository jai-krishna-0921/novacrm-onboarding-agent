import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const TooltipProvider = T.Provider;

export function Tip({
  content,
  children,
  side = "top",
  className,
  bare,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
  /** No dark bubble: the content brings its own card (chart-style tooltips). */
  bare?: boolean;
}) {
  if (content == null || content === "") return <>{children}</>;
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          collisionPadding={12}
          className={cn(
            bare
              ? "z-50 data-[state=delayed-open]:animate-fade-up"
              : "z-50 max-w-[300px] rounded-ctl bg-ink px-2.5 py-1.5 text-[12.5px] leading-snug text-white shadow-pop data-[state=delayed-open]:animate-fade-up",
            className,
          )}
        >
          {content}
          {bare ? null : <T.Arrow className="fill-ink" width={10} height={5} />}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
