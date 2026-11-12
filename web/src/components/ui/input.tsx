import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/cn";

const base =
  "w-full rounded-ctl border border-line-strong bg-surface px-3 text-[14.5px] text-ink placeholder:text-ink-3 shadow-[inset_0_1px_1px_rgb(16_24_40/0.04)] transition-[border-color,box-shadow] duration-150 focus:border-accent focus:outline-none focus:ring-3 focus:ring-accent/15 aria-[invalid=true]:border-bad aria-[invalid=true]:ring-bad/15 disabled:bg-sunken disabled:text-ink-3";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cn(base, "h-9", className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cn(base, "min-h-20 py-2", className)} {...rest} />;
  },
);

/** Label above, control, then hint or error below. The control gets id and aria wiring. */
export function Field({
  label,
  hint,
  error,
  hintTone,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  hintTone?: "ok" | "muted";
  children: (p: { id: string; "aria-invalid"?: boolean; "aria-describedby"?: string }) => ReactNode;
  className?: string;
}) {
  const id = useId();
  const msgId = `${id}-msg`;
  const msg = error || hint;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[13px] font-medium text-ink-2">
        {label}
      </label>
      {children({
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": msg ? msgId : undefined,
      })}
      {msg ? (
        <p
          id={msgId}
          className={cn("text-[12.5px]", error ? "text-bad" : hintTone === "ok" ? "text-ok" : "text-ink-3")}
        >
          {msg}
        </p>
      ) : null}
    </div>
  );
}
