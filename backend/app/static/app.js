// NovaCRM onboarding dashboard. Vanilla JS, same-origin API, no external assets.
// All data is rendered with textContent / text nodes (emails and transcripts are untrusted), never parsed as markup.
"use strict";

const REFRESH_MS = 3000;
// Mirrors backend/app/graph/root.py ALLOWED (the API re-validates every resolution).
const ALLOWED = {
  tier: ["set_tier", "retry", "cancel"], input: ["retry", "cancel"], duplicate: ["retry", "cancel"],
  system: ["retry", "cancel"],
};
const TIERS = ["enterprise", "growth"];
const KIND = {
  tier: "Plan tier not confirmed", input: "Deal email needs a fix", duplicate: "Possible duplicate project",
  system: "System problem", migration: "Migration check failed",
};
const STEPS = [
  ["email", "Email received"], ["checked", "Checked"], ["call", "Calling AE"], ["tier", "Tier confirmed"],
  ["project", "Rocketlane project"], ["slack", "Slack channel"], ["tracking", "Tracking & handoff"],
];
const STATUS = {  // run.status -> [chip label, tone, list group]
  RECEIVED: ["Checking", "active", "active"], AWAITING_CLARIFICATION: ["Waiting on AE", "warn", "attn"],
  AWAITING_CALL: ["On a call", "active", "active"], AWAITING_RETRY: ["Retrying call", "active", "active"],
  TIER_CONFIRMED: ["Creating project", "active", "active"], PROJECT_CREATED: ["Setting up Slack", "active", "active"],
  COMPLETED: ["In onboarding", "ok", "done"], HANDED_OFF: ["Handed off", "ok", "done"],
  ESCALATED: ["Needs a human", "warn", "attn"], FAILED: ["Error", "bad", "attn"], CANCELLED: ["Cancelled", "idle", "done"], NOT_A_DEAL: ["Not a deal email", "idle", "done"],
};
const TERMINAL = new Set(["HANDED_OFF", "CANCELLED", "FAILED", "NOT_A_DEAL"]);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const app = document.getElementById("app");
const live = document.getElementById("live");
let config = { rocketlane_app_url: "" };
let view = null;          // {name, id}
let inflight = null;      // the refresh in progress, if any
let ui = null;            // per-view persistent nodes (survive the 3 s refresh)
let transition = null;    // "fwd" | "back" for the next paint
let backTo = null;        // in-app hash we came from (Back uses history when set)
const cache = new Map();  // run id -> {sig, at, data}: run detail, used by the list for names and steppers

// ---------- helpers ----------
function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k in n && k !== "list") n[k] = v;
    else n.setAttribute(k, v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

async function api(path, opts = {}) {
  const init = { headers: { Accept: "application/json" }, method: opts.method || "GET" };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  if (r.status === 401) { toLogin(); throw Object.assign(new Error("Signed out"), { status: 401 }); }
  let data = null;
  try { data = await r.json(); } catch (_) { /* non-JSON body */ }
  if (!r.ok) {
    let msg = data && data.detail !== undefined ? data.detail : r.statusText;
    if (typeof msg !== "string") msg = JSON.stringify(msg);
    const e = new Error(msg || `Request failed (${r.status})`);
    e.status = r.status;
    throw e;
  }
  return data;
}

// Session missing or expired: go to the login page and come back to this exact view afterwards.
function toLogin() {
  location.assign(`/ui/login?next=${enc(`/ui${location.hash}`)}`);
}

function fmtTime(iso, withDate) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return withDate ? `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${t}` : t;
}

function ago(iso) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (!isFinite(s)) return "";
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const cap = (s) => { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); };
const human = (s) => cap(String(s || "").replace(/[_:]+/g, " ").trim());
const str = (v) => (v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));
const json = (v) => el("pre", {}, typeof v === "string" ? v : JSON.stringify(v, null, 2));
const errorBox = (e) => el("div", { class: "error", role: "alert" }, e.message || String(e));
const chip = (text, tone) => el("span", { class: `chip ${tone || ""}` }, text);
const tierChip = (t) => (t ? el("span", { class: `tier ${String(t).toLowerCase()}` }, t) : null);
function enc(s) { return encodeURIComponent(s); }
// replaceChildren() would print null/false as text; drop them (and flatten arrays) first.
const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));

function statusChip(run, info) {
  const [label, tone] = STATUS[String(run.status || "").toUpperCase()] || [human(run.status || "unknown"), "idle"];
  if (info && info.migrationEsc) return chip("Migration issue", "warn");
  return chip(label, tone);
}

function projectLink(id) {
  if (!id) return null;
  const base = config.rocketlane_app_url;
  if (!/^https?:\/\//i.test(base || "")) return el("span", {}, "Rocketlane ", el("span", { class: "mono" }, `#${id}`));
  return el("a", { href: `${base}/projects/${enc(id)}`, target: "_blank", rel: "noopener" }, `Open in Rocketlane #${id} ↗`);
}

function getPref(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
function setPref(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* storage blocked */ } }

function setLive(ok, msg) {
  live.className = ok ? "live on" : "live err";
  live.textContent = ok ? `Live, updated ${fmtTime(new Date().toISOString())}` : (msg || "Offline");
}

// Segmented control with a sliding indicator. opts: [{value, label}]
function seg(opts, value, onChange, cls) {
  const ind = el("span", { class: "seg-ind", "aria-hidden": "true" });
  const root = el("div", { class: `seg ${cls || ""}`, role: "group" }, ind);
  const btns = opts.map((o) => {
    const b = el("button", { type: "button", "aria-pressed": String(o.value === value),
      onclick: () => { root.select(o.value); onChange(o.value); } }, o.label, el("span", { class: "n" }));
    b.dataset.v = o.value;
    root.append(b);
    return b;
  });
  const place = () => {
    const b = btns.find((x) => x.getAttribute("aria-pressed") === "true");
    if (!b || !b.offsetWidth) { ind.style.width = "0"; return; }
    ind.style.width = `${b.offsetWidth}px`;
    ind.style.transform = `translateX(${b.offsetLeft}px)`;
    if (!root.classList.contains("ready")) requestAnimationFrame(() => root.classList.add("ready"));
  };
  root.select = (v) => { btns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === v))); place(); };
  root.value = () => (btns.find((b) => b.getAttribute("aria-pressed") === "true") || {}).dataset?.v;
  root.setCount = (v, n, hot) => {
    const b = btns.find((x) => x.dataset.v === v);
    if (!b) return;
    b.querySelector(".n").textContent = n === undefined ? "" : String(n);
    b.classList.toggle("hot", !!hot);
  };
  if ("ResizeObserver" in window) new ResizeObserver(place).observe(root);
  requestAnimationFrame(place);
  return root;
}

// ---------- what a run is doing, in plain words ----------
function lastAudit(audit, step, pred) {
  for (let i = audit.length - 1; i >= 0; i--) if (audit[i].step === step && (!pred || pred(audit[i]))) return audit[i];
  return null;
}

function nameOf(run, d) {
  if (run.customer_name) return run.customer_name;
  const audit = (d && d.audit) || [];
  const ex = lastAudit(audit, "extract_fields");
  if (ex && ex.output && ex.output.customer_name) return String(ex.output.customer_name);
  const st = lastAudit(audit, "start");
  const subj = st && st.input && st.input.subject;
  if (subj) return String(subj).replace(/^\s*(re:|fwd?:)?\s*closed[\s-]*won\s*[:-]?\s*/i, "") || String(subj);
  return "New deal";
}

function aeOf(audit) {
  const t = lastAudit(audit, "tool:ae_lookup", (a) => a.output && a.output.name);
  return t ? String(t.output.name) : "";
}

