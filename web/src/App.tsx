import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FileQuestion } from "lucide-react";
import { useEffect } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from "react-router";
import { ApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "@/components/layout/AppShell";
import { usePageMeta } from "@/components/layout/page-meta";
import { AccountExecutivesPage } from "@/pages/AccountExecutives";
import { AgentsPage } from "@/pages/Agents";
import { AiUsagePage } from "@/pages/AiUsage";
import { EscalationsPage } from "@/pages/Escalations";
import { LoginPage } from "@/pages/Login";
import { MigrationPage } from "@/pages/Migration";
import { OverviewPage } from "@/pages/Overview";
import { RunDetailPage } from "@/pages/RunDetail";
import { RunsPage } from "@/pages/Runs";
import { ToolCallsPage } from "@/pages/ToolCalls";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (n, e) => !(e instanceof ApiError && [401, 403, 404, 422].includes(e.status)) && n < 2,
        refetchOnWindowFocus: true,
        staleTime: 1000,
      },
    },
  });
}

/** Old dashboard links used hashes (/ui#/runs/abc). Send them to the matching path. */
function LegacyHash() {
  const navigate = useNavigate();
  useEffect(() => {
    const h = window.location.hash;
    if (h.startsWith("#/")) navigate(h.slice(1), { replace: true });
  }, [navigate]);
  return null;
}

function NotFound() {
  usePageMeta("Page not found");
  return (
    <Card>
      <EmptyState
        icon={FileQuestion}
        title="Page not found"
        action={
          <Link to="/overview" className="text-sm font-medium text-accent hover:underline">
            Go to the overview
          </Link>
        }
      >
        The link may be wrong or the page has moved.
      </EmptyState>
    </Card>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:id" element={<RunDetailPage />} />
        <Route path="/escalations" element={<EscalationsPage />} />
        <Route path="/escalations/:runId" element={<EscalationsPage />} />
        <Route path="/aes" element={<AccountExecutivesPage />} />
        <Route path="/migration" element={<MigrationPage />} />
        <Route path="/ai" element={<AiUsagePage />} />
        <Route path="/tools" element={<ToolCallsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function App({ client }: { client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={250} skipDelayDuration={150}>
        <ToastProvider>
          <BrowserRouter basename="/ui">
            <LegacyHash />
            <AppRoutes />
          </BrowserRouter>
        </ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
