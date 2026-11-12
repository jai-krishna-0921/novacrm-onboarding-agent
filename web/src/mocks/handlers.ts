import { http, HttpResponse, delay } from "msw";
import { createDb, type Db } from "./data";

// MSW handlers implementing the backend contract over the in-memory db. Used by `npm run dev:mock` and tests.

type J = Record<string, unknown>;
const json = (body: unknown, status = 200) => HttpResponse.json(body as J, { status });
const detail = (msg: unknown, status: number) => json({ detail: msg }, status);
const nowIso = () => new Date().toISOString();

function percentile(xs: number[], p: number): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.ceil(p * s.length) - 1)];
}
function group(rows: J[]) {
  const lat = rows.map((r) => r.latency_ms as number).filter((x) => x != null);
  return {
    calls: rows.length,
    input_tokens: rows.reduce((a, r) => a + ((r.input_tokens as number) || 0), 0),
    output_tokens: rows.reduce((a, r) => a + ((r.output_tokens as number) || 0), 0),
    cost_usd: +rows.reduce((a, r) => a + ((r.cost_usd as number) || 0), 0).toFixed(6),
    avg_latency_ms: lat.length ? Math.round(lat.reduce((a, b) => a + b, 0) / lat.length) : null,
    p50_latency_ms: percentile(lat, 0.5),
    p95_latency_ms: percentile(lat, 0.95),
  };
}
function groupsBy(rows: J[], keys: string[]) {
  const m = new Map<string, J[]>();
  for (const r of rows) {
    const k = JSON.stringify(keys.map((x) => r[x]));
    m.set(k, [...(m.get(k) ?? []), r]);
  }
  return [...m.entries()]
    .map(([k, v]) => ({ ...Object.fromEntries(keys.map((x, i) => [x, JSON.parse(k)[i]])), ...group(v) }))
    .sort((a, b) => b.calls - a.calls);
}
const EDGES = [1000, 2000, 5000, 10000, 30000];
const bucket = (ms: number) =>
  EDGES.findIndex((e) => ms < e) === -1 ? EDGES.length : EDGES.findIndex((e) => ms < e);

function trackingView(db: Db, id: string) {
  const t = db.tracking[id];
  if (!t) return null;
  const tasks = t.tasks as { phase: string; completed: boolean }[];
  const names = [...new Set(tasks.map((x) => x.phase))];
  const phases = names.map((n) => ({
    name: n,
    done: tasks.filter((x) => x.phase === n && x.completed).length,
    total: tasks.filter((x) => x.phase === n).length,
  }));
  const done = tasks.filter((x) => x.completed).length;
  return {
    current_phase: phases.find((p) => p.done < p.total)?.name ?? null,
    percent_done: tasks.length ? Math.round((100 * done) / tasks.length) : 0,
    tasks_done: done,
    tasks_total: tasks.length,
    phases,
    last_synced_at: t.synced_at,
    handoff_summary: t.handoff_summary,
    handed_off_at: t.handed_off_at,
  };
}

function pending(db: Db, run: J) {
  const st = run.status;
  const esc = db.escalations.find(
    (e) => e.run_id === run.id && e.status === "open" && e.kind !== "migration",
  );
  if (st === "ESCALATED" && esc) return { type: "escalation", kind: esc.kind, reason: esc.reason };
  if (st === "AWAITING_CALL") return { type: "call", provider_call_id: `call_${run.id}_1`, attempt: 1 };
  if (st === "AWAITING_RETRY") return { type: "wait", until: Date.now() / 1000 + 240, attempt: 1 };
  return null;
}

function page(rows: J[], url: URL, fields: string[], filters: string[], db: Db) {
  const limit = Number(url.searchParams.get("limit") ?? 25);
  const offset = Number(url.searchParams.get("offset") ?? 0);
  const match = rows
    .filter((r) => filters.every((f) => !url.searchParams.get(f) || r[f] === url.searchParams.get(f)))
    .sort((a, b) => (b.id as number) - (a.id as number));
  const name = (id: unknown) => db.runs.find((r) => r.id === id)?.customer_name ?? null;
  return {
    total: match.length,
    limit,
    offset,
    rows: match.slice(offset, offset + limit).map((r) => {
      const o: J = { customer_name: name(r.run_id) };
      for (const f of fields) o[f] = r[f];
      return o;
    }),
  };
}