// Derive the 7-step pipeline, the headline and the tone from run + detail (detail may be missing in the list).
function derive(run, d, openEscs) {
  d = d || {};
  const audit = d.audit || [];
  const pending = d.pending || null;
  const tr = d.tracking && typeof d.tracking === "object" ? d.tracking : null;
  const status = String(run.status || "").toUpperCase();
  const escs = (openEscs || []).filter((e) => e.run_id === run.id);
  const esc = escs.find((e) => e.kind !== "migration") || null;
  const migrationEsc = escs.find((e) => e.kind === "migration") || null;
  const decisions = audit.filter((a) => a.step === "decide_tier");
  const confirmed = decisions.find((a) => a.status === "confirmed");
  const connected = decisions.filter((a) => a.status !== "no_answer");
  const manual = lastAudit(audit, "resolve", (a) => a.input && a.input.action === "set_tier" && a.input.tier);
  const tier = run.plan_tier || (confirmed && confirmed.output && confirmed.output.tier) || (manual && manual.input.tier) || "";
  const ae = aeOf(audit);
  const onCall = (pending && (pending.type === "call" || pending.type === "wait")) ||
    status === "AWAITING_CALL" || status === "AWAITING_RETRY";
  const has = (step) => audit.some((a) => a.step === step && a.status !== "failed");
  const statusIdx = { RECEIVED: 1, AWAITING_CLARIFICATION: 1, AWAITING_CALL: 2, AWAITING_RETRY: 2, TIER_CONFIRMED: 4,
    PROJECT_CREATED: 5, COMPLETED: 6, HANDED_OFF: 7 }[status];

  const done = [
    true,
    has("validate") && !lastAudit(audit, "validate", (a) => a.status !== "ok") || has("lookup_ae") || decisions.length > 0 || !!tier,
    !onCall && (connected.length > 0 || !!tier),
    !!tier,
    !!run.rocketlane_project_id || has("create_project"),
    !!run.slack_channel_id || has("setup_channel"),
    status === "HANDED_OFF" || !!(tr && tr.handed_off_at),
  ];
  if (!audit.length && statusIdx !== undefined) for (let i = 0; i < 7; i++) done[i] = i < statusIdx;  // list fallback
  if (onCall) done[2] = false;
  let cur = done.indexOf(false);
  if (onCall) cur = 2;
  if (cur < 0) cur = 7;

  const steps = STEPS.map(([key, label], i) => ({ key, label, state: done[i] ? "done" : "todo", sub: "" }));
  // subtitles for finished steps
  const st = lastAudit(audit, "start");
  steps[0].sub = st ? fmtTime(st.ts) : fmtTime(run.created_at);
  if (done[1]) steps[1].sub = "Sender and fields OK";
  if (done[2]) steps[2].sub = !connected.length && manual ? "Skipped by a person" : ae ? `Spoke with ${ae}` : "Call connected";
  if (done[3]) steps[3].sub = manual && !confirmed ? `${cap(tier)}, set by ${manual.input.by || "a person"}` : cap(tier);
  if (done[4]) steps[4].sub = run.rocketlane_project_id ? `Project #${run.rocketlane_project_id}` : "Created";
  if (done[5]) steps[5].sub = has("slack_connect") ? "Ready (invite customer by hand)" : "Channel ready";
  if (done[6]) steps[6].sub = "Handed off to support";

  const pct = trackingPct(tr);
  const reason = (esc && esc.reason) || (pending && pending.reason) || (lastAudit(audit, "escalate") || {}).rationale || "";
  let tone = "active", title = "Working on it", sub = "";
  const c = cur < 7 ? steps[cur] : null;

  if (status === "FAILED") {
    tone = "bad"; title = "Stopped with an error"; sub = run.error || "";
    if (c) { c.state = "bad"; c.sub = "Error"; }
  } else if (status === "CANCELLED") {
    tone = "idle"; title = "Cancelled"; sub = "Someone cancelled this run. No project was created after that point.";
    if (c) { c.state = "idle"; c.sub = "Cancelled"; }
  } else if (status === "ESCALATED" || (esc && pending && pending.type === "escalation")) {
    const kind = (esc && esc.kind) || (pending && pending.kind) || "system";
    tone = "warn"; title = `Needs a human: ${(KIND[kind] || "review").toLowerCase()}`; sub = reason;
    if (c) {
      c.state = "warn";
      if (kind === "tier" && cur === 2) c.sub = `No answer after ${decisions.length || "several"} ${decisions.length === 1 ? "try" : "tries"}`;
      else if (kind === "tier") c.sub = "Tier unclear on the call";
      else c.sub = KIND[kind] || "Needs review";
    }
  } else if (status === "AWAITING_CLARIFICATION") {
    tone = "warn"; title = "Waiting for the AE to send missing details";
    const v = lastAudit(audit, "validate");
    const issues = v && v.output && v.output.issues;
    sub = Array.isArray(issues) && issues.length ? `Missing or invalid: ${issues.map(str).join(", ")}` : "We emailed the AE and will continue when they reply.";
    if (c) { c.state = "warn"; c.sub = "Asked AE for details"; }
  } else if (onCall) {
    const attempt = (pending && pending.attempt) || decisions.length + 1;
    if (pending && pending.type === "wait") {
      const until = typeof pending.until === "number" ? new Date(pending.until * 1000).toISOString() : pending.until;
      title = "Call not answered. Trying again soon";
      sub = until ? `Next attempt at ${fmtTime(until)}` : "The next attempt is scheduled.";
      steps[2].sub = "Retrying";
    } else {
      title = `Calling ${ae || "the AE"} to confirm the plan tier`;
      sub = `Attempt ${attempt}. This page updates as soon as the call ends.`;
      steps[2].sub = `Ringing, attempt ${attempt}`;
    }
    steps[2].state = "active";
  } else if (status === "HANDED_OFF" || cur === 7) {
    tone = "ok"; title = "Handed off to support";
    sub = tr && tr.handed_off_at ? `Handed off ${fmtTime(tr.handed_off_at, true)}` : "All phases complete.";
  } else if (status === "COMPLETED" || cur === 6) {
    if (migrationEsc) {
      tone = "warn"; title = "Onboarding running. Migration needs review"; sub = migrationEsc.reason || "";
      steps[6].state = "warn"; steps[6].sub = "Migration check failed";
    } else if (tr) {
      tone = "ok"; title = tr.current_phase ? `In onboarding: ${tr.current_phase} phase` : "In onboarding";
      sub = `${pct}% of tasks done. Last synced ${ago(tr.last_synced_at || tr.synced_at) || "recently"}.`;
      steps[6].state = "active"; steps[6].sub = `${pct}% done`;
    } else {
      tone = "ok"; title = "Onboarding is set up"; sub = "Rocketlane project and Slack channel are ready.";
      steps[6].sub = "Starts at next sync";
    }
  } else if (c) {
    c.state = "active"; c.sub = "In progress";
    title = `Working: ${c.label.toLowerCase()}`;
  }
  // connectors: a step is "reached" when the one before it is done
  steps.forEach((s, i) => { s.reached = i > 0 && steps[i - 1].state === "done"; });
  return { steps, tone, title, sub, tier, ae, esc, migrationEsc, pct, tracking: tr, name: nameOf(run, d) };
}

function trackingPct(tr) {
  if (!tr) return 0;
  let p = tr.percent_done ?? tr.percent_complete ?? tr.progress_pct ?? tr.progress;
  if ((p === undefined || p === null) && tr.tasks_total) p = (100 * (tr.tasks_done || 0)) / tr.tasks_total;
  p = Number(p);
  if (!isFinite(p)) return 0;
  if (p > 0 && p < 1) p *= 100;
  return Math.round(Math.max(0, Math.min(100, p)));
}

function groupOf(run, info) {
  if (info.esc || info.migrationEsc) return "attn";
  return (STATUS[String(run.status || "").toUpperCase()] || [0, 0, "active"])[2];
}

// ---------- plain-language activity ----------
const TONE = { ok: "ok", escalated: "warn", blocked: "warn", no_answer: "warn", fallback: "warn", degraded: "warn",
  unclear: "warn", failed: "bad", error: "bad", ignored: "idle", confirmed: "ok" };

function describe(a) {
  const s = String(a.status || "ok").toLowerCase();
  const step = String(a.step || "");
  const tone = TONE[s] || "info";
  const ok = s === "ok";
  if (step.startsWith("tool:")) return { title: step.slice(5), tone: ok ? "tech" : tone, tech: true };
  const out = a.output || {};
  const t = (title, tn) => ({ title, tone: tn || tone });
  switch (step) {
    case "start": return t("Deal email received", "info");
    case "check_sender": return t("Sender is a known AE");
    case "extract_fields": return t("Read the deal details from the email");
    case "validate": return t(ok ? "All required details present" : s === "escalated" ? "Email covers more than one deal" : "Some details are missing or invalid");
    case "lookup_ae": return t("Found the AE's phone number");
    case "decide_tier": return t(s === "confirmed" ? `AE confirmed the ${cap(out.tier || "")} plan`.replace("  ", " ")
      : s === "no_answer" ? "Call not answered" : "Tier was not clear on the call");
    case "voice_webhook": return t(s === "ignored" ? "Ignored a stale call result" : "Call ended, result received", s === "ignored" ? "idle" : "info");
    case "check_duplicate": return t("No duplicate project found");
    case "create_project": return t("Rocketlane project created");
    case "draft_welcome": return t(s === "fallback" ? "Used the standard welcome message" : "Welcome message drafted");
    case "check_welcome": return t("Draft welcome rejected by fact check");
    case "slack_connect": return t("Customer invite needs a manual step");
    case "setup_channel": return t("Slack channel ready, welcome posted");
    case "complete": return t("Onboarding set up", "ok");
    case "escalate_request": return { title: "escalate_request", tone: "tech", tech: true };
    case "escalate": return t("Sent to a human for review", "warn");
    case "resolve": return t("A person made a decision", "info");
    case "failed": return t("Stopped with an error", "bad");
    case "dedupe": return t("Duplicate email ignored", "idle");
    case "sync": return t("Progress synced from Rocketlane", "info");
    case "phase_complete": return t(ok ? "Phase completed" : "Phase announcement had a problem");
    case "migration_check": return t(ok ? "Migration check passed" : "Migration check found problems", ok ? "ok" : "bad");
    case "migration_verified_accepted": return t("Migration sign-off accepted");
    case "reopen_note": return t("Could not write the reopen note");
    case "handoff": return t(ok ? "Handed off to support" : s === "blocked" ? "Handoff held back" : "Handoff note failed");
    case "draft_handoff": return t(s === "fallback" ? "Used the standard handoff summary" : "Handoff summary drafted");
    case "check_handoff": return t("Handoff draft rejected by fact check");
    case "dismiss_migration_escalation": return t("Migration issue dismissed by a person", "info");
    default: return t(human(step));
  }
}

const ICON = { ok: "✓", warn: "!", bad: "!", info: "•", idle: "-", tech: "·" };

function feedItem(a, fresh) {
  const d = describe(a);
  const why = a.rationale && a.rationale !== "tool call succeeded" ? a.rationale : "";
  const tech = [];
  if (a.input !== null && a.input !== undefined) tech.push(el("div", { class: "small muted" }, "Input"), json(a.input));
  if (a.output !== null && a.output !== undefined) tech.push(el("div", { class: "small muted" }, "Output"), json(a.output));
  return el("li", { class: `${d.tone}${d.tech ? " tech" : ""}${fresh ? " fresh" : ""}` },
    el("span", { class: "ic", "aria-hidden": "true" }, ICON[d.tone] || "•"),
    el("div", { class: "feed-head" },
      el("span", { class: "feed-title" }, d.title),
      el("span", { class: "time", title: a.ts || "" }, fmtTime(a.ts))),
    why && !d.tech ? el("div", { class: "feed-why" }, why) : null,
    el("details", {}, el("summary", {}, "Details"),
      el("dl", { class: "kv" }, el("dt", {}, "Step"), el("dd", { class: "mono" }, a.step || ""),
        el("dt", {}, "Agent"), el("dd", {}, a.agent || ""), el("dt", {}, "Status"), el("dd", {}, a.status || "ok"),
        why && d.tech ? [el("dt", {}, "Why"), el("dd", {}, why)] : null),
      tech));
}

// ---------- pagination ----------
const PAGE_SIZES = [5, 10, 25];

// Rows-per-page select + prev/next + "1-10 of 34". The size is remembered per list.
function pager(key, onChange) {
  let size = Number(getPref(`dash.pp.${key}`, "10"));
  if (!PAGE_SIZES.includes(size)) size = 10;
  const sel = el("select", { "aria-label": "Rows per page", onchange: () => {
    size = Number(sel.value); setPref(`dash.pp.${key}`, String(size)); onChange("size");
  } }, PAGE_SIZES.map((n) => el("option", { value: String(n), selected: n === size }, String(n))));
  const range = el("span", { class: "range" });
  const pg = el("span", { class: "pg" });
  const prev = el("button", { class: "btn icon", type: "button", "aria-label": "Previous page", onclick: () => onChange(-1) }, "‹");
  const next = el("button", { class: "btn icon", type: "button", "aria-label": "Next page", onclick: () => onChange(1) }, "›");
  const root = el("div", { class: "pager" }, el("label", { class: "pp" }, "Rows per page", sel), range,
    el("div", { class: "pager-nav" }, prev, pg, next));
  return {
    root,
    size: () => size,
    // Returns the clamped page index.
    update(total, page) {
      const pages = Math.max(1, Math.ceil(total / size));
      page = Math.max(0, Math.min(page, pages - 1));
      range.textContent = total ? `${page * size + 1}-${Math.min(total, (page + 1) * size)} of ${total}` : "";
      pg.textContent = `${page + 1} / ${pages}`;
      prev.disabled = page <= 0;
      next.disabled = page >= pages - 1;
      root.hidden = total === 0;
      return page;
    },
  };
}
const turn = (page, delta) => (delta === "size" ? 0 : page + delta);

