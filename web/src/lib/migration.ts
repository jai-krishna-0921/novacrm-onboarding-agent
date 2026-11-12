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
