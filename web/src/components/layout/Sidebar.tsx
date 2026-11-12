import {
  Activity,
  AlertTriangle,
  Bot,
  Database,
  LayoutGrid,
  ListChecks,
  Users,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { NavLink } from "react-router";
import { cn } from "@/lib/cn";
import { useEscalations } from "@/lib/queries";
import { Tip } from "@/components/ui/tooltip";

type Item = { to: string; label: string; icon: LucideIcon; count?: boolean };
const NAV: { group: string; items: Item[] }[] = [
  {
    group: "Operations",
    items: [
      { to: "/agents", label: "Agents", icon: Activity },
      { to: "/overview", label: "Overview", icon: LayoutGrid },
      { to: "/runs", label: "Runs", icon: ListChecks },
      { to: "/escalations", label: "Needs a human", icon: AlertTriangle, count: true },
    ],
  },
  { group: "Directory", items: [{ to: "/aes", label: "Account Executives", icon: Users }] },
  { group: "Data", items: [{ to: "/migration", label: "Data migration", icon: Database }] },
  {
    group: "Observability",
    items: [
      { to: "/ai", label: "AI usage", icon: Bot },
      { to: "/tools", label: "Tool calls", icon: Wrench },
    ],
  },
];

export function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <span
        className="grid size-8 place-items-center rounded-[9px] bg-accent shadow-[inset_0_1px_0_rgb(255_255_255/0.2)]"
        aria-hidden
      >
        <span className="flex gap-[3px]">
          <span className="size-2 rounded-full bg-white" />
          <span className="size-2 rounded-full bg-white/55" />
        </span>
      </span>
      <span className="flex flex-col leading-tight">
        <span className="text-[14.5px] font-semibold text-ink">NovaCRM</span>
        <span className="text-[12px] text-ink-3">Onboarding</span>
      </span>
    </div>
  );
}

export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const escs = useEscalations();
  const open = escs.data?.length ?? 0;
  return (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {NAV.map((g) => (
        <div key={g.group}>
          <div className="px-3 pb-1.5 text-[12px] font-medium text-ink-3">{g.group}</div>
          <ul className="flex flex-col gap-0.5">
            {g.items.map((it) => (
              <li key={it.to}>
                <NavLink
                  to={it.to}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cn(
                      "group flex h-9 items-center gap-2.5 rounded-ctl px-3 text-[14px] font-medium text-ink-2 transition-colors hover:bg-idle-soft hover:text-ink",
                      isActive && "bg-surface text-ink shadow-card ring-1 ring-line",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <it.icon
                        className={cn(
                          "size-[17px] text-ink-3 group-hover:text-ink-2",
                          isActive && "text-accent",
                        )}
                        strokeWidth={1.85}
                        aria-hidden
                      />
                      <span className="flex-1 truncate">{it.label}</span>
                      {it.count && open > 0 ? (
                        <Tip
                          content={`${open} open escalation${open === 1 ? "" : "s"} waiting for a decision`}
                          side="right"
                        >
                          <span className="tabular rounded-full bg-warn px-1.5 text-[12px] leading-5 font-semibold text-white">
                            {open}
                          </span>
                        </Tip>
                      ) : null}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-dvh w-[248px] shrink-0 flex-col gap-6 border-r border-line bg-canvas px-3 py-4 lg:flex">
      <Brand />
      <SidebarNav />
      <p className="mt-auto px-3 text-[12px] leading-relaxed text-ink-3">
        CS owns the process. The agents run it and send anything unclear to a person.
      </p>
    </aside>
  );
}