// ---------- routing ----------
function route() {
  const h = location.hash.replace(/^#\/?/, "");
  const [name, id] = h.split("/");
  if (name === "runs" && id) return { name: "run", id: decodeURIComponent(id) };
  if (name === "escalations") return { name: "escalations", id: id ? decodeURIComponent(id) : null };
  if (name === "runs" || name === "ai" || name === "tools") return { name };
  return { name: "overview" };
}

let drawerBack = false;  // the drawer was opened from the inbox, so closing it can use history.back()

function navigate() {
  const prev = view;
  view = route();
  const drawerOnly = !!(prev && prev.name === "escalations" && view.name === "escalations");
  if (view.name === "run") backTo = prev && prev.name !== "run" ? `#/${prev.name}` : backTo;
  else backTo = null;
  if (view.name === "escalations" && view.id) drawerBack = drawerOnly && !prev.id;
  transition = drawerOnly || !prev ? null : view.name === "run" ? "fwd" : prev.name === "run" ? "back" : "fwd";
  if (!drawerOnly) { ui = null; document.body.classList.remove("locked"); }  // a side panel left open must not lock the next page
  setTitle();
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const on = a.dataset.nav === (view.name === "run" ? "runs" : view.name);
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (!prev) app.replaceChildren(el("div", { class: "rows" }, [0, 1, 2, 3].map(() => el("div", { class: "skel" }))));
  refresh(true);
}

const TITLES = { overview: "Overview", runs: "Onboarding runs", escalations: "Needs a human", run: "Run details",
  ai: "AI usage", tools: "Tool calls" };
const CRUMBS = { "#/escalations": "Needs a human", "#/overview": "Overview", "#/ai": "AI usage", "#/tools": "Tool calls" };

function setTitle() {
  const title = TITLES[view.name] || "";
  document.getElementById("page-title").textContent = title;
  document.title = `${title} | NovaCRM Onboarding`;
  const crumb = document.getElementById("crumb");
  crumb.hidden = view.name !== "run";
  crumb.textContent = `‹ ${CRUMBS[backTo] || "Runs"}`;
}

function goBack() {
  if (backTo) history.back();
  else location.hash = "#/runs";
}

function closeDrawer() {
  if (drawerBack) history.back();
  else location.hash = "#/escalations";
}

// Swap the page content, with a view transition between routes when the browser supports it.
function paint(fn) {
  const dir = transition;
  transition = null;
  if (!dir || !document.startViewTransition || reduceMotion.matches) { fn(); return; }
  document.documentElement.dataset.dir = dir;
  document.startViewTransition(() => { fn(); window.scrollTo(0, 0); });
}

async function refresh(force) {
  if (!force && (document.hidden || inflight)) return;
  while (inflight) await inflight.catch(() => {});
  inflight = load();
  try { await inflight; } finally { inflight = null; }
}

async function load() {
  const v = view;
  try {
    const escs = await api("/escalations?status=open");
    updateEscCount(escs);
    if (v !== view) return;
    if (v.name === "run") {
      let data;
      try { data = await api(`/runs/${enc(v.id)}`); } catch (e) {
        if (v !== view) return;
        if (e.status === 404) { paint(() => notFound()); setLive(true); return; }
        throw e;
      }
      if (v !== view) return;
      cache.set(v.id, { sig: data.run.updated_at, at: Date.now(), data });
      paint(() => renderRun(data, escs));
    } else if (OBS[v.name]) {
      const data = await OBS[v.name].load();
      if (v !== view) return;
      paint(() => OBS[v.name].render(data, escs));
    } else {
      const runs = await api("/runs");
      if (v.name === "escalations" && v.id) {  // the open drawer always gets fresh detail
        const d = await api(`/runs/${enc(v.id)}`).catch(() => null);
        if (d) cache.set(v.id, { sig: d.run.updated_at, at: Date.now(), data: d });
      }
      await fillCache(runs);
      if (v !== view) return;
      paint(() => (v.name === "runs" ? renderRuns(runs, escs) : renderEscalations(runs, escs)));
    }
    setLive(true);
  } catch (e) {
    setLive(false, "Refresh failed");
    if (!app.querySelector(".panel, .row, .inbox-item, .kpi")) app.replaceChildren(errorBox(e));
  }
}

// The list needs each run's detail for its name and pipeline; only refetch what changed (or is still moving).
async function fillCache(runs) {
  const now = Date.now();
  const stale = runs.slice(0, 60).filter((r) => {
    const c = cache.get(r.id);
    if (!c || c.sig !== r.updated_at) return true;
    const st = String(r.status || "").toUpperCase();
    return !TERMINAL.has(st) && now - c.at > (st === "COMPLETED" ? 30000 : 9000);
  });
  await Promise.all(stale.map((r) => api(`/runs/${enc(r.id)}`)
    .then((d) => cache.set(r.id, { sig: r.updated_at, at: now, data: d }))
    .catch(() => { /* row falls back to status only */ })));
}

function updateEscCount(escs) {
  const c = document.getElementById("esc-count");
  c.hidden = !escs.length;
  if (c.textContent !== String(escs.length)) c.textContent = String(escs.length);
}

// ---------- runs list ----------
const FILTERS = [{ value: "all", label: "All" }, { value: "attn", label: "Needs a human" },
  { value: "active", label: "In progress" }, { value: "done", label: "Done" }];

function renderRuns(runs, escs) {
  if (!ui || ui.kind !== "runs") {
    const filter = getPref("dash.filter", "all");
    ui = { kind: "runs", cards: new Map(), filter, first: true, page: 0 };
    ui.list = el("div", { class: "rows" });
    ui.empty = el("div", { class: "panel empty", hidden: true });
    ui.seg = seg(FILTERS, filter, (f) => { ui.filter = f; ui.page = 0; setPref("dash.filter", f); applyFilter(); });
    ui.pager = pager("runs", (d) => { ui.page = turn(ui.page, d); applyFilter(); });
    app.replaceChildren(
      el("div", { class: "page-head" },
        el("p", {}, "Each closed-won deal email starts a run. Open one to follow it step by step."),
        ui.seg),
      el("div", { class: "list-panel" },
        el("div", { class: "list-head", "aria-hidden": "true" },
          el("span", {}, "Customer"), el("span", {}, "Status"), el("span", {}, "Pipeline"), el("span", {}, "Updated")),
        ui.list, ui.empty, ui.pager.root));
  }
  const counts = { all: runs.length, attn: 0, active: 0, done: 0 };
  const ids = new Set();
  runs.forEach((r, i) => {
    ids.add(r.id);
    const d = cache.get(r.id);
    const info = derive(r, d && d.data, escs);
    let card = ui.cards.get(r.id);
    if (!card) {
      card = makeRow(r.id, ui.first ? i : null);
      ui.cards.set(r.id, card);
    }
    updateRow(card, r, info);
    card.group = groupOf(r, info);
    counts[card.group] = (counts[card.group] || 0) + 1;
    if (ui.list.children[i] !== card.root) ui.list.insertBefore(card.root, ui.list.children[i] || null);
  });
  for (const [id, card] of ui.cards) if (!ids.has(id)) { card.root.remove(); ui.cards.delete(id); }
  for (const f of ["all", "attn", "active", "done"]) ui.seg.setCount(f, counts[f], f === "attn" && counts.attn > 0);
  ui.total = runs.length;
  ui.first = false;
  applyFilter();
}

function applyFilter() {
  const order = [...ui.list.children].map((n) => [...ui.cards.values()].find((c) => c.root === n)).filter(Boolean);
  const match = order.filter((c) => ui.filter === "all" || c.group === ui.filter);
  const size = ui.pager.size();
  ui.page = ui.pager.update(match.length, ui.page);
  const shown = new Set(match.slice(ui.page * size, (ui.page + 1) * size));
  for (const c of order) c.root.hidden = !shown.has(c);
  ui.empty.hidden = shown.size > 0;
  if (!shown.size) {
    fill(ui.empty, ...(ui.total
      ? [el("strong", {}, "Nothing here right now"), "Try another filter."]
      : [el("strong", {}, "No runs yet"), "Forward a closed-won deal email to the CS inbox to start one."]));
  }
}

function makeRow(id, staggerIndex) {
  const c = {
    chip: el("span"), time: el("span", { class: "time" }), name: el("h3"), meta: el("div", { class: "card-meta" }),
    mini: el("div", { class: "mini", "aria-hidden": "true" }, STEPS.map(() => el("span"))), now: el("div", { class: "card-now" }),
  };
  c.root = el("a", { class: `row${staggerIndex === null ? " new" : ""}`, href: `#/runs/${enc(id)}` },
    el("div", { class: "r-main" }, c.name, c.meta), el("div", { class: "r-state" }, c.chip),
    el("div", { class: "r-pipe" }, c.mini, c.now), c.time);
  if (staggerIndex !== null) c.root.style.setProperty("--i", String(Math.min(staggerIndex, 12)));
  return c;
}

function updateRow(c, run, info) {
  const ch = statusChip(run, info);
  if (c.chip.textContent !== ch.textContent || c.chip.className !== ch.className) {
    c.chip.className = ch.className; c.chip.textContent = ch.textContent;
  }
  c.time.textContent = ago(run.updated_at || run.created_at);
  c.time.title = `Received ${fmtTime(run.created_at, true)}`;
  if (c.name.textContent !== info.name) c.name.textContent = info.name;
  const metaSig = `${info.tier}|${run.opportunity_id || ""}`;
  if (c.metaSig !== metaSig) {
    c.metaSig = metaSig;
    fill(c.meta, tierChip(info.tier) || el("span", { class: "small muted" }, "Tier pending"));
    c.meta.title = run.opportunity_id ? `Opportunity ${run.opportunity_id}` : "";
  }
  info.steps.forEach((s, i) => {
    const cls = s.state === "todo" ? "" : s.state;
    if (c.mini.children[i].className !== cls) c.mini.children[i].className = cls;
  });
  c.mini.title = info.steps.map((s) => `${s.label}: ${s.state === "todo" ? "not yet" : s.state}`).join("\n");
  const hot = info.tone === "warn" || info.tone === "bad";
  const nowText = hot && info.sub ? `${info.title}. ${info.sub}` : info.title;
  if (c.now.textContent !== nowText) c.now.textContent = nowText;
  c.now.className = `card-now ${hot ? info.tone : ""}`;
  c.root.classList.toggle("attn", hot);
  c.root.classList.toggle("warnish", info.tone === "warn");
  c.root.setAttribute("aria-label", `${info.name}: ${ch.textContent}. ${info.title}`);
}

// ---------- escalations inbox + side drawer ----------
function renderEscalations(runs, escs) {
  const byId = Object.fromEntries(runs.map((r) => [r.id, r]));
  const sorted = [...escs].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  if (!ui || ui.kind !== "esc") {
    ui = { kind: "esc", page: 0, sigs: {}, msg: {}, times: [] };
    ui.list = el("div", { class: "inbox" });
    ui.pager = pager("esc", (d) => { ui.page = turn(ui.page, d); ui.sigs.list = null; renderInbox(ui.last.byId, ui.last.sorted); });
    ui.scrim = el("div", { class: "scrim", onclick: closeDrawer });
    ui.drawer = el("aside", { class: "drawer", "aria-label": "Escalation", tabindex: "-1" });
    app.replaceChildren(
      el("div", { class: "page-head" },
        el("p", {}, "Runs waiting for a decision. Open one to resolve it.")),
      ui.list, ui.pager.root, ui.scrim, ui.drawer);
  }
  ui.last = { byId, sorted };
  renderInbox(byId, sorted);
  renderDrawer(byId, escs);
}

function renderInbox(byId, sorted) {
  const size = ui.pager.size();
  ui.page = ui.pager.update(sorted.length, ui.page);
  const pageItems = sorted.slice(ui.page * size, (ui.page + 1) * size);
  const sig = [pageItems.map((e) => [e.key, e.reason, nameOf(byId[e.run_id] || {}, (cache.get(e.run_id) || {}).data)]),
    view.id, sorted.length];
  if (!changed("list", sig)) {
    ui.times.forEach(([n, iso]) => { n.textContent = `Raised ${ago(iso)}`; });
    return;
  }
  ui.times = [];
  const items = pageItems.map((e, i) => {
    const r = byId[e.run_id] || { id: e.run_id };
    const t = el("span", { class: "time", title: fmtTime(e.created_at, true) }, `Raised ${ago(e.created_at)}`);
    ui.times.push([t, e.created_at]);
    const item = el("a", { class: `inbox-item${view.id === e.run_id ? " current" : ""}`, href: `#/escalations/${enc(e.run_id)}`,
      "aria-current": view.id === e.run_id ? "true" : null },
    el("span", { class: "ic", "aria-hidden": "true" }, "!"),
    el("div", {},
      el("div", { class: "row-line" }, el("h3", {}, nameOf(r, (cache.get(e.run_id) || {}).data)),
        chip(KIND[e.kind] || "Needs review", "warn"), t),
      el("div", { class: "reason" }, e.reason || "")),
    el("span", { class: "go" }, "Decide →"));
    if (ui.sigs.listSeen) item.style.animation = "none";
    else item.style.setProperty("--i", String(Math.min(i, 10)));
    return item;
  });
  ui.sigs.listSeen = true;
  fill(ui.list, items.length ? items
    : el("div", { class: "panel empty" }, el("strong", {}, "All clear"), "Nothing needs a human right now."));
}

function renderDrawer(byId, escs) {
  const id = view.id;
  const open = !!id;
  ui.drawer.classList.toggle("open", open);
  ui.scrim.classList.toggle("open", open);
  document.body.classList.toggle("locked", open);
  if (!open) { ui.sigs.esc = null; return; }
  const d = (cache.get(id) || {}).data;
  const run = (d && d.run) || byId[id] || { id };
  const pending = d && d.pending;
  const esc = escs.find((e) => e.run_id === id && e.kind !== "migration") || escs.find((e) => e.run_id === id);
  const info = derive(run, d, escs);
  const sig = [id, esc && esc.key, esc && esc.reason, pending && pending.type, pending && pending.kind,
    !!run.rocketlane_project_id, !!d, info.name];
  if (!changed("esc", sig)) return;
  const msg = ui.msg.esc || (ui.msg.esc = el("div", { "aria-live": "polite" }));
  const kind = esc ? esc.kind || (pending && pending.kind) || "system" : null;
  const vstep = el("ol", { class: "stepper vertical", "aria-label": "Progress" }, info.steps.map((s, i) => el("li",
    { class: `step ${s.state}${s.reached ? " reached" : ""}` },
    el("span", { class: "dot", "aria-hidden": "true" }, { done: "✓", warn: "!", bad: "!", idle: "×" }[s.state] || String(i + 1)),
    el("span", { class: "step-label" }, s.label), el("span", { class: "step-sub" }, s.sub))));
  const wasOpen = ui.drawer.childElementCount > 0;
  fill(ui.drawer,
    el("div", { class: "drawer-head" },
      el("div", {}, kind ? chip(KIND[kind] || "Needs review", "warn") : chip("Resolved", "ok"),
        el("h2", {}, info.name)),
      el("button", { class: "btn icon", type: "button", "aria-label": "Close", onclick: closeDrawer }, "×")),
    el("div", { class: "drawer-body" },
      esc ? el("div", { class: "reason" }, esc.reason || "")
        : msg.childElementCount ? null : el("div", { class: "notice" }, "This escalation is closed. Nothing left to decide here."),
      esc ? (kind === "migration" ? migrationActions(run) : d ? resolveForm(run, kind, pending) : el("div", { class: "working" }, "Loading…")) : null,
      msg,
      el("details", { class: "drawer-steps", open: !esc }, el("summary", {}, "Where this run is"), vstep),
      el("a", { class: "btn", href: `#/runs/${enc(id)}` }, "Open full run →")));
  if (!wasOpen) ui.drawer.focus({ preventScroll: true });
}

// ---------- run detail ----------
function notFound() {
  ui = null;
  app.replaceChildren(el("div", { class: "panel empty" }, el("strong", {}, "Run not found"),
    "It may have been removed, or the link is wrong.", el("div", { class: "actions center" },
      el("a", { class: "btn", href: "#/runs" }, "Go to all runs"))));
}

const TABS = [{ value: "activity", label: "Activity" }, { value: "progress", label: "Project progress" },
  { value: "migration", label: "Migration check" }, { value: "tech", label: "AI & tools" }];

function renderRun(data, escs) {
  const run = data.run;
  const runEscs = escs.filter((e) => e.run_id === run.id);
  const info = derive(run, data, runEscs);
  if (!ui || ui.kind !== "run" || ui.id !== run.id) {
    ui = { kind: "run", id: run.id, sigs: {}, msg: {}, feedPage: 0, feedSeen: 0 };
    ui.feedMode = getPref("dash.feed", "key");
    ui.hero = el("section", { class: "panel hero" });
    ui.stepper = el("ol", { class: "stepper", "aria-label": "Progress" }, STEPS.map(([, label], i) => el("li", { class: "step todo" },
      el("span", { class: "dot", "aria-hidden": "true" }, String(i + 1)), el("span", { class: "step-label" }, label),
      el("span", { class: "step-sub" }))));
    ui.escalation = el("div");
    ui.feed = el("ol", { class: "feed" });
    ui.feedPager = pager("feed", (d) => { ui.feedPage = turn(ui.feedPage, d); renderFeed(); });
    ui.progress = el("div");
    ui.migration = el("div");
    ui.behind = el("div");
    const panes = {
      activity: el("div", { class: "pane", role: "tabpanel" },
        el("div", { class: "pane-head" },
          el("span", { class: "small muted" }, "Newest first"),
          seg([{ value: "key", label: "Key events" }, { value: "all", label: "Everything" }], ui.feedMode, (m) => {
            ui.feedMode = m; ui.feedPage = 0; setPref("dash.feed", m); renderFeed();
          })),
        ui.feed, ui.feedPager.root),
      progress: el("div", { class: "pane", role: "tabpanel" }, ui.progress),
      migration: el("div", { class: "pane", role: "tabpanel" }, ui.migration),
      tech: el("div", { class: "pane", role: "tabpanel" }, ui.behind),
    };
    const show = (t) => { for (const [k, p] of Object.entries(panes)) p.hidden = k !== t; };
    let tab = getPref("dash.tab", "activity");
    if (!panes[tab]) tab = "activity";
    ui.tabs = seg(TABS, tab, (t) => { setPref("dash.tab", t); show(t); }, "tabs");
    ui.tabs.setAttribute("role", "tablist");
    show(tab);
    app.replaceChildren(
      ui.hero,
      el("section", { class: "panel" }, ui.stepper),
      ui.escalation,
      el("section", { class: "panel tabbed" }, ui.tabs, Object.values(panes)));
  }
  renderHero(run, info);
  renderStepper(info);
  renderEscalation(run, data.pending, runEscs);
  ui.lastAudit = data.audit || [];
  renderFeed();
  renderProgress(run, info);
  renderMigration(run, data.migration_checks || []);
  renderBehind(run, data);
}

function changed(key, value) {
  const sig = JSON.stringify(value);
  if (ui.sigs[key] === sig) return false;
  ui.sigs[key] = sig;
  return true;
}

function renderHero(run, info) {
  const sig = [info.name, info.tone, info.title, info.sub, info.tier, run.status, run.rocketlane_project_id,
    run.slack_channel_id, run.opportunity_id, run.error, !!info.migrationEsc];
  if (!changed("hero", sig)) return;
  const ICONS = { ok: "✓", warn: "!", bad: "!", idle: "×", active: "…" };
  fill(ui.hero,
    el("div", { class: "hero-id" },
      el("div", { class: "card-top" }, statusChip(run, info), el("span", { class: "time" }, `Received ${fmtTime(run.created_at, true)}`)),
      el("h2", { class: "hero-name" }, info.name),
      el("div", { class: "hero-meta" },
        tierChip(info.tier),
        projectLink(run.rocketlane_project_id),
        run.slack_channel_id ? el("span", {}, "Slack ", el("span", { class: "mono" }, run.slack_channel_id)) : null,
        run.opportunity_id ? el("span", {}, "Opp ", el("span", { class: "mono" }, run.opportunity_id)) : null)),
    el("div", { class: `now ${info.tone}`, role: "status" },
      el("span", { class: "icon", "aria-hidden": "true" }, ICONS[info.tone] || "…"),
      el("div", {}, info.title, info.sub ? el("span", { class: "sub" }, info.sub) : null)));
  ui.tabs.setCount("migration", info.migrationEsc ? "Fail" : undefined, !!info.migrationEsc);
}

function renderStepper(info) {
  const marks = { done: "✓", warn: "!", bad: "!", idle: "×" };
  info.steps.forEach((s, i) => {
    const li = ui.stepper.children[i];
    const cls = `step ${s.state}${s.reached ? " reached" : ""}`;
    if (li.className !== cls) li.className = cls;
    const mark = marks[s.state] || String(i + 1);
    if (li.children[0].textContent !== mark) li.children[0].textContent = mark;
    if (li.children[2].textContent !== s.sub) li.children[2].textContent = s.sub;
    if (s.state === "active") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  });
}

// Activity feed: newest first, paginated; rebuilt only when entries, filter or page change.
function renderFeed() {
  const rows = ui.lastAudit || [];
  const all = ui.feedMode === "all";
  const items = rows.map((r, i) => [r, i]).filter(([r]) => all || !describe(r).tech).reverse();
  const size = ui.feedPager.size();
  ui.feedPage = ui.feedPager.update(items.length, ui.feedPage);
  if (!changed("feed", [rows.length, ui.feedMode, ui.feedPage, size])) return;
  const seen = ui.feedSeen;
  ui.feedSeen = rows.length;
  const open = new Set([...ui.feed.querySelectorAll("li")].filter((li) => li.querySelector("details[open]")).map((li) => li.dataset.i));
  const pageItems = items.slice(ui.feedPage * size, (ui.feedPage + 1) * size).map(([r, i]) => {
    const li = feedItem(r, seen > 0 && i >= seen);
    li.dataset.i = String(i);
    if (open.has(li.dataset.i)) li.querySelector("details").open = true;
    return li;
  });
  fill(ui.feed, pageItems.length ? pageItems : el("li", { class: "idle" }, el("span", { class: "ic" }, "-"),
    el("span", { class: "feed-title muted" }, "Nothing has happened yet.")));
}

// ---------- escalation panel (rebuilt only when the escalation changes, so typed input survives refresh) ----------
function renderEscalation(run, pending, escs) {
  const box = ui.escalation;
  const open = escs.filter((e) => e.status === "open");
  if (!changed("esc", [open.map((e) => [e.key, e.kind, e.reason]), pending && pending.type, pending && pending.kind,
    !!run.rocketlane_project_id])) return;
  const msg = ui.msg.esc || (ui.msg.esc = el("div", { "aria-live": "polite" }));
  const panels = open.map((e) => {
    const kind = e.kind || (pending && pending.kind) || "system";
    return el("section", { class: "panel esc", "aria-label": "Needs your decision" },
      el("div", { class: "esc-info" },
        chip(KIND[kind] || "Needs review", "warn"),
        el("h2", {}, kind === "migration" ? "Migration data needs a look" : "Needs your decision"),
        el("div", { class: "reason" }, e.reason || ""),
        el("span", { class: "time" }, `Raised ${fmtTime(e.created_at, true)} by the ${e.agent ? `${e.agent} agent` : "agent"}`)),
      el("div", { class: "esc-act" }, kind === "migration" ? migrationActions(run) : resolveForm(run, kind, pending)));
  });
  fill(box, panels, msg);
}

function byField() {
  const i = el("input", { type: "text", value: getPref("dash.by", ""), autocomplete: "name", required: true });
  i.addEventListener("change", () => setPref("dash.by", i.value.trim()));
  return [el("label", { class: "field" }, "Your name", i), i];
}

// A destructive button that asks for a second click instead of a blocking dialog.
function armedButton(label, confirmLabel, action) {
  let timer = null;
  const b = el("button", { class: "btn danger", type: "button", onclick: (ev) => {
    if (!b.classList.contains("armed")) {
      b.classList.add("armed"); b.textContent = confirmLabel;
      timer = setTimeout(() => { b.classList.remove("armed"); b.textContent = label; }, 4000);
      return;
    }
    clearTimeout(timer); b.classList.remove("armed"); b.textContent = label;
    action(ev);
  } }, label);
  return b;
}

function resolveForm(run, kind, pending) {
  if (!pending || pending.type !== "escalation") {
    return el("div", { class: "small muted" }, "The run is not waiting on this escalation right now.");
  }
  const actions = (ALLOWED[kind] || ALLOWED.system).filter((a) => !(a === "set_tier" && run.rocketlane_project_id));
  const [byLabel, by] = byField();
  const note = el("input", { type: "text", placeholder: "Optional" });
  let tier = "";
  const out = el("div", { "aria-live": "polite" });
  const tierSeg = actions.includes("set_tier")
    ? seg(TIERS.map((t) => ({ value: t, label: cap(t) })), "", (t) => { tier = t; }, "big") : null;
  const send = async (a, btn) => {
    const body = { action: a, by: by.value.trim() };
    if (!body.by) { out.replaceChildren(errorBox(new Error("Enter your name first, so the audit log shows who decided."))); by.focus(); return; }
    if (a === "set_tier") {
      if (!tier) { out.replaceChildren(errorBox(new Error("Pick Enterprise or Growth first."))); return; }
      body.tier = tier;
    }
    if (note.value.trim()) body.note = note.value.trim();
    setPref("dash.by", body.by);
    const done = { set_tier: `Tier set to ${cap(tier)}. Creating the project now.`, retry: "Retrying now.", cancel: "Run cancelled." }[a];
    await post(btn, out, `/runs/${enc(run.id)}/resolve`, body, () => done);
  };
  const buttons = [];
  if (actions.includes("set_tier")) buttons.push(el("button", { class: "btn primary big", type: "button",
    onclick: (ev) => send("set_tier", ev.currentTarget) }, "Confirm tier and continue"));
  if (actions.includes("retry")) buttons.push(el("button", { class: `btn ${buttons.length ? "" : "primary big"}`, type: "button",
    onclick: (ev) => send("retry", ev.currentTarget) }, kind === "tier" ? "Call the AE again" : "Try again"));
  if (actions.includes("cancel")) buttons.push(armedButton("Cancel run", "Click again to cancel", (ev) => send("cancel", ev.currentTarget)));
  return el("div", {},
    tierSeg ? [el("div", { class: "label" }, "Which plan did the customer buy?"), tierSeg] : null,
    el("div", { class: "fields" }, byLabel, el("label", { class: "field" }, "Note", note)),
    el("div", { class: "actions" }, buttons),
    out);
}

function migrationActions(run) {
  const [byLabel, by] = byField();
  const reason = el("input", { type: "text", placeholder: "Why this failure is acceptable" });
  const out = el("div", { "aria-live": "polite" });
  return el("div", {},
    el("p", { class: "small muted", style: "max-width:65ch" },
      "Get the data fixed and run the check again. A passing check closes this. Or dismiss it with a reason."),
    el("div", { class: "actions" },
      el("button", { class: "btn primary big", type: "button", onclick: (ev) => runMigration(ev.currentTarget, out, run.id) },
        "Run the check again")),
    el("div", { class: "fields" }, byLabel, el("label", { class: "field" }, "Reason to dismiss", reason)),
    el("div", { class: "actions" }, el("button", { class: "btn danger", type: "button", onclick: async (ev) => {
      const body = { by: by.value.trim(), reason: reason.value.trim() };
      if (!body.by || !body.reason) { out.replaceChildren(errorBox(new Error("Both your name and a reason are required."))); return; }
      setPref("dash.by", body.by);
      await post(ev.currentTarget, out, `/runs/${enc(run.id)}/migration-escalation/dismiss`, body, () => "Migration issue dismissed.");
    } }, "Dismiss")),
    out);
}

async function post(btn, out, path, body, ok) {
  btn.disabled = true;
  out.replaceChildren(el("div", { class: "working" }, "Working…"));
  try {
    const r = await api(path, { method: "POST", body });
    const notice = el("div", { class: "notice", role: "status" }, ok(r || {}));
    out.replaceChildren(notice);
    if (ui && ui.sigs) ui.sigs.esc = null;
    if (ui && ui.kind === "esc" && view.id) cache.delete(view.id);
    await refresh(true);
    // The escalation panel may have been rebuilt (e.g. it closed); keep the result visible.
    if (!out.isConnected && ui && ui.msg && ui.msg.esc) ui.msg.esc.replaceChildren(notice);
  } catch (e) {
    out.replaceChildren(errorBox(e));
  } finally {
    btn.disabled = false;
  }
}

async function runMigration(btn, out, runId) {
  await post(btn, out, `/runs/${enc(runId)}/migration-check`, undefined, (r) =>
    r.passed ? "All migration checks passed." : "Some checks failed. See the Migration check tab.");
}

// ---------- project progress ----------
function renderProgress(run, info) {
  const box = ui.progress;
  const tr = info.tracking;
  if (!run.rocketlane_project_id && !tr) {
    if (changed("prog", "none")) fill(box, el("div", { class: "empty" }, el("strong", {}, "No project yet"),
      "Progress shows here once the Rocketlane project exists."));
    return;
  }
  const phases = tr && Array.isArray(tr.phases) ? tr.phases.filter((p) => p && p.name) : [];
  const out = ui.msg.sync || (ui.msg.sync = el("div", { "aria-live": "polite" }));
  if (changed("prog", [!!tr, phases.map((p) => p.name), tr && tr.handoff_summary, run.rocketlane_project_id])) {
    const syncBtn = run.rocketlane_project_id ? el("button", { class: "btn", type: "button",
      onclick: (ev) => post(ev.currentTarget, out, `/runs/${enc(run.id)}/sync`, {}, () => "Synced from Rocketlane.") }, "Sync now") : null;
    ui.prog = { ring: el("div", { class: "ring", role: "img" }, el("span")), title: el("strong"), sub: el("span", { class: "small muted" }),
      synced: el("span", { class: "small muted" }), bars: [] };
    const bars = phases.map((p) => {
      const b = { root: el("div", { class: "phase" }), n: el("span", { class: "n" }), fill: el("span") };
      b.root.append(el("div", { class: "phase-name" }, p.name), el("div", { class: "bar" }, b.fill), b.n);
      ui.prog.bars.push(b);
      return b.root;
    });
    fill(box,
      tr ? el("div", { class: "prog-wrap" },
        el("div", { class: "prog" }, ui.prog.ring, el("div", { class: "prog-text" }, ui.prog.title, ui.prog.sub)),
        bars.length ? el("div", { class: "phases" }, bars) : null)
        : el("div", { class: "empty" }, "Waiting for the first sync from Rocketlane."),
      tr && tr.handoff_summary ? [el("div", { class: "small muted", style: "margin-top:16px" }, "Handoff summary"),
        el("div", { class: "handoff" }, str(tr.handoff_summary))] : null,
      el("div", { class: "sync-row" }, ui.prog.synced, syncBtn), out);
  }
  if (!tr) return;
  const p = ui.prog;
  const pct = info.pct;
  requestAnimationFrame(() => {
    p.ring.style.setProperty("--p", String(pct));
    phases.forEach((ph, i) => {
      const b = p.bars[i];
      if (!b) return;
      const f = ph.total ? Math.max(0, Math.min(1, ph.done / ph.total)) : 0;
      b.fill.style.transform = `scaleX(${f})`;
      b.root.classList.toggle("current", ph.name === tr.current_phase);
    });
  });
  p.ring.setAttribute("aria-label", `${pct}% of tasks done`);
  p.ring.firstChild.textContent = `${pct}%`;
  p.title.textContent = tr.handed_off_at ? "All phases done" : tr.current_phase ? `${tr.current_phase} phase` : "All tasks done";
  p.sub.textContent = tr.tasks_total !== undefined ? `${tr.tasks_done ?? 0} of ${tr.tasks_total} tasks complete` : "";
  phases.forEach((ph, i) => { if (p.bars[i]) p.bars[i].n.textContent = `${ph.done ?? 0} of ${ph.total ?? 0} tasks`; });
  const synced = tr.last_synced_at || tr.synced_at;
  p.synced.textContent = synced ? `Synced ${ago(synced)}` : "Not synced yet";
}

// ---------- migration checks ----------
function renderMigration(run, checks) {
  if (!changed("mig", [checks.length, checks.length && checks[checks.length - 1].batch_id, !!run.rocketlane_project_id])) return;
  const out = ui.msg.mig || (ui.msg.mig = el("div", { "aria-live": "polite" }));
  if (!run.rocketlane_project_id && !checks.length) {
    fill(ui.migration, el("div", { class: "empty" }, el("strong", {}, "No project yet"),
      "The data migration check runs after the Rocketlane project exists."));
    return;
  }
  const lastBatch = checks.length ? checks[checks.length - 1].batch_id : null;
  const latest = checks.filter((c) => c.batch_id === lastBatch);
  const older = checks.filter((c) => c.batch_id !== lastBatch);
  const failed = latest.filter((c) => !c.passed).length;
  const tiles = latest.map((c, i) => {
    const d = c.details || {};
    let detail = "";
    if (d.expected !== undefined || d.actual !== undefined) detail = `Expected ${str(d.expected)}, found ${str(d.actual)}`;
    else if (Array.isArray(d.sample_ids) && d.sample_ids.length) detail = `e.g. rows ${d.sample_ids.slice(0, 4).map(str).join(", ")}`;
    const t = el("div", { class: `tile ${c.passed ? "" : "fail"}` },
      el("div", { class: "k" }, human(c.check_name)), el("div", { class: "v" }, c.passed ? "✓ Pass" : "! Fail"),
      detail ? el("div", { class: "d" }, detail) : null);
    t.style.setProperty("--i", String(i));
    return t;
  });
  const btn = run.rocketlane_project_id
    ? el("button", { class: "btn", type: "button", onclick: (ev) => runMigration(ev.currentTarget, out, run.id) }, "Run check") : null;
  fill(ui.migration,
    el("div", { class: "pane-head" },
      latest.length
        ? el("span", { class: `verdict ${failed ? "bad" : "ok"}` }, failed ? `${failed} of ${latest.length} checks failed` : `All ${latest.length} checks passed`,
          el("span", { class: "small muted" }, ` ${fmtTime(latest[0].ts, true)}`))
        : el("span", { class: "small muted" }, "No check has run yet. It runs by itself when the import task is completed in Rocketlane."),
      btn),
    out,
    latest.length ? [el("div", { class: "tiles" }, tiles),
      latest.some((c) => c.details && Object.keys(c.details).length)
        ? el("details", { style: "margin-top:12px" }, el("summary", {}, "Raw results"), json(latest.map((c) => ({ check: c.check_name, passed: c.passed, details: c.details })))) : null,
      older.length ? el("details", { style: "margin-top:8px" }, el("summary", {}, `${new Set(older.map((c) => c.batch_id)).size} earlier run(s)`),
        el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["When", "Check", "Result"].map((h) => el("th", {}, h)))),
          el("tbody", {}, older.slice().reverse().map((c) => el("tr", {}, el("td", {}, fmtTime(c.ts, true)), el("td", {}, human(c.check_name)),
            el("td", {}, chip(c.passed ? "Pass" : "Fail", c.passed ? "ok" : "bad")))))))) : null] : null);
}