const mask = (p: unknown) =>
  typeof p === "string" && p.length > 4 ? `${"*".repeat(p.length - 4)}${p.slice(-4)}` : p;
const aeOut = (a: J) => ({
  email: a.email,
  name: a.name,
  phone_masked: mask(a.phone),
  active: a.active,
  updated_at: a.updated_at,
});

function migrationProblems(db: Db) {
  const { source, dest } = db.migration;
  const destIds = new Set(dest.map((r) => r.id));
  const counts = new Map<unknown, number>();
  for (const r of dest) counts.set(r.id, (counts.get(r.id) ?? 0) + 1);
  return {
    duplicates: [...counts.entries()].filter(([, n]) => n > 1).map(([id]) => id),
    missing_required: [
      ...new Set(
        dest.filter((r) => !String(r.name ?? "").trim() || !String(r.email ?? "").trim()).map((r) => r.id),
      ),
    ],
    missing_in_dest: source.filter((r) => !destIds.has(r.id)).map((r) => r.id),
    not_in_source: [
      ...new Set(dest.filter((r) => r.id == null || !source.some((x) => x.id === r.id)).map((r) => r.id)),
    ],
  };
}
/** Same shape as compare_columns() in services/migration_check.py. */
function migrationColumns(db: Db) {
  const { sourceColumns: s, destColumns: d } = db.migration;
  return {
    source: s,
    dest: d,
    missing: s.filter((c) => !d.includes(c)),
    extra: d.filter((c) => !s.includes(c)),
  };
}
function summary(db: Db) {
  return {
    source_count: db.migration.source.length,
    dest_count: db.migration.dest.length,
    problems: migrationProblems(db),
    columns: migrationColumns(db),
    last_check: db.migration.lastCheck,
  };
}
/** Mirrors repair() in services/migration_demo.py: add missing columns (values from the source), drop duplicate
 * ids, fill blank name/email from the source, add missing source rows; extra destination columns are kept. */
function repairDest(db: Db) {
  const { source } = db.migration;
  const cols = migrationColumns(db);
  const byId = new Map(source.map((r) => [r.id, r]));
  const out = new Map<unknown, Record<string, unknown>>();
  for (const r of db.migration.dest) {
    if (out.has(r.id)) continue;
    const src = byId.get(r.id) ?? {};
    const row: Record<string, unknown> = { ...r };
    for (const c of cols.missing) row[c] = src[c] ?? null;
    for (const f of ["name", "email"])
      if (!String(row[f] ?? "").trim() && String(src[f] ?? "").trim()) row[f] = src[f];
    out.set(r.id, row);
  }
  const columns = [...cols.dest, ...cols.missing];
  for (const [id, r] of byId)
    if (!out.has(id)) out.set(id, Object.fromEntries(columns.map((c) => [c, r[c] ?? null])));
  db.migration.dest = [...out.values()].sort((a, b) => Number(a.id) - Number(b.id));
  db.migration.destColumns = columns;
}
function runCheck(db: Db, runId: string) {
  const p = migrationProblems(db);
  const cols = migrationColumns(db);
  const { source, dest } = db.migration;
  const batch = Math.random().toString(16).slice(2, 14);
  const ts = nowIso();
  const results = [
    {
      name: "record_count",
      passed: source.length === dest.length,
      details: { expected: source.length, actual: dest.length, sample_ids: [] },
    },
    {
      name: "primary_keys",
      passed: !p.missing_in_dest.length,
      details: { expected: 0, actual: p.missing_in_dest.length, sample_ids: p.missing_in_dest.slice(0, 10) },
    },
    {
      name: "duplicates",
      passed: !p.duplicates.length,
      details: { expected: 0, actual: p.duplicates.length, sample_ids: p.duplicates.slice(0, 10) },
    },
    {
      name: "required_fields",
      passed: !p.missing_required.length,
      details: {
        expected: 0,
        actual: p.missing_required.length,
        sample_ids: p.missing_required.slice(0, 10),
      },
    },
    {
      // sample_ids holds column names for this check
      name: "schema",
      passed: !cols.missing.length,
      details: { expected: 0, actual: cols.missing.length, sample_ids: cols.missing.slice(0, 10) },
    },
  ];
  for (const r of results)
    db.checks.push({
      run_id: runId,
      batch_id: batch,
      ts,
      check_name: r.name,
      passed: r.passed,
      details: r.details,
    });
  const passed = results.every((r) => r.passed);
  const open = db.escalations.filter(
    (e) => e.run_id === runId && e.kind === "migration" && e.status === "open",
  );
  if (passed) open.forEach((e) => (e.status = "resolved"));
  else if (!open.length)
    db.escalations.push({
      key: `${runId}:migration:${batch}`,
      run_id: runId,
      kind: "migration",
      agent: "migration",
      status: "open",
      reason: `Migration check batch ${batch} needs the CSM.`,
      created_at: ts,
    });
  db.audit.push({
    id: db.audit.length + 1000,
    run_id: runId,
    ts,
    agent: "migration",
    step: "migration_check",
    status: passed ? "ok" : "escalated",
    rationale: passed ? "all five checks passed" : "escalated to CSM",
    input: null,
    output: { batch_id: batch, passed },
  });
  db.migration.lastCheck = { run_id: runId, batch_id: batch, ts, passed };
  return { batch_id: batch, passed, results, escalation: passed ? null : `${runId}:migration` };
}

