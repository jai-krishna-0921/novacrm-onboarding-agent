import * as T from "@radix-ui/react-toast";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

type Tone = "ok" | "bad" | "info";
type Item = { id: number; title: string; body?: string; tone: Tone };
type Push = (t: { title: string; body?: string; tone?: Tone }) => void;

const Ctx = createContext<Push>(() => {});
export const useToast = () => useContext(Ctx);

let seq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const push = useCallback<Push>((t) => {
    setItems((xs) => [...xs.slice(-3), { id: ++seq, tone: t.tone ?? "ok", title: t.title, body: t.body }]);
  }, []);
  const drop = (id: number) => setItems((xs) => xs.filter((x) => x.id !== id));
  return (
    <Ctx.Provider value={push}>
      <T.Provider swipeDirection="right" duration={5000}>
        {children}
        {items.map((t) => (
          <T.Root
            key={t.id}
            onOpenChange={(o) => !o && drop(t.id)}
            type={t.tone === "bad" ? "foreground" : "background"}
            className="grid grid-cols-[auto_1fr_auto] items-start gap-3 rounded-card border border-line bg-surface p-3.5 shadow-pop data-[state=open]:animate-[sheet-in_0.3s_cubic-bezier(0.16,1,0.3,1)] data-[swipe=move]:translate-x-[var(--radix-toast-swipe-move-x)]"
          >
            <ToneIcon tone={t.tone} />
            <div className="min-w-0">
              <T.Title className="text-sm font-semibold text-ink">{t.title}</T.Title>
              {t.body ? (
                <T.Description className="mt-0.5 text-[13px] break-words text-ink-3">{t.body}</T.Description>
              ) : null}
            </div>
            <T.Close
              aria-label="Dismiss"
              className="rounded p-0.5 text-ink-3 hover:bg-idle-soft hover:text-ink"
            >
              <X className="size-4" aria-hidden />
            </T.Close>
          </T.Root>
        ))}
        <T.Viewport className="fixed right-4 bottom-4 z-[60] flex w-[min(380px,calc(100vw-32px))] flex-col gap-2 outline-none" />
      </T.Provider>
    </Ctx.Provider>
  );
}

function ToneIcon({ tone }: { tone: Tone }) {
  const cls = "mt-0.5 size-[18px]";
  if (tone === "ok") return <CheckCircle2 className={cn(cls, "text-ok")} aria-hidden />;
  if (tone === "bad") return <AlertTriangle className={cn(cls, "text-bad")} aria-hidden />;
  return <Info className={cn(cls, "text-accent")} aria-hidden />;
}