// ---------- technical: LLM usage and tool calls ----------
function renderBehind(run, data) {
  const llm = data.llm || {};
  const calls = data.tool_calls || [];
  if (!changed("behind", [llm.calls, calls.length])) return;
  const body = ui.behind;
  const table = (heads, rows) => el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, heads.map((h) => el("th", {}, h)))), el("tbody", {}, rows)));
  const open = [...body.querySelectorAll("details")].map((d) => d.open);
  fill(body,
    el("div", { class: "stats" },
      el("div", { class: "stat" }, el("b", {}, String(llm.calls ?? 0)), el("small", {}, "LLM calls")),
      el("div", { class: "stat" }, el("b", {}, `${llm.input_tokens ?? 0} / ${llm.output_tokens ?? 0}`), el("small", {}, "Tokens in / out")),
      el("div", { class: "stat" }, el("b", {}, `$${Number(llm.cost_usd || 0).toFixed(4)}`), el("small", {}, "LLM cost"))),
    el("div", { class: "chips", style: "margin:10px 0 4px" }, "Models used: ",
      [...new Set((llm.by_node || []).map((r) => r.model).filter(Boolean))].map((m) => chip(m, "active"))),
    el("h3", { class: "section-title" }, `LLM calls (${(llm.by_node || []).length})`),
    (llm.by_node || []).length ? table(["Time", "Agent / step", "Model", "Prompt", "Tokens in / out", "Latency", "Cost", "Stop"],
      llm.by_node.map((r) => el("tr", {},
        el("td", {}, fmtTime(r.ts)), el("td", {}, `${r.agent || ""} / ${r.node || ""}`), el("td", { class: "mono" }, r.model || ""),
        el("td", { class: "mono" }, r.prompt_version || ""), el("td", {}, `${r.input_tokens ?? 0} / ${r.output_tokens ?? 0}`),
        el("td", {}, r.latency_ms != null ? `${(r.latency_ms / 1000).toFixed(1)} s` : ""),
        el("td", {}, r.cost_usd != null ? `$${Number(r.cost_usd).toFixed(5)}` : ""), el("td", { class: "small" }, r.stop_reason || ""))))
      : el("p", { class: "small muted" }, "No LLM calls yet."),
    el("h3", { class: "section-title" }, `Tool calls (${calls.length})`),
    calls.length ? table(["Time", "Agent", "Tool", "Result", "Try", ""], calls.map((t) => el("tr", {},
      el("td", {}, fmtTime(t.ts)), el("td", {}, t.agent || ""), el("td", { class: "mono" }, t.tool || ""),
      el("td", {}, chip(t.status || "", t.status === "ok" ? "ok" : "bad")), el("td", {}, t.attempt != null ? String(t.attempt) : ""),
      el("td", {}, el("details", {}, el("summary", {}, "Input / output"), json({ args: t.args, result: t.result }))))))
      : el("p", { class: "small muted" }, "No tool calls yet."),
    el("dl", { class: "kv", style: "margin-top:12px" }, el("dt", {}, "Run id"), el("dd", { class: "mono" }, run.id),
      run.gmail_message_id ? [el("dt", {}, "Gmail message"), el("dd", { class: "mono" }, run.gmail_message_id)] : null));
  body.querySelectorAll("details").forEach((d, i) => { if (open[i]) d.open = true; });
}

