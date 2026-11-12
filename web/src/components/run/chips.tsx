import { ExternalLink } from "lucide-react";
import type { Escalation, Run } from "@/lib/schemas";
import { kindLabel, KIND, statusInfo, type RunView } from "@/lib/run-state";
import { TipBadge } from "@/components/ui/badge";
import { Mono } from "@/components/ui/misc";
import { useUiConfig } from "@/lib/queries";
import { cap } from "@/lib/format";

export function StatusChip({ run, view }: { run: Run; view?: Pick<RunView, "migrationEsc"> }) {
  if (view?.migrationEsc)
    return (
      <TipBadge tone="warn" dot tip={KIND.migration.explain}>
        Migration issue
      </TipBadge>
    );
  const s = statusInfo(run.status);
  return (
    <TipBadge tone={s.tone} dot tip={s.explain || s.label}>
      {s.label}
    </TipBadge>
  );
}

export function KindChip({ kind }: { kind: string | null | undefined }) {
  return (
    <TipBadge tone="warn" tip={KIND[kind ?? ""]?.explain ?? "Needs review by a person"}>
      {kindLabel(kind)}
    </TipBadge>
  );
}

export function TierChip({ tier }: { tier: string }) {
  if (!tier)
    return (
      <TipBadge
        tone="idle"
        tip="The plan tier is confirmed by phone with the AE before any project is created."
      >
        Tier pending
      </TipBadge>
    );
  const ent = tier.toLowerCase() === "enterprise";
  return (
    <TipBadge
      tone={ent ? "accent" : "neutral"}
      tip={ent ? "Enterprise template: longer plan with a data migration phase." : "Growth template."}
    >
      {cap(tier)}
    </TipBadge>
  );
}

export function ProjectLink({ id }: { id: string | null | undefined }) {
  const cfg = useUiConfig();
  if (!id) return null;
  const base = cfg.data?.rocketlane_app_url ?? "";
  if (!/^https?:\/\//i.test(base))
    return (
      <span className="text-sm text-ink-2">
        Rocketlane <Mono>#{id}</Mono>
      </span>
    );
  return (
    <a
      href={`${base}/projects/${encodeURIComponent(id)}`}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline"
    >
      Rocketlane #{id}
      <ExternalLink className="size-3.5" aria-hidden />
    </a>
  );
}

export function escalationsFor(escs: Escalation[] | undefined, runId: string) {
  return (escs ?? []).filter((e) => e.run_id === runId);
}
