import * as D from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft, LogOut, Menu, X } from "lucide-react";
import { useState } from "react";
import { Link, useLocation } from "react-router";
import { request } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtTime } from "@/lib/format";
import { useEscalations, useMe } from "@/lib/queries";
import { Button } from "@/components/ui/button";
import {
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownMenu,
  DropdownSeparator,
  DropdownTrigger,
} from "@/components/ui/dropdown-menu";
import { Tip } from "@/components/ui/tooltip";
import { Brand, SidebarNav } from "./Sidebar";
import { usePageMetaValue } from "./page-meta";

function LiveIndicator() {
  const q = useEscalations();
  const ok = !q.isError && q.dataUpdatedAt > 0;
  const label = q.isError ? "Offline" : ok ? "Live" : "Connecting";
  const tip = q.isError
    ? "The last refresh failed. Retrying every 3 seconds."
    : ok
      ? `Data refreshes every 3 seconds while this tab is open. Last update ${fmtTime(new Date(q.dataUpdatedAt).toISOString())}.`
      : "Loading data";
  return (
    <Tip content={tip} side="bottom">
      <span
        tabIndex={0}
        role="status"
        className={cn(
          "inline-flex h-7 items-center gap-2 rounded-full border px-2.5 text-[12.5px] font-medium",
          q.isError ? "border-bad-line bg-bad-soft text-bad" : "border-line bg-surface text-ink-2",
        )}
      >
        <span className="relative flex size-2">
          {ok ? (
            <span className="absolute inset-0 animate-ping rounded-full bg-ok/50 motion-reduce:hidden" />
          ) : null}
          <span
            className={cn(
              "relative size-2 rounded-full",
              q.isError ? "bg-bad" : ok ? "bg-ok" : "bg-line-strong",
            )}
          />
        </span>
        {label}
      </span>
    </Tip>
  );
}

function UserMenu() {
  const me = useMe();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const name = me.data?.user || "admin";
  const signOut = async () => {
    setBusy(true);
    try {
      await request("/auth/logout", { method: "POST", body: {} });
    } catch {
      /* the cookie expires anyway */
    }
    qc.clear();
    window.location.assign("/ui/login");
  };
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <button
          type="button"
          className="inline-flex h-9 items-center gap-2 rounded-ctl pr-2 pl-1 text-sm text-ink-2 hover:bg-idle-soft"
          aria-label={`Account menu for ${name}`}
        >
          <span className="grid size-7 place-items-center rounded-full bg-accent-soft text-[13px] font-semibold text-accent-strong">
            {name.charAt(0).toUpperCase()}
          </span>
          <span className="hidden max-w-32 truncate sm:inline">{name}</span>
          <ChevronDown className="size-4 text-ink-3" aria-hidden />
        </button>
      </DropdownTrigger>
      <DropdownContent>
        <DropdownLabel>
          Signed in as <span className="font-medium text-ink">{name}</span>
        </DropdownLabel>
        <DropdownSeparator />
        <DropdownItem onSelect={signOut} disabled={busy}>
          <LogOut className="size-4" aria-hidden />
          {busy ? "Signing out" : "Sign out"}
        </DropdownItem>
      </DropdownContent>
    </DropdownMenu>
  );
}

function MobileNav() {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  return (
    <D.Root open={open} onOpenChange={setOpen}>
      <D.Trigger asChild>
        <Button variant="ghost" size="icon" aria-label="Open navigation" className="lg:hidden">
          <Menu className="size-5" aria-hidden />
        </Button>
      </D.Trigger>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-[rgb(16_24_40/0.24)]" />
        <D.Content
          key={loc.pathname}
          className="fixed inset-y-0 left-0 z-50 flex w-[280px] flex-col gap-6 border-r border-line bg-canvas px-3 py-4 shadow-pop focus:outline-none"
        >
          <D.Title className="sr-only">Navigation</D.Title>
          <D.Description className="sr-only">Pages of the onboarding console</D.Description>
          <div className="flex items-center justify-between">
            <Brand />
            <D.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close navigation">
                <X className="size-4" aria-hidden />
              </Button>
            </D.Close>
          </div>
          <SidebarNav onNavigate={() => setOpen(false)} />
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

export function Topbar() {
  const meta = usePageMetaValue();
  const crumbs = meta.crumbs ?? [];
  const back = crumbs.length ? crumbs[crumbs.length - 1] : null;
  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-line bg-canvas/90 px-4 backdrop-blur-md sm:px-6">
      <MobileNav />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {back?.to ? (
          <Tip content={`Back to ${back.label}`} side="bottom">
            <Link
              to={back.to}
              className="grid size-8 shrink-0 place-items-center rounded-ctl border border-line bg-surface text-ink-2 shadow-card hover:text-ink"
              aria-label={`Back to ${back.label}`}
            >
              <ChevronLeft className="size-4" aria-hidden />
            </Link>
          </Tip>
        ) : null}
        <div className="min-w-0">
          {crumbs.length ? (
            <nav aria-label="Breadcrumb" className="hidden text-[12.5px] text-ink-3 sm:block">
              {crumbs.map((c, i) => (
                <span key={i}>
                  {c.to ? (
                    <Link to={c.to} className="hover:text-ink">
                      {c.label}
                    </Link>
                  ) : (
                    c.label
                  )}
                  <span className="px-1.5" aria-hidden>
                    /
                  </span>
                </span>
              ))}
            </nav>
          ) : null}
          <h1 className="truncate text-[18px] leading-6 font-semibold tracking-[-0.015em] text-ink">
            {meta.title}
          </h1>
        </div>
      </div>
      <LiveIndicator />
      <UserMenu />
    </header>
  );
}
