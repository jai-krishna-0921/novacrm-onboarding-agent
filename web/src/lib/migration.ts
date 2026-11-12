import type { MigrationDataRow, MigrationSummary } from "./schemas";

// Labels and tones for migration problems, matching backend/app/services/migration_check.py: row codes are
// duplicate, not_in_source, missing_<field> (name, email) and missing_in_dest; summary keys are duplicates, missing_required and
// missing_in_dest and not_in_source, plus columns.missing for the schema check.

export const PROBLEM: Record<string, { label: string; explain: string }> = {
  duplicate: { label: "Duplicate id", explain: "The same id appears more than once in the destination." },
  duplicates: { label: "Duplicate id", explain: "The same id appears more than once in the destination." },
  // Summary key (ids with any blank required field); rows carry the per-field codes below.
  missing_required: {
    label: "Missing name or email",
    explain: "Name or email is blank. Both are required.",
  },
  missing_name: { label: "Missing name", explain: "The name is blank. Name and email are both required." },
  missing_email: { label: "Missing email", explain: "The email is blank. Name and email are both required." },
  not_in_source: {
    label: "Not in source",
    explain: "This destination row has no id, or an id the source never had.",
  },
  missing_in_dest: {
    label: "Not in destination",
    explain: "This source row was not copied to the destination.",
  },
};

export const problemLabel = (p: string) => PROBLEM[p]?.label ?? p.replace(/_/g, " ");
export const problemExplain = (p: string) => PROBLEM[p]?.explain ?? "";

export type RowTone = "ok" | "bad" | "warn";

/** Missing rows are the worst (data loss); duplicates and blanks are data quality problems. */
export function rowTone(row: Pick<MigrationDataRow, "problems">): RowTone {
  if (!row.problems.length) return "ok";
  if (row.problems.includes("missing_in_dest")) return "bad";
  return "warn";
}

export function problemCount(s: MigrationSummary): number {
  const p = s.problems;
  return (
    p.duplicates.length +
    p.missing_required.length +
    p.missing_in_dest.length +
    p.not_in_source.length +
    s.columns.missing.length
  );
}

export const CHECKS: Record<string, { label: string; explain: string }> = {
  record_count: { label: "Record count", explain: "Destination has the same number of rows as the source." },
  primary_keys: { label: "Primary keys", explain: "Every source id exists in the destination." },
  duplicates: { label: "Duplicates", explain: "No id appears twice in the destination." },
  required_fields: { label: "Required fields", explain: "Every destination row has a name and an email." },
  schema: {
    label: "Columns",
    explain: "Every source column exists in the destination. Extra destination columns are fine.",
  },
};

/** The schema check's sample_ids are column names; every other check lists row ids. */
export const sampleNoun = (checkName: string) => (checkName === "schema" ? "missing columns" : "ids");

export type PredictedCheck = { name: string; label: string; passed: boolean; detail: string };

const ids = (xs: unknown[], max = 4) =>
  xs.length > max ? `${xs.slice(0, max).join(", ")} and ${xs.length - max} more` : xs.join(", ");

/** What the five checks in migration_check.py would say about the data right now (same rules, from the
 * summary): lets the page show a verdict before, or long after, a check was recorded on a run. */
export function predictChecks(s: MigrationSummary): PredictedCheck[] {
  const p = s.problems;
  const diff = s.dest_count - s.source_count;
  return [
    {
      name: "record_count",
      label: CHECKS.record_count.label,
      passed: s.source_count > 0 && diff === 0,
      detail:
        s.source_count === 0
          ? "Source is empty"
          : diff === 0
            ? `${s.source_count} rows on both sides`
            : `${s.dest_count} of ${s.source_count} rows (${Math.abs(diff)} ${diff > 0 ? "extra" : "short"})`,
    },
    {
      name: "primary_keys",
      label: CHECKS.primary_keys.label,
      passed: !p.missing_in_dest.length,
      detail: p.missing_in_dest.length
        ? `Ids not copied: ${ids(p.missing_in_dest)}`
        : "Every source id copied",
    },
    {
      name: "duplicates",
      label: CHECKS.duplicates.label,
      passed: !p.duplicates.length,
      detail: p.duplicates.length ? `Duplicated ids: ${ids(p.duplicates)}` : "No id appears twice",
    },
    {
      name: "required_fields",
      label: CHECKS.required_fields.label,
      passed: !p.missing_required.length,
      detail: p.missing_required.length
        ? `Blank name or email on ids ${ids(p.missing_required)}`
        : "Every row has a name and an email",
    },
    {
      name: "schema",
      label: CHECKS.schema.label,
      passed: !s.columns.missing.length,
      detail: s.columns.missing.length
        ? `Missing in destination: ${s.columns.missing.join(", ")}`
        : "Every source column exists",
    },
  ];
}
