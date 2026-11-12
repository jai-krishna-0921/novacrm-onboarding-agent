// In-memory sample data for `npm run dev:mock` and tests. Shapes follow the backend contract.
// Clearly fake: customers are invented companies, phone numbers are masked, ids are made up.

type J = Record<string, unknown>;

const NOW = Date.now();
const iso = (minAgo: number) => new Date(NOW - minAgo * 60_000).toISOString();

export type Db = ReturnType<typeof createDb>;

export function createDb() {
  const runs: J[] = [];
  const audit: J[] = [];
  const toolCalls: J[] = [];
  const llmCalls: J[] = [];
  const escalations: J[] = [];
  const checks: J[] = [];
  const tracking: Record<string, J> = {};
  let auditId = 1;
  let toolId = 1;
  let llmId = 1;
  let seq = 1;

  const a = (
    run_id: string,
    min: number,
    agent: string,
    step: string,
    status = "ok",
    rationale = "",
    input: unknown = null,
    output: unknown = null,
  ) => audit.push({ id: auditId++, run_id, ts: iso(min), agent, step, status, rationale, input, output });
  const tool = (
    run_id: string,
    min: number,
    agent: string,
    name: string,
    status = "ok",
    args: unknown = {},
    result: unknown = {},
    latency = 420,
    attempt = 1,
  ) =>
    toolCalls.push({
      id: toolId++,
      run_id,
      ts: iso(min),
      agent,
      tool: name,
      status,
      attempt,
      latency_ms: latency,
      idempotency_key: `${run_id}:${name}:${attempt}`,
      args,
      result,
    });
  const llm = (
    run_id: string,
    min: number,
    agent: string,
    node: string,
    tin: number,
    tout: number,
    latency: number,
    model = "claude-haiku-5-5",
    prompt = `${node}@v3`,
  ) =>
    llmCalls.push({
      id: llmId++,
      run_id,
      ts: iso(min),
      agent,
      node,
      model,
      prompt_version: prompt,
      input_tokens: tin,
      output_tokens: tout,
      latency_ms: latency,
      cost_usd: +(tin * 1e-6 + tout * 5e-6).toFixed(6),
      stop_reason: "end_turn",
    });

  const TRANSCRIPT = [
    "Agent: Hi Priya, this is the NovaCRM onboarding assistant calling about the Larkspur Health deal.",
    "User: Hi, yes, I closed that one yesterday.",
    "Agent: Can you confirm which plan tier the customer bought?",
    "User: They signed for Enterprise. Three year term, with the data migration package.",
    "Agent: Thank you. So that is the Enterprise plan. Is that correct?",
    "User: Correct, Enterprise.",
    "Agent: Great, I will set up the onboarding project now. Have a good day.",
  ].join("\n");
  const UNCLEAR = [
    "Agent: Hi Marcus, calling to confirm the plan tier for Quarry Labs.",
    "User: Honestly I am not sure yet, finance is still deciding between the two.",
    "Agent: Would you say it is Enterprise or Growth?",
    "User: Could be either. Let me get back to you.",
  ].join("\n");

  type Spec = {
    id: string;
    name: string;
    status: string;
    tier?: string;
    project?: string;
    slack?: string;
    opp: string;
    created: number;
    updated: number;
    ae?: string;
    stage: number;
    error?: string;
  };
  const specs: Spec[] = [
    {
      id: "r-7f3a91",
      name: "Larkspur Health",
      status: "COMPLETED",
      tier: "enterprise",
      project: "48213",
      slack: "C07LARK",
      opp: "OPP-30418",
      created: 1440 * 3,
      updated: 4,
      ae: "Priya Raman",
      stage: 7,
    },
    {
      id: "r-2b8c44",
      name: "Quarry Labs",
      status: "ESCALATED",
      opp: "OPP-30422",
      created: 180,
      updated: 22,
      ae: "Marcus Oyelaran",
      stage: 3,
    },
    {
      id: "r-91d0e2",
      name: "Fernhill Logistics",
      status: "AWAITING_CALL",
      opp: "OPP-30431",
      created: 6,
      updated: 1,
      ae: "Elena Vasquez",
      stage: 2,
    },
    {
      id: "r-c4e7a0",
      name: "Brightwater Analytics",
      status: "HANDED_OFF",
      tier: "growth",
      project: "47790",
      slack: "C06BRWA",
      opp: "OPP-30107",
      created: 1440 * 12,
      updated: 1440,
      ae: "Tomasz Wielgus",
      stage: 7,
    },
    {
      id: "r-5a6f19",
      name: "Ostara Foods",
      status: "COMPLETED",
      tier: "enterprise",
      project: "48102",
      slack: "C07OSTA",
      opp: "OPP-30377",
      created: 1440 * 5,
      updated: 35,
      ae: "Priya Raman",
      stage: 7,
    },
    {
      id: "r-e83b27",
      name: "Kestrel Mobility",
      status: "AWAITING_CLARIFICATION",
      opp: "OPP-30440",
      created: 95,
      updated: 90,
      ae: "Elena Vasquez",
      stage: 1,
    },
    {
      id: "r-0d4c58",
      name: "Pinecrest Dental Group",
      status: "AWAITING_RETRY",
      opp: "OPP-30436",
      created: 40,
      updated: 12,
      ae: "Marcus Oyelaran",
      stage: 2,
    },
    {
      id: "r-6b1f73",
      name: "Halden Insurance",
      status: "CANCELLED",
      opp: "OPP-30219",
      created: 1440 * 8,
      updated: 1440 * 7,
      ae: "Tomasz Wielgus",
      stage: 3,
    },
    {
      id: "r-a92e05",
      name: "Northgate Ceramics",
      status: "PROJECT_CREATED",
      tier: "growth",
      project: "48251",
      opp: "OPP-30444",
      created: 14,
      updated: 0.5,
      ae: "Elena Vasquez",
      stage: 5,
    },
    {
      id: "r-3c7d61",
      name: "Vellum Studio",
      status: "FAILED",
      opp: "OPP-30301",
      created: 1440 * 2,
      updated: 1440 * 2 - 3,
      ae: "Priya Raman",
      stage: 2,
      error: "rocketlane_create_project failed after 3 attempts: 503 Service Unavailable",
    },
    {
      id: "r-f0a8b3",
      name: "Summit Ridge Credit Union",
      status: "COMPLETED",
      tier: "growth",
      project: "47988",
      slack: "C06SRCU",
      opp: "OPP-30254",
      created: 1440 * 6,
      updated: 60 * 5,
      ae: "Marcus Oyelaran",
      stage: 7,
    },
    {
      id: "r-11b4c9",
      name: "Re: weekly pipeline",
      status: "NOT_A_DEAL",
      opp: "",
      created: 1440 * 4,
      updated: 1440 * 4,
      stage: 1,
    },
  ];

  for (const s of specs) {
    const r: J = {
      id: s.id,
      seq: seq++,
      status: s.status,
      gmail_message_id: `18c${s.id.slice(2)}9e`,
      opportunity_id: s.opp || null,
      customer_key: s.name.toLowerCase().replace(/\W+/g, "-"),
      customer_name: s.status === "NOT_A_DEAL" ? null : s.name,
      plan_tier: s.tier ?? null,
      rocketlane_project_id: s.project ?? null,
      slack_channel_id: s.slack ?? null,
      error: s.error ?? null,
      created_at: iso(s.created),
      updated_at: iso(s.updated),
    };
    runs.push(r);
    const t0 = s.created;
    a(s.id, t0, "root", "start", "ok", "deal email received", {
      subject: `Closed Won: ${s.name}`,
      from: "ae@novacrm.com",
    });
    if (s.status === "NOT_A_DEAL") {
      a(s.id, t0 - 0.2, "intake", "check_sender", "ok", "not a closed-won deal email");
      continue;
    }
    a(s.id, t0 - 0.1, "intake", "check_sender", "ok", "sender is an active AE");
    tool(
      s.id,
      t0 - 0.1,
      "intake",
      "ae_lookup",
      "ok",
      { email: "ae@novacrm.com" },
      { name: s.ae, phone: "******8350" },
      38,
    );
    a(s.id, t0 - 0.1, "intake", "tool:ae_lookup", "ok", "tool call succeeded", null, { name: s.ae });
    a(s.id, t0 - 0.2, "intake", "extract_fields", "ok", "fields read from the email", null, {
      customer_name: s.name,
      opportunity_id: s.opp,
    });
    llm(s.id, t0 - 0.2, "intake", "extract_fields", 1840, 212, 1630);
    if (s.status === "AWAITING_CLARIFICATION") {
      a(
        s.id,
        t0 - 0.3,
        "intake",
        "validate",
        "blocked",
        "required fields missing; asked the AE by email",
        null,
        { issues: ["contract start date", "customer admin email"] },
      );
      tool(
        s.id,
        t0 - 0.4,
        "intake",
        "gmail_send",
        "ok",
        { to: "elena.vasquez@novacrm.com", subject: "Missing details for Kestrel Mobility" },
        { id: "msg-77" },
        610,
      );
      continue;
    }
    a(s.id, t0 - 0.3, "intake", "validate", "ok", "all required fields present");
    a(s.id, t0 - 0.3, "intake", "lookup_ae", "ok", `AE ${s.ae} found`);
    tool(
      s.id,
      t0 - 0.5,
      "intake",
      "voice_start_call",
      "ok",
      { to: "******8350", run_id: s.id },
      { call_id: `call_${s.id}_1` },
      880,
    );
    if (s.status === "AWAITING_CALL") continue;
    if (s.status === "AWAITING_RETRY") {
      a(
        s.id,
        t0 - 3,
        "intake",
        "decide_tier",
        "no_answer",
        "call not answered",
        { attempt: 1, status: "no_answer", transcript: "" },
        { outcome: "no_answer", tier: null },
      );
      continue;
    }
    if (s.id === "r-2b8c44") {
      for (let i = 1; i <= 3; i++) {
        llm(s.id, t0 - 10 * i, "intake", "interpret_call", 960, 88, 1210);
        a(
          s.id,
          t0 - 10 * i,
          "intake",
          "decide_tier",
          "unclear",
          "AE did not commit to a tier",
          { attempt: i, status: "connected", provider_tier: null, confirmed: false, transcript: UNCLEAR },
          { outcome: "unclear", tier: null, cross_check: "unclear" },
        );
        if (i < 3)
          tool(
            s.id,
            t0 - 10 * i - 5,
            "intake",
            "voice_start_call",
            "ok",
            { to: "******8350" },
            { call_id: `call_${s.id}_${i + 1}` },
            790,
            i + 1,
          );
      }
      a(
        s.id,
        22,
        "root",
        "escalate",
        "escalated",
        "No clear plan tier after 3 call attempts (last: AE did not commit to a tier)",
      );
      escalations.push({
        key: `${s.id}:tier`,
        run_id: s.id,
        kind: "tier",
        agent: "intake",
        status: "open",
        reason:
          "No clear plan tier after 3 call attempts. The AE said finance is still choosing between Enterprise and Growth.",
        resolved_by: null,
        created_at: iso(22),
      });
      continue;
    }
    if (s.id === "r-6b1f73") {
      a(
        s.id,
        t0 - 5,
        "intake",
        "decide_tier",
        "unclear",
        "AE unsure",
        { attempt: 1, status: "connected", transcript: UNCLEAR },
        { outcome: "unclear" },
      );
      a(s.id, s.updated, "root", "resolve", "ok", "cancelled by Dana Whitlock", {
        action: "cancel",
        by: "Dana Whitlock",
        note: "Deal fell through",
      });
      continue;
    }
    if (s.id === "r-3c7d61") {
      a(
        s.id,
        t0 - 2,
        "intake",
        "decide_tier",
        "confirmed",
        "AE confirmed Growth",
        {
          attempt: 1,
          status: "connected",
          transcript: TRANSCRIPT.replace(/Enterprise/g, "Growth").replace("Larkspur Health", s.name),
        },
        { outcome: "confirmed", tier: "growth" },
      );
      tool(
        s.id,
        t0 - 3,
        "intake",
        "rocketlane_create_project",
        "failed",
        { template: "growth", customer: s.name },
        { error: "503 Service Unavailable" },
        30000,
        3,
      );
      a(s.id, t0 - 3, "root", "failed", "failed", s.error);
      continue;
    }
    llm(s.id, t0 - 2, "intake", "interpret_call", 1020, 96, 1340);
    a(s.id, t0 - 2, "intake", "voice_webhook", "ok", "call result received; resuming");
    a(
      s.id,
      t0 - 2,
      "intake",
      "decide_tier",
      "confirmed",
      `AE confirmed the ${s.tier} plan; transcript cross-check agrees`,
      {
        attempt: 1,
        status: "connected",
        provider_tier: s.tier,
        confirmed: true,
        transcript: TRANSCRIPT.replace(/Enterprise/g, s.tier === "growth" ? "Growth" : "Enterprise")
          .replace("Larkspur Health", s.name)
          .replace("Priya", (s.ae ?? "").split(" ")[0]),
      },
      { outcome: "confirmed", tier: s.tier, cross_check: s.tier },
    );
    a(s.id, t0 - 2.2, "intake", "check_duplicate", "ok", "no active project for this customer");
    tool(
      s.id,
      t0 - 2.4,
      "intake",
      "rocketlane_create_project",
      "ok",
      { template: s.tier, customer: s.name, opportunity_id: s.opp },
      { project_id: s.project },
      2310,
    );
    a(
      s.id,
      t0 - 2.4,
      "intake",
      "create_project",
      "ok",
      `project ${s.project} created from the ${s.tier} template`,
    );
    if (s.stage < 6) continue;
    llm(s.id, t0 - 2.6, "comms", "draft_welcome", 2210, 380, 2890);
    llm(s.id, t0 - 2.7, "comms", "check_welcome", 1430, 64, 940, "claude-haiku-5-5", "check_welcome@v2");
    a(s.id, t0 - 2.7, "comms", "draft_welcome", "ok", "welcome drafted and fact-checked");
    tool(
      s.id,
      t0 - 2.8,
      "comms",
      "slack_create_channel",
      "ok",
      { name: `ext-${r.customer_key}` },
      { channel_id: s.slack },
      640,
    );
    tool(
      s.id,
      t0 - 2.9,
      "comms",
      "slack_post_message",
      "ok",
      { channel: s.slack, text: "(welcome message)" },
      { ts: "1731400000.000100" },
      310,
    );
    a(s.id, t0 - 2.9, "comms", "setup_channel", "ok", "channel ready, welcome posted");
    a(s.id, t0 - 3, "root", "complete", "ok", "onboarding set up");
    const phases =
      s.tier === "enterprise"
        ? [
            ["Kickoff", 4, 4],
            ["Configuration", 6, 5],
            ["Data migration", 5, s.id === "r-5a6f19" ? 2 : 3],
            ["Training", 4, 0],
            ["Go-live", 3, 0],
          ]
        : [
            ["Kickoff", 3, 3],
            ["Configuration", 4, 4],
            ["Training", 3, s.status === "HANDED_OFF" ? 3 : 1],
            ["Go-live", 2, s.status === "HANDED_OFF" ? 2 : 0],
          ];
    const tasks: J[] = [];
    for (const [ph, total, done] of phases as [string, number, number][])
      for (let i = 0; i < total; i++) tasks.push({ phase: ph, completed: i < done });
    tracking[s.id] = {
      tasks,
      synced_at: iso(s.id === "r-c4e7a0" ? 1440 : 2),
      handoff_summary:
        s.status === "HANDED_OFF"
          ? "Brightwater Analytics finished all four phases on schedule. Two admins trained. Open item: SSO setup is owned by their IT team (ticket SUP-2291)."
          : null,
      handed_off_at: s.status === "HANDED_OFF" ? iso(1440) : null,
    };
    a(s.id, 30, "tracking", "sync", "ok", "progress synced");
    tool(
      s.id,
      30,
      "tracking",
      "rocketlane_list_tasks",
      "ok",
      { project_id: s.project },
      { count: tasks.length },
      520,
    );
  }

  // Migration checks for the two enterprise projects.
  const batch = (run_id: string, id: string, min: number, fails: boolean) => {
    const rows: [string, number, number, unknown[]][] = fails
      ? [
          ["record_count", 60, 57, []],
          ["primary_keys", 0, 4, [12, 31, 47, 58]],
          ["duplicates", 0, 1, [23]],
          ["required_fields", 0, 3, [17, 36, 44]],
          ["schema", 0, 1, ["phone"]],
        ]
      : [
          ["record_count", 60, 60, []],
          ["primary_keys", 0, 0, []],
          ["duplicates", 0, 0, []],
          ["required_fields", 0, 0, []],
          ["schema", 0, 0, []],
        ];
    for (const [name, expected, actual, ids] of rows)
      checks.push({
        run_id,
        batch_id: id,
        ts: iso(min),
        check_name: name,
        passed: name === "record_count" ? expected === actual : actual === 0,
        details: { expected, actual, sample_ids: ids },
      });
  };
  batch("r-5a6f19", "b81e2c0d4f11", 600, false);
  batch("r-5a6f19", "c03a7d91e2b4", 45, true);
  a(
    "r-5a6f19",
    45,
    "migration",
    "migration_check",
    "escalated",
    "escalated to CSM: primary_keys (expected 0, actual 4); duplicates (expected 0, actual 1); required_fields (expected 0, actual 3); schema (expected 0, actual 1)",
  );
  escalations.push({
    key: "r-5a6f19:migration:c03a7d91e2b4",
    run_id: "r-5a6f19",
    kind: "migration",
    agent: "migration",
    status: "open",
    reason:
      "Migration check batch c03a7d91e2b4 needs the CSM: 4 source rows missing in the destination, 1 duplicate id, 3 rows without a name or email, missing column phone.",
    created_at: iso(45),
  });
  batch("r-7f3a91", "a77f0b13c9d2", 300, false);

  // A spread of older LLM calls for the charts.
  for (let d = 13; d >= 0; d--) {
    const n = [3, 5, 2, 7, 4, 0, 1, 6, 9, 4, 3, 8, 5, 6][13 - d];
    for (let i = 0; i < n; i++) {
      const min = d * 1440 + 60 + i * 37;
      const node = ["extract_fields", "interpret_call", "draft_welcome", "check_welcome", "draft_handoff"][
        i % 5
      ];
      llm(
        specs[i % 6].id,
        min,
        node.includes("handoff") ? "tracking" : node.includes("welcome") ? "comms" : "intake",
        node,
        900 + ((i * 137) % 1400),
        60 + ((i * 53) % 400),
        700 + ((i * 911) % 5200),
        i % 7 === 3 ? "claude-sonnet-5-5" : "claude-haiku-5-5",
      );
    }
  }
  llmCalls.sort((x, y) => String(x.ts).localeCompare(String(y.ts)));
  llmCalls.forEach((r, i) => (r.id = i + 1));

  const aes: J[] = [
    {
      email: "priya.raman@novacrm.com",
      name: "Priya Raman",
      phone: "+919789998350",
      active: true,
      updated_at: iso(1440 * 20),
    },
    {
      email: "marcus.oyelaran@novacrm.com",
      name: "Marcus Oyelaran",
      phone: "+14155550134",
      active: true,
      updated_at: iso(1440 * 9),
    },
    {
      email: "elena.vasquez@novacrm.com",
      name: "Elena Vasquez",
      phone: "+34612345678",
      active: true,
      updated_at: iso(1440 * 3),
    },
    {
      email: "tomasz.wielgus@novacrm.com",
      name: "Tomasz Wielgus",
      phone: "+48601234567",
      active: true,
      updated_at: iso(1440 * 31),
    },
    {
      email: "hannah.okafor@novacrm.com",
      name: "Hannah Okafor",
      phone: "+447700900812",
      active: false,
      updated_at: iso(1440 * 60),
    },
    {
      email: "diego.marchetti@novacrm.com",
      name: "Diego Marchetti",
      phone: "+393471234590",
      active: true,
      updated_at: iso(1440 * 2),
    },
  ];

  // Contacts, mirroring backend/app/db/seed.py demo_contacts(): 60 source rows (some phone/title blank); the
  // 57-row destination has no phone column, an extra lifecycle_stage column, ids 12/31/47/58 never copied, id 23
  // duplicated, blank email on 17 and 44, blank name on 36.
  const FIRST = [
    "Ava",
    "Ben",
    "Chloe",
    "Dev",
    "Elena",
    "Farah",
    "Gil",
    "Hana",
    "Ivan",
    "Jia",
    "Kofi",
    "Lena",
  ];
  const LAST = ["Shah", "Okafor", "Novak", "Reyes", "Tanaka"];
  const COMPANIES = ["Globex", "Globex Retail", "Globex Logistics", "Globex Health"];
  const TITLES = ["VP Sales", "Account Manager", "Ops Lead", "CFO", "Analyst", "", "Director, IT"];
  const STAGES = ["customer", "customer", "lead", "opportunity"];
  const sourceColumns = ["id", "name", "email", "phone", "company", "title", "created_on"];
  const destColumns = ["id", "name", "email", "company", "title", "created_on", "lifecycle_stage"];
  const source: J[] = Array.from({ length: 60 }, (_, k) => {
    const i = k + 1;
    const first = FIRST[(i - 1) % 12];
    const last = LAST[Math.floor((i - 1) / 12)];
    return {
      id: i,
      name: `${first} ${last}`,
      email: `${first}.${last}@globex.example`.toLowerCase(),
      phone: i % 9 === 0 ? null : `+1 415 555 ${String(1000 + i).padStart(4, "0")}`,
      company: COMPANIES[i % 4],
      title: TITLES[i % 7] || null,
      created_on: new Date(Date.UTC(2024, 0, 1 + 11 * i)).toISOString().slice(0, 10),
    };
  });
  const brokenDest = () => {
    const dropped = new Set([12, 31, 47, 58]);
    const dest: J[] = [];
    for (const r of source) {
      const id = r.id as number;
      if (dropped.has(id)) continue;
      const d: J = {};
      for (const c of destColumns.slice(0, -1)) d[c] = r[c];
      d.lifecycle_stage = STAGES[id % 4];
      if (id === 17 || id === 44) d.email = null;
      if (id === 36) d.name = "";
      dest.push(d);
      if (id === 23) dest.push({ ...d });
    }
    return { rows: dest, columns: [...destColumns] };
  };
  const broken = brokenDest();
  const migration = {
    source,
    sourceColumns,
    dest: broken.rows,
    destColumns: broken.columns,
    lastCheck: { run_id: "r-5a6f19", batch_id: "c03a7d91e2b4", ts: iso(45), passed: false } as J | null,
  };

  const agents = {
    agents: [
      {
        id: "intake",
        name: "Intake and routing",
        description: "Reads deal emails, checks them, calls the AE, creates the project.",
        status: "running",
        last_activity_at: iso(1),
        last_activity: "Calling Elena Vasquez about Fernhill Logistics",
        stats: { runs_today: 4, calls_placed_today: 6, escalated_today: 1 },
        next_run_at: null,
      },
      {
        id: "comms",
        name: "Communication",
        description: "Creates the customer Slack channel and posts the welcome.",
        status: "idle",
        last_activity_at: iso(14),
        last_activity: "Posted the welcome in the Northgate Ceramics channel",
        stats: { channels_created_today: 2, welcomes_posted_today: 2 },
        next_run_at: null,
      },
      {
        id: "tracking",
        name: "Project tracking",
        description: "Syncs progress from Rocketlane, runs migration checks, writes handoffs.",
        status: "idle",
        last_activity_at: iso(2),
        last_activity: "Synced 3 projects, no changes",
        stats: { projects_tracked: 4, handoffs_this_week: 1, migration_checks_today: 1 },
        next_run_at: new Date(NOW + 4 * 60_000 + 12_000).toISOString(),
      },
    ],
    scheduler: { last_tick_at: iso(0.05), interval_seconds: 5, status: "running" },
    gmail: {
      last_sync_at: iso(0.3),
      last_push_at: iso(6),
      watch_expires_at: new Date(NOW + 30 * 3_600_000).toISOString(),
    },
    // Same names and shape as backend services/health.py (checked_at is null until the first check).
    integrations: [
      { name: "gmail", ok: true, detail: "inbox reachable", checked_at: iso(0.5) },
      { name: "retell", ok: true, detail: "voice agent configured", checked_at: iso(0.5) },
      { name: "rocketlane", ok: true, detail: "API reachable, 2 templates", checked_at: iso(0.5) },
      { name: "slack", ok: false, detail: "invalid_auth", checked_at: iso(0.5) },
      { name: "anthropic", ok: false, detail: "first check pending", checked_at: null },
    ],
    rocketlane_automations: [
      { name: "Overdue task reminder", trigger: "a task is 1 day overdue", notify: "the task owner" },
      {
        name: "Overdue escalation",
        trigger: "a task is 3 days overdue",
        notify: "the CSM and the project owner",
      },
      { name: "Phase complete", trigger: "all tasks in a phase are done", notify: "the project channel" },
    ],
  };

  // AE call attempts per run (GET /runs/:id/calls); recordings are served as a tiny WAV by the handlers.
  const calls: Record<string, J[]> = {
    "r-7f3a91": [
      {
        call_id: "call_a1",
        attempt: 1,
        outcome: "no_answer",
        transcript: null,
        started_at: Date.now() - 3 * 86400_000,
        duration_ms: 31_000,
        has_recording: false,
      },
      {
        call_id: "call_a2",
        attempt: 2,
        outcome: "confirmed",
        transcript:
          "Agent: Hi Priya, this is the NovaCRM onboarding assistant calling about Larkspur Health.\nAE: Hi, yes.\nAgent: Which plan did they sign, Starter, Growth or Enterprise?\nAE: Enterprise, the full package.\nAgent: Thanks, I will set up the Enterprise onboarding.",
        started_at: Date.now() - 3 * 86400_000 + 3600_000,
        duration_ms: 74_500,
        has_recording: true,
      },
    ],
  };
  // Per-run customer database (no password is ever kept here either).
  const migrationSources: Record<string, J> = {};

  return {
    calls,
    migrationSources,
    runs,
    audit,
    toolCalls,
    llmCalls,
    escalations,
    checks,
    tracking,
    aes,
    migration,
    brokenDest,
    agents,
  };
}