export function makeHandlers(db: Db = createDb(), opts: { latency?: number } = {}) {
  const wait = () => (opts.latency ? delay(opts.latency) : Promise.resolve());
  const run = (id: string) => db.runs.find((r) => r.id === id);
  return [
    http.post("/auth/login", async ({ request }) => {
      const b = (await request.json()) as J;
      if (b.username === "admin" && b.password === "admin")
        return json({ user: "admin", next: b.next || "/ui" });
      return detail("Incorrect username or password.", 401);
    }),
    http.post("/auth/logout", () => json({ ok: true })),
    http.get("/auth/me", () => json({ user: "admin" })),
    http.get("/ui/config", () => json({ rocketlane_app_url: "https://novacrm.rocketlane.com" })),
    http.get("/healthz", () => json({ ok: true, mode: "fake" })),

    http.get("/runs", async () => {
      await wait();
      return json([...db.runs].sort((a, b) => (b.seq as number) - (a.seq as number)));
    }),
    http.get("/runs/:id", async ({ params }) => {
      await wait();
      const r = run(String(params.id));
      if (!r) return detail("run not found", 404);
      const llm = db.llmCalls.filter((x) => x.run_id === r.id).map(({ id: _id, ...x }) => x);
      return json({
        run: r,
        pending: pending(db, r),
        audit: db.audit
          .filter((x) => x.run_id === r.id)
          .sort((a, b) => String(a.ts).localeCompare(String(b.ts))),
        tool_calls: db.toolCalls.filter((x) => x.run_id === r.id).map(({ id: _id, ...x }) => x),
        migration_checks: db.checks.filter((x) => x.run_id === r.id),
        tracking: trackingView(db, r.id as string),
        llm: { ...group(llm), by_node: llm },
      });
    }),
    http.get("/escalations", ({ request }) => {
      const s = new URL(request.url).searchParams.get("status");
      return json(db.escalations.filter((e) => !s || e.status === s));
    }),
    http.post("/runs/:id/resolve", async ({ params, request }) => {
      await wait();
      const r = run(String(params.id));
      const b = (await request.json()) as J;
      if (!r) return detail("run not found", 404);
      const esc = db.escalations.find(
        (e) => e.run_id === r.id && e.status === "open" && e.kind !== "migration",
      );
      if (!esc) return detail("run is not waiting on an escalation", 422);
      if (!b.by) return detail("by is required", 422);
      esc.status = "resolved";
      db.audit.push({
        id: db.audit.length + 1000,
        run_id: r.id,
        ts: nowIso(),
        agent: "root",
        step: "resolve",
        status: "ok",
        rationale: `${b.action} by ${b.by}`,
        input: b,
        output: null,
      });
      if (b.action === "cancel") r.status = "CANCELLED";
      else if (b.action === "set_tier") {
        r.status = "TIER_CONFIRMED";
        r.plan_tier = b.tier;
      } else r.status = "AWAITING_CALL";
      r.updated_at = nowIso();
      return json({ ok: true, status: r.status });
    }),
    http.post("/runs/:id/sync", ({ params }) => {
      const r = run(String(params.id));
      if (!r?.rocketlane_project_id) return detail("run has no Rocketlane project yet", 404);
      if (db.tracking[r.id as string]) db.tracking[r.id as string].synced_at = nowIso();
      return json({ run_id: r.id, changed: false, actions: [] });
    }),
    http.post("/runs/:id/migration-check", async ({ params }) => {
      await wait();
      const r = run(String(params.id));
      if (!r) return detail("run not found", 404);
      if (!r.rocketlane_project_id) return detail("run has no Rocketlane project yet", 404);
      return json(runCheck(db, r.id as string));
    }),
    http.post("/runs/:id/migration-escalation/dismiss", async ({ params, request }) => {
      const b = (await request.json()) as J;
      if (!String(b.reason ?? "").trim() || !String(b.by ?? "").trim())
        return detail([{ loc: ["body", "reason"], msg: "String should match pattern '\\S'" }], 422);
      const open = db.escalations.filter(
        (e) => e.run_id === params.id && e.kind === "migration" && e.status === "open",
      );
      if (!open.length) return detail("no open migration escalation for this run", 404);
      open.forEach((e) => (e.status = "dismissed"));
      return json({ ok: true, dismissed: open.map((e) => e.key) });
    }),

    http.get("/metrics/overview", () => {
      const by: Record<string, number> = {};
      const days: Record<string, number> = {};
      for (const r of db.runs) {
        by[r.status as string] = (by[r.status as string] ?? 0) + 1;
        const d = String(r.created_at).slice(0, 10);
        days[d] = (days[d] ?? 0) + 1;
      }
      const outcomes = db.audit.filter((a) => a.step === "decide_tier").map((a) => a.status);
      return json({
        runs_total: db.runs.length,
        runs_by_status: by,
        runs_by_day: Object.entries(days).map(([day, runs]) => ({ day, runs })),
        escalations_open: db.escalations.filter((e) => e.status === "open").length,
        email_to_project: { runs: 6, avg_seconds: 412.6 },
        calls: {
          placed: db.toolCalls.filter((t) => t.tool === "voice_start_call" && t.status === "ok").length,
          confirmed: outcomes.filter((x) => x === "confirmed").length,
          unclear: outcomes.filter((x) => x === "unclear").length,
          no_answer: outcomes.filter((x) => x === "no_answer").length,
        },
        llm_calls: db.llmCalls.length,
        llm_cost_usd: group(db.llmCalls).cost_usd,
      });
    }),
    http.get("/metrics/llm", ({ request }) => {
      const days = Number(new URL(request.url).searchParams.get("days") ?? 14);
      const rows = db.llmCalls;
      const byDay = new Map<string, J[]>();
      for (const r of rows)
        byDay.set(String(r.ts).slice(0, 10), [...(byDay.get(String(r.ts).slice(0, 10)) ?? []), r]);
      const today = new Date();
      const series = Array.from({ length: days }, (_, i) => {
        const d = new Date(
          Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - (days - 1 - i)),
        )
          .toISOString()
          .slice(0, 10);
        return { day: d, ...group(byDay.get(d) ?? []) };
      });
      const buckets = [0, 0, 0, 0, 0, 0];
      for (const r of rows) buckets[bucket(r.latency_ms as number)]++;
      return json({
        totals: group(rows),
        by_model: groupsBy(rows, ["model"]),
        by_node: groupsBy(rows, ["agent", "node"]),
        by_prompt: groupsBy(rows, ["prompt_version"]),
        by_day: [],
        series,
        latency_buckets: buckets,
        latency_bucket_labels: ["< 1 s", "1-2 s", "2-5 s", "5-10 s", "10-30 s", "30 s+"],
        prices_usd_per_mtok: { "claude-haiku-5-5": [1, 5], "claude-sonnet-5-5": [3, 15] },
        prices_estimated: true,
      });
    }),
    http.get("/llm-calls", ({ request }) =>
      json(
        page(
          db.llmCalls,
          new URL(request.url),
          [
            "id",
            "run_id",
            "ts",
            "agent",
            "node",
            "model",
            "prompt_version",
            "input_tokens",
            "output_tokens",
            "latency_ms",
            "cost_usd",
            "stop_reason",
          ],
          ["model", "node", "run_id"],
          db,
        ),
      ),
    ),
    http.get("/metrics/tools", () => {
      const by = new Map<string, J[]>();
      for (const t of db.toolCalls) by.set(t.tool as string, [...(by.get(t.tool as string) ?? []), t]);
      return json({
        tools: [...by.entries()]
          .map(([tool, v]) => {
            const failed = v.filter((x) => x.status === "failed").length;
            const lat = v.map((x) => x.latency_ms as number);
            return {
              tool,
              calls: v.length,
              ok: v.length - failed,
              failed,
              replayed: tool === "slack_post_message" ? 1 : 0,
              blocked: 0,
              failure_rate: +(failed / v.length).toFixed(4),
              avg_latency_ms: Math.round(lat.reduce((a, b) => a + b, 0) / lat.length),
              p95_latency_ms: percentile(lat, 0.95),
            };
          })
          .sort((a, b) => b.calls - a.calls),
      });
    }),
    http.get("/tool-calls", ({ request }) =>
      json(
        page(
          db.toolCalls,
          new URL(request.url),
          ["id", "run_id", "ts", "agent", "tool", "status", "attempt", "latency_ms", "idempotency_key"],
          ["tool", "agent", "status", "run_id"],
          db,
        ),
      ),
    ),
    http.get("/tool-calls/:id", async ({ params }) => {
      await wait();
      const t = db.toolCalls.find((x) => String(x.id) === params.id);
      if (!t) return detail("tool call not found", 404);
      return json({ ...t, customer_name: run(t.run_id as string)?.customer_name ?? null });
    }),

    http.get("/aes", () => json(db.aes.map(aeOut))),
    http.post("/aes", async ({ request }) => {
      await wait();
      const b = (await request.json()) as J;
      const email = String(b.email ?? "")
        .trim()
        .toLowerCase();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return detail("email: not a valid email address", 422);
      if (!String(b.name ?? "").trim()) return detail("name: must not be empty", 422);
      if (!/^\+[1-9]\d{7,14}$/.test(String(b.phone ?? "")))
        return detail("phone: must be E.164, for example +919789998350", 422);
      if (db.aes.some((a) => a.email === email)) return detail("an AE with this email already exists", 409);
      const a = {
        email,
        name: String(b.name).trim(),
        phone: b.phone,
        active: b.active !== false,
        updated_at: nowIso(),
      };
      db.aes.push(a);
      return json(aeOut(a), 201);
    }),
    http.patch("/aes/:email", async ({ params, request }) => {
      await wait();
      const a = db.aes.find((x) => x.email === decodeURIComponent(String(params.email)));
      if (!a) return detail("AE not found", 404);
      const b = (await request.json()) as J;
      if (b.phone !== undefined && !/^\+[1-9]\d{7,14}$/.test(String(b.phone)))
        return detail("phone: must be E.164, for example +919789998350", 422);
      if (b.name !== undefined && !String(b.name).trim()) return detail("name: must not be empty", 422);
      Object.assign(
        a,
        Object.fromEntries(Object.entries(b).filter(([k]) => ["name", "phone", "active"].includes(k))),
        { updated_at: nowIso() },
      );
      return json(aeOut(a));
    }),

    http.get("/migration/summary", () => json(summary(db))),
    http.get("/migration/data", ({ request }) => {
      const u = new URL(request.url);
      const side = u.searchParams.get("side") === "dest" ? "dest" : "source";
      const limit = Number(u.searchParams.get("limit") ?? 25);
      const offset = Number(u.searchParams.get("offset") ?? 0);
      const only = u.searchParams.get("only_problems") === "true";
      const p = migrationProblems(db);
      const rows = db.migration[side].map((r) => {
        const problems: string[] = [];
        if (side === "source" && p.missing_in_dest.includes(r.id)) problems.push("missing_in_dest");
        if (side === "dest" && p.duplicates.includes(r.id)) problems.push("duplicate");
        // Same codes as backend row_problems(): one missing_<field> tag per blank required field.
        if (side === "dest" && !String(r.name ?? "").trim()) problems.push("missing_name");
        if (side === "dest" && !String(r.email ?? "").trim()) problems.push("missing_email");
        return { ...r, problems };
      });
      const match = only ? rows.filter((r) => r.problems.length) : rows;
      return json({ total: match.length, rows: match.slice(offset, offset + limit) });
    }),
    http.post("/migration/demo/fix", async () => {
      await wait();
      repairDest(db);
      return json(summary(db));
    }),
    http.post("/migration/demo/reset", async () => {
      await wait();
      const broken = db.brokenDest();
      db.migration.dest = broken.rows;
      db.migration.destColumns = broken.columns;
      return json(summary(db));
    }),

    http.get("/agents/status", () => json(db.agents)),
  ];
}
