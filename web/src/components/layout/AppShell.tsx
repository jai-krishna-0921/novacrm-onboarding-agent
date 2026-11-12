import { Outlet } from "react-router";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { PageMetaProvider } from "./page-meta";

export function AppShell() {
  return (
    <PageMetaProvider>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[70] focus:rounded-ctl focus:bg-surface focus:px-3 focus:py-2 focus:shadow-pop"
      >
        Skip to content
      </a>
      <div className="flex min-h-dvh">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <main
            id="main"
            tabIndex={-1}
            className="mx-auto w-full max-w-[1320px] flex-1 px-4 py-5 focus:outline-none sm:px-6 sm:py-6"
          >
            <Outlet />
          </main>
        </div>
      </div>
    </PageMetaProvider>
  );
}