// ---------- observability: overview, AI usage, tool calls (sidebar pages) ----------
// Aggregates come from the API (computed in the store). Charts are hand-written SVG built with createElementNS;
// every label is a text node. Costs are estimates from list prices (backend/app/llm/client.py PRICES).
const SVGNS = "http://www.w3.org/2000/svg";
const AGENTS = ["intake", "comms", "tracking", "migration", "root"];
const CHART_DAYS = 14;

function svgEl(tag, attrs, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
  for (const c of kids) if (c !== null && c !== undefined) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

const fmtInt = (n) => Number(n || 0).toLocaleString();
const fmtUsd = (v, d = 4) => `$${Number(v || 0).toFixed(d)}`;
function fmtMs(v) {
  if (v === null || v === undefined) return "-";
  return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(v < 10000 ? 2 : 1)} s`;
}
function fmtDur(s) {
  if (s === null || s === undefined) return "-";
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
function shortDay(iso) {
  const d = new Date(`${iso}T00:00:00`);
  return isNaN(d) ? String(iso) : d.toLocaleDateString([], { month: "short", day: "numeric" });
}
function lastDays(n) {
  const out = [];
  const today = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function kpi(label, value, sub, tone) {
  return el("div", { class: `kpi ${tone || ""}` }, el("span", { class: "kpi-k" }, label),
    el("b", { class: "kpi-v" }, value), sub ? el("span", { class: "kpi-s" }, sub) : null);
}

// Vertical bars, one series. points: [{label, value}]. Labels are HTML (crisp at any width), bars are SVG.
function barChart(points, fmt, name, sparse) {
  const n = Math.max(points.length, 1);
  const max = Math.max(0, ...points.map((p) => Number(p.value) || 0));
  const s = svgEl("svg", { viewBox: `0 0 ${n * 10} 100`, preserveAspectRatio: "none", class: "bars", role: "img",
    "aria-label": `${name}. ${points.map((p) => `${p.label} ${fmt(p.value)}`).join(", ")}` });
  points.forEach((p, i) => {
    const v = Number(p.value) || 0;
    const h = v && max ? Math.max(2, (96 * v) / max) : 0;
    s.append(svgEl("g", { class: "bar" }, svgEl("title", {}, `${p.label}: ${fmt(v)}`),
      svgEl("rect", { class: "hit", x: i * 10, y: 0, width: 10, height: 100 }),
      svgEl("rect", { class: "fill", x: i * 10 + 1.5, y: 100 - h, width: 7, height: h })));
  });
  s.append(svgEl("line", { class: "base", x1: 0, x2: n * 10, y1: 99.5, y2: 99.5 }));
  const mid = Math.floor((n - 1) / 2);
  const xs = el("div", { class: `chart-x${sparse ? " sparse" : ""}`, style: `grid-template-columns:repeat(${n},minmax(0,1fr))` },
    points.map((p, i) => el("span", {}, !sparse || i === 0 || i === n - 1 || i === mid ? p.label : "")));
  return el("div", { class: "chart" }, el("div", { class: "chart-max" }, `Peak ${fmt(max)}`), s, xs);
}

// Area sparkline for a daily series. points: [{label, value}]
function sparkChart(points, fmt, name) {
  const n = points.length;
  const max = Math.max(0, ...points.map((p) => Number(p.value) || 0)) || 1;
  const x = (i) => (n > 1 ? (i * 100) / (n - 1) : 50);
  const y = (v) => 38 - (36 * (Number(v) || 0)) / max;
  const line = points.map((p, i) => `${x(i).toFixed(2)},${y(p.value).toFixed(2)}`).join(" ");
  const s = svgEl("svg", { viewBox: "0 0 100 40", preserveAspectRatio: "none", class: "spark", role: "img",
    "aria-label": `${name}. ${points.map((p) => `${p.label} ${fmt(p.value)}`).join(", ")}` },
  svgEl("polygon", { class: "area", points: `0,40 ${line} 100,40` }),
  svgEl("polyline", { class: "line", points: line, "vector-effect": "non-scaling-stroke" }));
  points.forEach((p, i) => {
    const w = n > 1 ? 100 / (n - 1) : 100;
    s.append(svgEl("rect", { class: "hit", x: Math.max(0, x(i) - w / 2), y: 0, width: w, height: 40 },
      svgEl("title", {}, `${p.label}: ${fmt(p.value)}`)));
  });
  return el("div", { class: "chart" }, el("div", { class: "chart-max" }, `Peak ${fmt(Math.max(...points.map((p) => Number(p.value) || 0), 0))}`),
    s, el("div", { class: "chart-x ends" }, el("span", {}, points.length ? points[0].label : ""),
      el("span", {}, points.length ? points[n - 1].label : "")));
}

// heads: [label, cls?]; rows: arrays of cells (node or text). cls "num" right-aligns, "hide-sm" hides on phones.
function dataTable(heads, rows, onRow) {
  const cls = heads.map((h) => (Array.isArray(h) ? h[1] : ""));
  return el("div", { class: "table-wrap" }, el("table", { class: `data${onRow ? " clickable" : ""}` },
    el("thead", {}, el("tr", {}, heads.map((h, i) => el("th", { class: cls[i] }, Array.isArray(h) ? h[0] : h)))),
    el("tbody", {}, rows.map((r, ri) => {
      const tr = el("tr", {}, r.cells.map((c, i) => el("td", { class: cls[i] }, c)));
      if (onRow) {
        tr.tabIndex = 0;
        tr.addEventListener("click", () => onRow(r.data, tr));
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRow(r.data, tr); } });
        if (r.current) tr.classList.add("current");
      }
      tr.style.setProperty("--i", String(Math.min(ri, 10)));
      return tr;
    }))));
}

function section(title, ...kids) { return el("section", { class: "panel" }, el("h2", {}, title), ...kids); }

function select(label, options, value, onChange) {
  const s = el("select", { "aria-label": label, onchange: () => onChange(s.value) },
    el("option", { value: "" }, `All ${label.toLowerCase()}${label.endsWith("s") ? "es" : "s"}`),
    options.map((o) => el("option", { value: o, selected: o === value }, o)));
  if (value && !options.includes(value)) s.append(el("option", { value, selected: true }, value));
  return s;
}

// Identifier in mono with soft line breaks after "_" (text nodes + <wbr>, never parsed markup).
const ident = (v) => el("span", { class: "mono" }, String(v || "-").split(/(?<=_)/).flatMap((part, i) => (i ? [el("wbr"), part] : [part])));
const modelChip = (m) => el("span", { class: "chip model mono" }, m || "-");
const runLink = (id) => el("a", { class: "btn", href: `#/runs/${enc(id)}` }, "Open run →");

// A side panel (reuses the drawer). Opening it never changes the route, so refreshes keep it.
function sidePanel() {
  const scrim = el("div", { class: "scrim" });
  const drawer = el("aside", { class: "drawer obs", "aria-label": "Details", tabindex: "-1" });
  const p = { scrim, drawer, key: null };
  p.close = () => {
    if (!p.key) return;
    const was = p.key;
    p.key = null;
    drawer.classList.remove("open"); scrim.classList.remove("open"); document.body.classList.remove("locked");
    if (p.onClose) p.onClose(was);
  };
  p.open = (key, chipNode, title, body) => {
    const first = !p.key;
    p.key = key;
    fill(drawer, el("div", { class: "drawer-head" }, el("div", {}, chipNode, el("h2", {}, title)),
      el("button", { class: "btn icon", type: "button", "aria-label": "Close", onclick: p.close }, "×")),
    el("div", { class: "drawer-body" }, body));
    drawer.classList.add("open"); scrim.classList.add("open"); document.body.classList.add("locked");
    if (first) drawer.focus({ preventScroll: true });
  };
  scrim.addEventListener("click", p.close);
  return p;
}

function kvList(pairs) {
  return el("dl", { class: "kv big" }, pairs.filter(Boolean).map(([k, v]) => [el("dt", {}, k), el("dd", {}, v === null || v === undefined || v === "" ? "-" : v)]));
}

// Shared shell for the two log pages: filters + paged table + side panel. State lives in `ui` across refreshes.
function obsState(kind, filters) {
  if (ui && ui.kind === kind) return ui;
  const size = Number(getPref(`dash.pp.${kind}`, "10"));
  return { kind, sigs: {}, page: 0, size: PAGE_SIZES.includes(size) ? size : 10, f: { ...filters } };
}
function logQuery(st) {
  const q = new URLSearchParams({ limit: String(st.size), offset: String(st.page * st.size) });
  for (const [k, v] of Object.entries(st.f)) if (v) q.set(k, v);
  return q.toString();
}
function setFilter(k, v) { ui.f[k] = v; ui.page = 0; ui.sigs.log = null; refresh(true); }

function filterBar(fields) {
  const any = Object.values(ui.f).some(Boolean);
  return el("div", { class: "filters" }, fields,
    any ? el("button", { class: "btn ghost", type: "button", onclick: () => {
      for (const k of Object.keys(ui.f)) ui.f[k] = ""; ui.page = 0; ui.sigs.log = null; refresh(true);
    } }, "Clear filters") : null);
}

function runIdField() {
  const i = el("input", { type: "search", value: ui.f.run_id || "", placeholder: "Run id", "aria-label": "Run id",
    class: "mono", spellcheck: false });
  i.addEventListener("change", () => setFilter("run_id", i.value.trim()));
  return i;
}

function obsShell(st, intro) {
  ui = st;
  ui.top = el("div");
  ui.logPanel = el("section", { class: "panel log-panel" });
  ui.pager = pager(st.kind, (d) => {
    ui.size = ui.pager.size(); ui.page = turn(ui.page, d); ui.sigs.log = null; refresh(true);
  });
  ui.side = sidePanel();
  ui.side.onClose = () => { ui.sigs.log = null; refresh(true); };
  ui.closePanel = ui.side.close;
  app.replaceChildren(el("div", { class: "page-head" }, el("p", {}, intro)), ui.top, ui.logPanel, ui.side.scrim, ui.side.drawer);
}

// ----- Overview -----
const OVERVIEW = {
  async load() {
    const [ov, llm, runs] = await Promise.all([api("/metrics/overview"), api(`/metrics/llm?days=${CHART_DAYS}`), api("/runs")]);
    return { ov, llm, runs };
  },
  render({ ov, llm, runs }, escs) {
    if (!ui || ui.kind !== "overview") {
      ui = { kind: "overview", sigs: {} };
      ui.kpis = el("div", { class: "kpis" });
      ui.charts = el("div", { class: "grid2" });
      ui.lower = el("div", { class: "grid2" });
      app.replaceChildren(el("div", { class: "page-head" }, el("p", {}, "How onboarding is going across every run.")),
        ui.kpis, ui.charts, ui.lower);
    }
    const st = ov.runs_by_status || {};
    const n = (k) => st[k] || 0;
    const active = Object.entries(st).filter(([k]) => (STATUS[k] || [0, 0, "active"])[2] === "active").reduce((a, [, v]) => a + v, 0);
    const c = ov.calls || {};
    const e2p = ov.email_to_project || {};
    if (changed("kpis", [ov, escs.length])) {
      fill(ui.kpis,
        kpi("Runs", fmtInt(ov.runs_total), `${fmtInt(active)} in progress`),
        kpi("Completed", fmtInt(n("COMPLETED") + n("HANDED_OFF")), `${fmtInt(n("HANDED_OFF"))} handed off`, "ok"),
        kpi("Needs a human", fmtInt(ov.escalations_open), ov.escalations_open ? "Open escalations" : "Nothing waiting",
          ov.escalations_open ? "warn" : ""),
        kpi("Email to project", fmtDur(e2p.avg_seconds), e2p.runs ? `Average over ${fmtInt(e2p.runs)} runs` : "No project yet"),
        kpi("LLM cost (est.)", fmtUsd(ov.llm_cost_usd), `${fmtInt(ov.llm_calls)} calls, list prices`),
        kpi("Calls confirmed / placed", `${fmtInt(c.confirmed)} / ${fmtInt(c.placed)}`,
          `${fmtInt(c.unclear)} unclear, ${fmtInt(c.no_answer)} unanswered`));
    }
    const days = lastDays(CHART_DAYS);
    const perDay = Object.fromEntries((ov.runs_by_day || []).map((d) => [d.day, d.runs]));
    const series = llm.series || [];
    if (changed("charts", [perDay, series.map((d) => d.cost_usd)])) {
      fill(ui.charts,
        section(`Runs started, last ${CHART_DAYS} days`, barChart(days.map((d) => ({ label: shortDay(d), value: perDay[d] || 0 })),
          (v) => fmtInt(v), "Runs started per day", true)),
        section("LLM cost per day (est.)", sparkChart(series.map((d) => ({ label: shortDay(d.day), value: d.cost_usd })),
          (v) => fmtUsd(v), "Estimated LLM cost per day")));
    }
    const recent = [...runs].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 6);
    if (changed("lower", [st, recent.map((r) => [r.id, r.status, r.updated_at, r.customer_name])])) {
      const total = Math.max(1, ...Object.values(st));
      const statusRows = Object.entries(st).sort((a, b) => b[1] - a[1]).map(([k, v]) => {
        const [label, tone] = STATUS[k] || [human(k), "idle"];
        const bar = el("span", { class: `hbar ${tone}` });
        bar.style.setProperty("--w", String(v / total));
        return el("li", {}, el("span", { class: "hlabel" }, label), el("span", { class: "htrack" }, bar), el("b", {}, fmtInt(v)));
      });
      fill(ui.lower,
        section("Runs by status", statusRows.length ? el("ul", { class: "hbars" }, statusRows)
          : el("div", { class: "empty" }, el("strong", {}, "No runs yet"), "Runs appear when a deal email arrives.")),
        section("Recent activity", recent.length ? el("div", { class: "recent" }, recent.map((r) => {
          const d = cache.get(r.id);
          const info = derive(r, d && d.data, escs);
          return el("a", { class: "recent-row", href: `#/runs/${enc(r.id)}` },
            el("span", { class: "recent-name" }, info.name), statusChip(r, info),
            el("span", { class: "time" }, ago(r.updated_at || r.created_at)));
        })) : el("div", { class: "empty" }, "Nothing yet.")));
    }
  },
};

