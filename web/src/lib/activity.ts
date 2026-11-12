import type { AuditRow } from "./schemas";
import { cap, human } from "./format";

export type FeedTone = "ok" | "warn" | "bad" | "info" | "idle" | "tech";
const TONE: Record<string, FeedTone> = {
  ok: "ok",
  escalated: "warn",
  blocked: "warn",
  no_answer: "warn",
  fallback: "warn",
  degraded: "warn",
  unclear: "warn",
  failed: "bad",
  error: "bad",
  ignored: "idle",
  confirmed: "ok",
  replayed: "idle",
};

/** Plain-language label for one audit row. `tech` rows are hidden in the "Key events" view. */
export function describe(a: AuditRow): { title: string; tone: FeedTone; tech: boolean } {
  const s = String(a.status || "ok").toLowerCase();
  const step = String(a.step || "");
  const tone = TONE[s] ?? "info";
  const ok = s === "ok";
  if (step.startsWith("tool:"))
    return { title: `Tool: ${step.slice(5)}`, tone: ok ? "tech" : tone, tech: true };
  const out = (a.output && typeof a.output === "object" ? a.output : {}) as Record<string, unknown>;
  const t = (title: string, tn?: FeedTone) => ({ title, tone: tn ?? tone, tech: false });
  switch (step) {
    case "start":
      return t("Deal email received", "info");
    case "check_sender":
      return t("Sender is a known AE");
    case "extract_fields":
      return t("Read the deal details from the email");
    case "validate":
      return t(
        ok
          ? "All required details present"
          : s === "escalated"
            ? "Email covers more than one deal"
            : "Some details are missing or invalid",
      );
    case "lookup_ae":
      return t("Found the AE's phone number");
    case "decide_tier":
      return t(
        s === "confirmed"
          ? `AE confirmed the ${cap(out.tier ?? "")} plan`.replace("  ", " ")
          : s === "no_answer"
            ? "Call not answered"
            : "Tier was not clear on the call",
      );
    case "call_progress":
      return t(s === "started" ? "AE picked up the call" : "Call ended, reading the answer", "info");
    case "voice_webhook":
    case "voice_reconcile":
      return t(
        s === "ignored" ? "Ignored a stale call result" : "Call result received",
        s === "ignored" ? "idle" : "info",
      );
    case "check_duplicate":
      return t("No duplicate project found");
    case "create_project":
      return t("Rocketlane project created");
    case "draft_welcome":
      return t(s === "fallback" ? "Used the standard welcome message" : "Welcome message drafted");
    case "check_welcome":
      return t("Draft welcome rejected by fact check");
    case "slack_connect":
      return t("Customer invite needs a manual step");
    case "setup_channel":
      return t("Slack channel ready, welcome posted");
    case "complete":
      return t("Onboarding set up", "ok");
    case "escalate_request":
      return { title: "Escalation requested", tone: "tech", tech: true };
    case "escalate":
      return t("Sent to a human for review", "warn");
    case "resolve":
      return t("A person made a decision", "info");
    case "failed":
      return t("Stopped with an error", "bad");
    case "dedupe":
      return t("Duplicate email ignored", "idle");
    case "sync":
      return t("Progress synced from Rocketlane", "info");
    case "sync_failed":
      return t("Progress sync failed", "bad");
    case "phase_complete":
      return t(ok ? "Phase completed" : "Phase announcement had a problem");
    case "migration_check":
      return t(
        s === "ignored"
          ? "Repeated migration check skipped"
          : ok
            ? "Migration check passed"
            : "Migration check found problems",
        s === "ignored" ? "idle" : ok ? "ok" : "bad",
      );
    case "migration_verified_accepted":
      return t("Migration sign-off accepted");
    case "reopen_note":
      return t("Could not write the reopen note");
    case "handoff":
      return t(ok ? "Handed off to support" : s === "blocked" ? "Handoff held back" : "Handoff note failed");
    case "draft_handoff":
      return t(s === "fallback" ? "Used the standard handoff summary" : "Handoff summary drafted");
    case "check_handoff":
      return t("Handoff draft rejected by fact check");
    case "dismiss_migration_escalation":
      return t("Migration issue dismissed by a person", "info");
    default:
      return t(human(step));
  }
}

export type TranscriptLine = { speaker: string; text: string };

/** Split a call transcript ("Agent: ...\nUser: ...") into speaker turns. Unlabelled lines join the previous turn. */
export function parseTranscript(raw: unknown): TranscriptLine[] {
  if (typeof raw !== "string" || !raw.trim()) return [];
  const out: TranscriptLine[] = [];
  for (const line of raw.split(/\r?\n/)) {
    const m = /^\s*([A-Za-z][\w .'-]{0,30}):\s*(.*)$/.exec(line);
    if (m) out.push({ speaker: m[1].trim(), text: m[2] });
    else if (line.trim()) {
      if (out.length) out[out.length - 1].text += `\n${line.trim()}`;
      else out.push({ speaker: "", text: line.trim() });
    }
  }
  return out;
}

export function speakerRole(speaker: string): "agent" | "ae" | "other" {
  const s = speaker.toLowerCase();
  if (["agent", "assistant", "bot", "ai"].includes(s)) return "agent";
  if (["user", "ae", "human", "caller", "customer"].includes(s)) return "ae";
  return "other";
}
