import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

const overlay = "fixed inset-0 z-40 bg-[rgb(16_24_40/0.32)]";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className={overlay} />
        <D.Content
          className={cn(
            "fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] max-w-[480px] -translate-x-1/2 -translate-y-1/2 flex-col rounded-card border border-line bg-surface shadow-pop focus:outline-none data-[state=open]:animate-[dialog-in_0.22s_cubic-bezier(0.16,1,0.3,1)]",
            className,
          )}
        >
          <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-2">
            <div>
              <D.Title className="text-base font-semibold text-ink">{title}</D.Title>
              {description ? (
                <D.Description className="mt-1 text-sm text-ink-3">{description}</D.Description>
              ) : (
                <D.Description className="sr-only">{title}</D.Description>
              )}
            </div>
            <D.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close">
                <X className="size-4" aria-hidden />
              </Button>
            </D.Close>
          </div>
          {children ? <div className="overflow-y-auto px-5 py-3">{children}</div> : null}
          {footer ? (
            <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3.5">{footer}</div>
          ) : null}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

/** Confirm before a destructive or state-changing action. `onConfirm` may be async; the dialog stays open on error. */
export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  tone = "primary",
  onConfirm,
  children,
  disabled,
}: {
  trigger: (open: () => void) => ReactNode;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  tone?: "primary" | "danger";
  onConfirm: () => Promise<unknown> | unknown;
  children?: ReactNode;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      await onConfirm();
      setOpen(false);
    } catch {
      /* the caller shows the error */
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {trigger(() => setOpen(true))}
      <Dialog
        open={open}
        onOpenChange={(o) => !busy && setOpen(o)}
        title={title}
        description={description}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={busy}>
              Keep as is
            </Button>
            <Button
              variant={tone === "danger" ? "danger" : "primary"}
              onClick={go}
              loading={busy}
              disabled={disabled}
            >
              {confirmLabel}
            </Button>
          </>
        }
      >
        {children}
      </Dialog>
    </>
  );
}