// ----- AI usage -----
const AI = {
  async load() {
    const st = obsState("ai", { model: "", node: "", run_id: "" });
    const [m, page] = await Promise.all([api(`/metrics/llm?days=${CHART_DAYS}`), api(`/llm-calls?${logQuery(st)}`)]);
    return { st, m, page };
  },
  render({ st, m, page }) {
    if (ui !== st) obsShell(st, "Every LLM call across all runs: models, tokens, latency and estimated cost.");
    const t = m.totals || {};
    if (changed("top", [t, m.by_model, m.by_node, m.by_prompt, m.latency_buckets, (m.series || []).map((d) => d.calls)])) {
      const prices = Object.entries(m.prices_usd_per_mtok || {}).map(([k, [i, o]]) => `${k} $${i} in / $${o} out`).join("; ");
      const groupRow = (g, first) => ({ data: g, cells: [first, el("span", {}, fmtInt(g.calls)), `${fmtInt(g.input_tokens)} / ${fmtInt(g.output_tokens)}`,
        fmtUsd(g.cost_usd), fmtMs(g.avg_latency_ms), fmtMs(g.p95_latency_ms)] });
      const gh = ["", ["Calls", "num"], ["Tokens in / out", "num hide-sm"], ["Est. cost", "num"], ["Avg", "num hide-sm"], ["p95", "num"]];
      fill(ui.top,
        el("div", { class: "kpis" },
          kpi("LLM calls", fmtInt(t.calls)),
          kpi("Tokens in", fmtInt(t.input_tokens)),
          kpi("Tokens out", fmtInt(t.output_tokens)),
          kpi("Cost (est.)", fmtUsd(t.cost_usd), "List prices, not billing"),
          kpi("Latency p50", fmtMs(t.p50_latency_ms)),
          kpi("Latency p95", fmtMs(t.p95_latency_ms), t.avg_latency_ms !== null && t.avg_latency_ms !== undefined ? `Average ${fmtMs(t.avg_latency_ms)}` : "")),
        el("div", { class: "grid2" },
          section(`Calls per day, last ${CHART_DAYS} days`, barChart((m.series || []).map((d) => ({ label: shortDay(d.day), value: d.calls })),
            (v) => fmtInt(v), "LLM calls per day", true)),
          section("Latency distribution", barChart((m.latency_bucket_labels || []).map((l, i) => ({ label: l, value: (m.latency_buckets || [])[i] || 0 })),
            (v) => `${fmtInt(v)} calls`, "LLM calls by latency"))),
        section("By model", (m.by_model || []).length ? dataTable([["Model"], ...gh.slice(1)],
          m.by_model.map((g) => groupRow(g, modelChip(g.model)))) : el("p", { class: "small muted" }, "No LLM calls yet."),
        el("p", { class: "small muted note" }, `Cost is estimated from list prices per million tokens (${prices || "none set"}). Unknown models count as $0.`)),
        el("div", { class: "grid2" },
          section("By step", (m.by_node || []).length ? dataTable([["Agent / step"], ["Calls", "num"], ["Est. cost", "num"], ["p95", "num"]],
            m.by_node.map((g) => ({ cells: [ident(`${g.agent || "-"} / ${g.node || "-"}`), fmtInt(g.calls), fmtUsd(g.cost_usd), fmtMs(g.p95_latency_ms)] })))
            : el("p", { class: "small muted" }, "No LLM calls yet.")),
          section("By prompt version", (m.by_prompt || []).length ? dataTable([["Prompt"], ["Calls", "num"], ["Est. cost", "num"], ["p95", "num"]],
            m.by_prompt.map((g) => ({ cells: [ident(g.prompt_version), fmtInt(g.calls), fmtUsd(g.cost_usd), fmtMs(g.p95_latency_ms)] })))
            : el("p", { class: "small muted" }, "No LLM calls yet."))));
    }
    const models = (m.by_model || []).map((g) => g.model).filter(Boolean);
    const nodes = [...new Set((m.by_node || []).map((g) => g.node).filter(Boolean))].sort();
    ui.page = ui.pager.update(page.total, ui.page);
    if (changed("log", [page.total, page.rows.map((r) => r.id), ui.f, models, nodes, ui.side.key])) {
      const open = (r) => {
        ui.side.open(`llm:${r.id}`, modelChip(r.model), `${r.agent || "-"} / ${r.node || "-"}`, [
          kvList([["Time", fmtTime(r.ts, true)], ["Customer", r.customer_name], ["Run", r.run_id ? el("span", { class: "mono" }, r.run_id) : ""],
            ["Agent", r.agent], ["Step", el("span", { class: "mono" }, r.node || "")], ["Model", el("span", { class: "mono" }, r.model || "")],
            ["Prompt", el("span", { class: "mono" }, r.prompt_version || "")], ["Tokens in", fmtInt(r.input_tokens)],
            ["Tokens out", fmtInt(r.output_tokens)], ["Latency", fmtMs(r.latency_ms)], ["Cost (est.)", fmtUsd(r.cost_usd, 6)],
            ["Stop reason", r.stop_reason]]),
          el("p", { class: "small muted" }, "Prompts and responses are not stored; this is the call's metadata."),
          r.run_id ? runLink(r.run_id) : null]);
        ui.sigs.log = null; refresh(true);
      };
      fill(ui.logPanel, el("div", { class: "panel-head" }, el("h2", {}, `Call log (${fmtInt(page.total)})`)),
        filterBar([select("Model", models, ui.f.model, (v) => setFilter("model", v)),
          select("Step", nodes, ui.f.node, (v) => setFilter("node", v)), runIdField()]),
        page.rows.length ? dataTable([["Time", "nowrap"], ["Customer", "hide-sm"], ["Step"], ["Model", "hide-sm"], ["Tokens in / out", "num hide-sm"],
          ["Latency", "num"], ["Est. cost", "num hide-sm"], ["Stop", "hide-sm"]],
        page.rows.map((r) => ({ data: r, current: ui.side.key === `llm:${r.id}`, cells: [fmtTime(r.ts, true), r.customer_name || "-",
          ident(r.node), modelChip(r.model), `${fmtInt(r.input_tokens)} / ${fmtInt(r.output_tokens)}`,
          fmtMs(r.latency_ms), fmtUsd(r.cost_usd), r.stop_reason || "-"] })), open)
          : el("div", { class: "empty" }, el("strong", {}, "No LLM calls match"), Object.values(ui.f).some(Boolean) ? "Try clearing the filters." : "Calls appear as runs are processed."),
        ui.pager.root);
    }
  },
};

