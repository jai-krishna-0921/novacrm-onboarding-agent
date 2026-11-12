import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

/** Side panel from the right. Full width on phones. */
export function Sheet({
  open,
  onOpenChange,
  title,
  eyebrow,
  description,
  children,
  footer,
  width = "max-w-[560px]",
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: ReactNode;
  eyebrow?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-[rgb(16_24_40/0.24)]" />
        <D.Content
          tabIndex={-1}
          onOpenAutoFocus={(e) => {
            // Focus the panel, not the close button: keyboard users start at the top of the content.
            e.preventDefault();
            (e.currentTarget as HTMLElement).focus();
          }}
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-line bg-surface shadow-pop transition-transform duration-300 ease-out-soft focus:outline-none data-[state=open]:animate-[sheet-in_0.32s_cubic-bezier(0.16,1,0.3,1)]",
            width,
          )}
        >
          <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
            <div className="min-w-0">
              {eyebrow ? <div className="mb-1.5">{eyebrow}</div> : null}
              <D.Title className="truncate text-[17px] font-semibold tracking-[-0.01em] text-ink">
                {title}
              </D.Title>
              {description ? (
                <D.Description className="mt-0.5 text-[13px] text-ink-3">{description}</D.Description>
              ) : (
                <D.Description className="sr-only">Details</D.Description>
              )}
            </div>
            <D.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close panel">
                <X className="size-4" aria-hidden />
              </Button>
            </D.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer ? (
            <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3.5">{footer}</div>
          ) : null}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
