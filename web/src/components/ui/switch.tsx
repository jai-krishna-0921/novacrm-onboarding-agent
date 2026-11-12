import * as S from "@radix-ui/react-switch";
import { cn } from "@/lib/cn";

export function Switch({ className, ...rest }: S.SwitchProps) {
  return (
    <S.Root
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent bg-line-strong transition-colors duration-200 ease-out-soft data-[state=checked]:bg-accent disabled:opacity-50",
        className,
      )}
      {...rest}
    >
      <S.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow-[0_1px_2px_rgb(16_24_40/0.2)] transition-transform duration-200 ease-out-soft data-[state=checked]:translate-x-[18px]" />
    </S.Root>
  );
}