// ----- Tool calls -----
const TOOLS = {
  async load() {
    const st = obsState("tools", { tool: "", agent: "", status: "", run_id: "" });
    const [m, page] = await Promise.all([api("/metrics/tools"), api(`/tool-calls?${logQuery(st)}`)]);
    return { st, m, page };
  },
  render({ st, m, page }) {
    if (ui !== st) obsShell(st, "Every external action the agents took, through the tool registry. Inputs and outputs load on demand.");
    const tools = m.tools || [];
    if (changed("top", tools)) {
      const calls = tools.reduce((a, t) => a + t.calls, 0);
      const failed = tools.reduce((a, t) => a + t.failed, 0);
      fill(ui.top,
        el("div", { class: "kpis" },
          kpi("Tool calls", fmtInt(calls)),
          kpi("Failed", fmtInt(failed), calls ? `${((100 * failed) / calls).toFixed(1)}% of calls` : "", failed ? "warn" : ""),
          kpi("Replayed", fmtInt(tools.reduce((a, t) => a + t.replayed, 0)), "Idempotent repeats, not re-sent"),
          kpi("Blocked", fmtInt(tools.reduce((a, t) => a + t.blocked, 0)), "Not on the agent's allowlist")),
        section("Health by tool", tools.length ? dataTable([["Tool"], ["Calls", "num"], ["Failed", "num hide-sm"], ["Failure rate", "num"],
          ["Replayed", "num hide-sm"], ["Blocked", "num hide-sm"], ["Avg", "num hide-sm"], ["p95", "num"]],
        tools.map((t) => ({ data: t, cells: [ident(t.tool), fmtInt(t.calls), fmtInt(t.failed),
          chip(`${(t.failure_rate * 100).toFixed(1)}%`, t.failed ? "bad" : "ok"), fmtInt(t.replayed), fmtInt(t.blocked),
          fmtMs(t.avg_latency_ms), fmtMs(t.p95_latency_ms)] })), (t) => setFilter("tool", t.tool))
          : el("p", { class: "small muted" }, "No tool calls yet.")));
    }
    const names = tools.map((t) => t.tool).sort();
    ui.page = ui.pager.update(page.total, ui.page);
    if (changed("log", [page.total, page.rows.map((r) => [r.id, r.status]), ui.f, names, ui.side.key])) {
      const open = async (r) => {
        const key = `tool:${r.id}`;
        const head = (body) => ui.side.open(key, chip(r.status || "", r.status === "ok" ? "ok" : "bad"), r.tool || "Tool call", body);
        const meta = kvList([["Time", fmtTime(r.ts, true)], ["Customer", r.customer_name], ["Run", r.run_id ? el("span", { class: "mono" }, r.run_id) : ""],
          ["Agent", r.agent], ["Attempt", r.attempt !== null && r.attempt !== undefined ? String(r.attempt) : ""],
          ["Duration", fmtMs(r.latency_ms)], ["Idempotency key", r.idempotency_key ? el("span", { class: "mono" }, r.idempotency_key) : ""]]);
        head([meta, el("div", { class: "working" }, "Loading input and output…")]);
        ui.sigs.log = null; refresh(true);
        let body;
        try {
          const d = await api(`/tool-calls/${enc(r.id)}`);
          body = [el("div", { class: "label" }, "Input"), json(d.args ?? null), el("div", { class: "label" }, "Output"), json(d.result ?? null),
            el("p", { class: "small muted" }, "Phone numbers are masked to the last 4 digits; secrets are redacted.")];
        } catch (e) { body = [errorBox(e)]; }
        if (ui && ui.side && ui.side.key === key) head([meta, body, r.run_id ? runLink(r.run_id) : null]);
      };
      fill(ui.logPanel, el("div", { class: "panel-head" }, el("h2", {}, `Call log (${fmtInt(page.total)})`)),
        filterBar([select("Tool", names, ui.f.tool, (v) => setFilter("tool", v)), select("Agent", AGENTS, ui.f.agent, (v) => setFilter("agent", v)),
          select("Status", ["ok", "failed"], ui.f.status, (v) => setFilter("status", v)), runIdField()]),
        page.rows.length ? dataTable([["Time", "nowrap"], ["Customer", "hide-sm"], ["Agent", "hide-sm"], ["Tool"], ["Status"], ["Try", "num hide-sm"],
          ["Duration", "num"], ["Idempotency key", "hide-sm"]],
        page.rows.map((r) => ({ data: r, current: ui.side.key === `tool:${r.id}`, cells: [fmtTime(r.ts, true), r.customer_name || "-", r.agent || "-",
          ident(r.tool), chip(r.status || "", r.status === "ok" ? "ok" : "bad"),
          r.attempt !== null && r.attempt !== undefined ? String(r.attempt) : "-", fmtMs(r.latency_ms),
          el("span", { class: "mono key", title: r.idempotency_key || "" }, r.idempotency_key || "-")] })), open)
          : el("div", { class: "empty" }, el("strong", {}, "No tool calls match"), Object.values(ui.f).some(Boolean) ? "Try clearing the filters." : "Calls appear as runs are processed."),
        ui.pager.root);
    }
  },
};

