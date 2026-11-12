import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, enc, request } from "./api";
import * as S from "./schemas";
import { TERMINAL } from "./run-state";

export const LIVE_MS = 3000; // runs, run detail, escalations
export const METRICS_MS = 15000; // aggregates and logs

// refetchIntervalInBackground is false by default: polling pauses while the tab is hidden and a
// refetch runs as soon as it is visible again (refetchOnWindowFocus).

export const qk = {
  me: ["me"] as const,
  config: ["ui-config"] as const,
  runs: ["runs"] as const,
  run: (id: string) => ["run", id] as const,
  escalations: ["escalations", "open"] as const,
  overview: ["metrics", "overview"] as const,
  llm: (days: number) => ["metrics", "llm", days] as const,
  tools: ["metrics", "tools"] as const,
  llmCalls: (q: string) => ["llm-calls", q] as const,
  toolCalls: (q: string) => ["tool-calls", q] as const,
  toolCall: (id: number) => ["tool-call", id] as const,
  aes: ["aes"] as const,
  migSummary: ["migration", "summary"] as const,
  migData: (q: string) => ["migration", "data", q] as const,
  agents: ["agents", "status"] as const,
};

export const AGENTS_MS = 10000;

export const useMe = () =>
  useQuery({ queryKey: qk.me, queryFn: () => api("/auth/me", S.Me), staleTime: 5 * 60_000 });
export const useUiConfig = () =>
  useQuery({ queryKey: qk.config, queryFn: () => api("/ui/config", S.UiConfig), staleTime: Infinity });

export const useRuns = () =>
  useQuery({ queryKey: qk.runs, queryFn: () => api("/runs", S.Runs), refetchInterval: LIVE_MS });

export const useEscalations = () =>
  useQuery({
    queryKey: qk.escalations,
    queryFn: () => api("/escalations?status=open", S.Escalations),
    refetchInterval: LIVE_MS,
  });

const runQuery = (id: string) => ({
  queryKey: qk.run(id),
  queryFn: () => api(`/runs/${enc(id)}`, S.RunDetail),
});

export const useRun = (id: string | undefined) =>
  useQuery({
    ...runQuery(id ?? ""),
    enabled: !!id,
    refetchInterval: LIVE_MS,
    retry: (n, e) => (e as { status?: number }).status !== 404 && n < 2,
  });

/** Detail for the rows on screen (names, steppers). Finished runs are not polled. */
export function useRunDetails(runs: S.Run[]) {
  return useQueries({
    queries: runs.map((r) => ({
      ...runQuery(r.id),
      staleTime: 2000,
      refetchInterval: TERMINAL.has(String(r.status ?? "").toUpperCase()) ? (false as const) : LIVE_MS,
    })),
  });
}

export const useOverview = () =>
  useQuery({
    queryKey: qk.overview,
    queryFn: () => api("/metrics/overview", S.Overview),
    refetchInterval: METRICS_MS,
  });
export const useLlmMetrics = (days = 14) =>
  useQuery({
    queryKey: qk.llm(days),
    queryFn: () => api(`/metrics/llm?days=${days}`, S.LlmMetrics),
    refetchInterval: METRICS_MS,
  });
export const useToolMetrics = () =>
  useQuery({
    queryKey: qk.tools,
    queryFn: () => api("/metrics/tools", S.ToolMetrics),
    refetchInterval: METRICS_MS,
  });

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params))
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  return q.toString();
}

export const useLlmCalls = (q: string) =>
  useQuery({
    queryKey: qk.llmCalls(q),
    queryFn: () => api(`/llm-calls?${q}`, S.LlmCallsPage),
    refetchInterval: METRICS_MS,
    placeholderData: keepPreviousData,
  });
export const useToolCalls = (q: string, live = false) =>
  useQuery({
    queryKey: qk.toolCalls(q),
    queryFn: () => api(`/tool-calls?${q}`, S.ToolCallsPage),
    refetchInterval: live ? LIVE_MS : METRICS_MS,
    placeholderData: keepPreviousData,
  });
export const useToolCall = (id: number | null) =>
  useQuery({
    queryKey: qk.toolCall(id ?? 0),
    queryFn: () => api(`/tool-calls/${id}`, S.ToolCallRow),
    enabled: id != null,
    staleTime: Infinity,
  });

export const useAes = () =>
  useQuery({ queryKey: qk.aes, queryFn: () => api("/aes", S.Aes), refetchInterval: METRICS_MS });

export const useMigrationSummary = () =>
  useQuery({
    queryKey: qk.migSummary,
    queryFn: () => api("/migration/summary", S.MigrationSummary),
    refetchInterval: METRICS_MS,
  });
export const useMigrationData = (q: string) =>
  useQuery({
    queryKey: qk.migData(q),
    queryFn: () => api(`/migration/data?${q}`, S.MigrationData),
    refetchInterval: METRICS_MS,
    placeholderData: keepPreviousData,
  });

/** POST/PATCH with JSON; invalidates the given keys afterwards. */
export function useAction<TBody, TOut = unknown>(
  fn: (body: TBody) => Promise<TOut>,
  invalidate: readonly (readonly unknown[])[],
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => Promise.all(invalidate.map((k) => qc.invalidateQueries({ queryKey: k }))),
  });
}

export const post = (path: string, body?: unknown) => request(path, { method: "POST", body: body ?? {} });
export const patch = (path: string, body: unknown) => request(path, { method: "PATCH", body });

export const useAgentsStatus = () =>
  useQuery({
    queryKey: qk.agents,
    queryFn: () => api("/agents/status", S.AgentsStatus),
    refetchInterval: AGENTS_MS,
  });
