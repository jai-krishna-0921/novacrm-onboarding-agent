import { useQueryClient } from "@tanstack/react-query";
import { PhoneCall, RotateCcw, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";
import { api, enc } from "@/lib/api";
import { cn } from "@/lib/cn";
import { cap, fmtTime, plainError } from "@/lib/format";
import { getPref, setPref } from "@/lib/prefs";
import { post, qk } from "@/lib/queries";
import { ALLOWED, KIND, TIERS } from "@/lib/run-state";
import { MigrationCheckResult, type Escalation, type Pending, type Run } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { ErrorBox } from "@/components/ui/empty-state";
import { Field, Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { KindChip } from "./chips";

function useByName() {
  const [by, setBy] = useState(() => getPref("dash.by", ""));
  return [
    by,
    (v: string) => {
      setBy(v);
      setPref("dash.by", v.trim());
    },
  ] as const;
}

function useRefreshRun(runId: string) {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: qk.run(runId) }),
      qc.invalidateQueries({ queryKey: qk.escalations }),
      qc.invalidateQueries({ queryKey: qk.runs }),
    ]);
}

export function DecisionPanels({
  run,
  pending,
  escalations,
  compact,
}: {
  run: Run;
  pending: Pending;
  escalations: Escalation[];
  compact?: boolean;
}) {
  const open = escalations.filter((e) => (e.status ?? "open") === "open");
  if (!open.length) return null;
  return (
    <div className="flex flex-col gap-4">
      {open.map((e) => (
        <Card
          key={e.key}
          aria-label="Needs your decision"
          className={cn("border-warn-line", compact ? "shadow-none" : "")}
        >
          <div className={cn("grid gap-5 p-5", !compact && "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]")}>
            <div className="min-w-0">
              <KindChip kind={e.kind} />
              <h2 className="mt-2 text-[16px] font-semibold text-ink">
                {e.kind === "migration" ? "Migration data needs a look" : "Needs your decision"}
              </h2>
              <p className="mt-1.5 text-sm break-words whitespace-pre-wrap text-ink-2">
                {plainError(e.reason)}
              </p>
              <p className="mt-2 text-[12.5px] text-ink-3">
                Raised {fmtTime(e.created_at, true)} by the {e.agent ? `${e.agent} agent` : "agent"}.{" "}
                {KIND[e.kind ?? ""]?.explain}
              </p>
            </div>
            <div className="min-w-0">
              {e.kind === "migration" ? (
                <MigrationActions run={run} />
              ) : (
                <ResolveForm run={run} kind={e.kind || pending?.kind || "system"} pending={pending} />
              )}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}

export function ResolveForm({ run, kind, pending }: { run: Run; kind: string; pending: Pending }) {
  const toast = useToast();
  const refresh = useRefreshRun(run.id);
  const [by, setBy] = useByName();
  const [note, setNote] = useState("");
  const [tier, setTier] = useState("");
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<string>("");

  if (!pending || pending.type !== "escalation") {
    return (
      <p className="text-sm text-ink-3">
        The run is not waiting on this escalation right now. This updates by itself.
      </p>
    );
  }
  const actions = (ALLOWED[kind] ?? ALLOWED.system).filter(
    (a) => !(a === "set_tier" && run.rocketlane_project_id),
  );

  const send = async (action: string) => {
    setError("");
    if (!by.trim()) {
      setError("Enter your name first, so the audit log shows who decided.");
      throw new Error("name");
    }
    if (action === "set_tier" && !tier) {
      setError("Pick Enterprise or Growth first.");
      throw new Error("tier");
    }
    const body: Record<string, string> = { action, by: by.trim() };
    if (action === "set_tier") body.tier = tier;
    if (note.trim()) body.note = note.trim();
    setBusy(action);
    try {
      await post(`/runs/${enc(run.id)}/resolve`, body);
      toast({
        title:
          action === "set_tier"
            ? `Tier set to ${cap(tier)}`
            : action === "retry"
              ? kind === "tier"
                ? "Calling the AE again"
                : "Retrying now"
              : "Run cancelled",
        body: action === "set_tier" ? "Creating the Rocketlane project now." : undefined,
      });
      setNote("");
      await refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast({ title: "Could not apply the decision", body: msg, tone: "bad" });
      throw e;
    } finally {
      setBusy("");
    }
  };
  const fire = (a: string) => () => void send(a).catch(() => {});

  return (
    <div className="flex flex-col gap-4">
      {actions.includes("set_tier") ? (
        <fieldset>
          <legend className="mb-2 text-[13px] font-medium text-ink-2">
            Which plan did the customer buy?
          </legend>
          <div role="radiogroup" className="grid grid-cols-2 gap-2">
            {TIERS.map((t) => (
              <button
                key={t}
                type="button"
                role="radio"
                aria-checked={tier === t}
                onClick={() => setTier(t)}
                className={cn(
                  "rounded-ctl border px-3 py-2.5 text-left transition-colors",
                  tier === t
                    ? "border-accent bg-accent-soft ring-2 ring-accent/15"
                    : "border-line-strong bg-surface hover:bg-sunken",
                )}
              >
                <span className="block text-sm font-semibold text-ink">{cap(t)}</span>
                <span className="block text-[12.5px] text-ink-3">
                  {t === "enterprise" ? "Includes the migration phase" : "Standard onboarding plan"}
                </span>
              </button>
            ))}
          </div>
        </fieldset>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Your name" hint="Shown in the audit log">
          {(p) => <Input {...p} value={by} onChange={(e) => setBy(e.target.value)} autoComplete="name" />}
        </Field>
        <Field label="Note (optional)">
          {(p) => <Input {...p} value={note} onChange={(e) => setNote(e.target.value)} />}
        </Field>
      </div>
      {error ? <ErrorBox error={error} /> : null}
      <div className="flex flex-wrap gap-2">
        {actions.includes("set_tier") ? (
          <Button variant="primary" size="lg" onClick={fire("set_tier")} loading={busy === "set_tier"}>
            <ShieldCheck className="size-4" aria-hidden />
            Confirm tier and continue
          </Button>
        ) : null}
        {actions.includes("retry") ? (
          <Button
            variant={actions.includes("set_tier") ? "secondary" : "primary"}
            size="lg"
            onClick={fire("retry")}
            loading={busy === "retry"}
          >
            {kind === "tier" ? (
              <PhoneCall className="size-4" aria-hidden />
            ) : (
              <RotateCcw className="size-4" aria-hidden />
            )}
            {kind === "tier" ? "Call the AE again" : "Try again"}
          </Button>
        ) : null}
        {actions.includes("cancel") ? (
          <ConfirmDialog
            title="Cancel this run?"
            description="The run stops here. Nothing more is created for this deal. This cannot be undone from the dashboard."
            confirmLabel="Cancel run"
            tone="danger"
            onConfirm={() => send("cancel")}
            trigger={(open) => (
              <Button
                variant="danger-ghost"
                size="lg"
                disabled={!!busy}
                onClick={() =>
                  by.trim() ? open() : setError("Enter your name first, so the audit log shows who decided.")
                }
              >
                <XCircle className="size-4" aria-hidden />
                Cancel run
              </Button>
            )}
          />
        ) : null}
      </div>
    </div>
  );
}

export function MigrationActions({ run }: { run: Run }) {
  const toast = useToast();
  const refresh = useRefreshRun(run.id);
  const [by, setBy] = useByName();
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);

  const recheck = async () => {
    setChecking(true);
    setError("");
    try {
      const r = await api(`/runs/${enc(run.id)}/migration-check`, MigrationCheckResult, {
        method: "POST",
        body: {},
      });
      toast(
        r.passed
          ? { title: "All migration checks passed", body: "The escalation is closed." }
          : { title: "Some checks still fail", body: "See the Migration check tab.", tone: "bad" },
      );
      await refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setChecking(false);
    }
  };

  const dismiss = async () => {
    setError("");
    if (!by.trim() || !reason.trim()) {
      setError("Both your name and a reason are required.");
      throw new Error("missing");
    }
    try {
      await post(`/runs/${enc(run.id)}/migration-escalation/dismiss`, {
        by: by.trim(),
        reason: reason.trim(),
      });
      toast({ title: "Migration issue dismissed", body: "The reason is in the audit log." });
      setReason("");
      await refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      throw e;
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-ink-2">
        Get the data fixed and run the check again. A passing check closes this. Or dismiss it with a reason.
      </p>
      <div>
        <Button variant="primary" size="lg" onClick={recheck} loading={checking}>
          <RotateCcw className="size-4" aria-hidden />
          Run the check again
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Your name">
          {(p) => <Input {...p} value={by} onChange={(e) => setBy(e.target.value)} autoComplete="name" />}
        </Field>
        <Field label="Reason to dismiss">
          {(p) => (
            <Input
              {...p}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why this failure is acceptable"
            />
          )}
        </Field>
      </div>
      {error ? <ErrorBox error={error} /> : null}
      <div>
        <ConfirmDialog
          title="Dismiss the migration issue?"
          description="The escalation closes without a passing check. The evidence note stays on the Rocketlane task, and a person still decides whether to tick Migration verified."
          confirmLabel="Dismiss"
          tone="danger"
          onConfirm={dismiss}
          trigger={(open) => (
            <Button
              variant="danger-ghost"
              onClick={() =>
                by.trim() && reason.trim() ? open() : setError("Both your name and a reason are required.")
              }
            >
              Dismiss with reason
            </Button>
          )}
        />
      </div>
    </div>
  );
}