const OBS = { overview: OVERVIEW, ai: AI, tools: TOOLS };

// ---------- user menu ----------
function setupUserMenu() {
  const btn = document.getElementById("user-btn");
  const menu = document.getElementById("user-menu");
  const toggle = (open) => {
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) menu.querySelector("button").focus();
  };
  btn.addEventListener("click", () => toggle(menu.hidden));
  document.addEventListener("click", (e) => { if (!menu.hidden && !e.target.closest(".user")) toggle(false); });
  menu.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); toggle(false); btn.focus(); } });
  const out = document.getElementById("sign-out");
  out.addEventListener("click", async () => {
    out.disabled = true;
    out.textContent = "Signing out…";
    try { await fetch("/auth/logout", { method: "POST" }); } catch (_) { /* cookie expires anyway */ }
    location.assign("/ui/login");
  });
  api("/auth/me").then((me) => {
    const name = String((me && me.user) || "");
    if (!name) return;
    document.getElementById("user-name").textContent = name;
    document.getElementById("menu-user").textContent = name;
    document.getElementById("user-initial").textContent = name.charAt(0).toUpperCase();
  }).catch(() => { /* a 401 already redirected */ });
}

// ---------- boot ----------
setupUserMenu();
document.getElementById("crumb").addEventListener("click", goBack);
window.addEventListener("hashchange", navigate);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !view || !document.getElementById("user-menu").hidden) return;
  if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
  if (view.name === "run") goBack();
  else if (view.name === "escalations" && view.id) closeDrawer();
  else if (ui && ui.closePanel) ui.closePanel();
});
setInterval(() => refresh(false), REFRESH_MS);
api("/ui/config").then((c) => { config = c || config; }).catch(() => { /* links fall back to plain ids */ })
  .finally(navigate);
