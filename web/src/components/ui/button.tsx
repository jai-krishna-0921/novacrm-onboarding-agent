import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost";
type Size = "sm" | "md" | "lg" | "icon";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-accent text-white hover:bg-accent-strong shadow-[inset_0_1px_0_rgb(255_255_255/0.15)] border border-accent-strong/40",
  secondary: "bg-surface text-ink-2 border border-line-strong hover:bg-sunken hover:text-ink shadow-card",
  ghost: "text-ink-2 hover:bg-idle-soft hover:text-ink",
  danger: "bg-bad text-white hover:bg-[#9c2a23] border border-[#9c2a23]",
  "danger-ghost": "text-bad hover:bg-bad-soft border border-bad-line bg-surface",
};
const SIZE: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  lg: "h-10 px-4 text-[15px] gap-2",
  icon: "h-8 w-8 justify-center",
};

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded-ctl font-medium transition-[background-color,color,transform,box-shadow] duration-150 ease-out-soft active:translate-y-px disabled:pointer-events-none disabled:opacity-55",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
      {children}
    </button>
  );
});
