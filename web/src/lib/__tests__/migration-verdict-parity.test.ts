import { describe, expect, it } from "vitest";
import { predictChecks } from "../migration";
import { MigrationSummary } from "../schemas";

// Each `summary` is the exact JSON backend summary() returns (find_problems + compare_columns), and `backend` is
// [c.passed for c in evaluate(source, dest)] for the same tables, captured from
// backend/app/services/migration_check.py. The verdict card must agree with the check that will be recorded.
const cols = { source: ["id", "name", "email"], dest: ["id", "name", "email"], missing: [], extra: [] };
const none = { duplicates: [], missing_required: [], missing_in_dest: [], not_in_source: [] };

const CASES: { id: string; summary: unknown; backend: boolean[] }[] = [
  {
    id: "clean",
    summary: { source_count: 2, dest_count: 2, problems: none, columns: cols },
    backend: [true, true, true, true, true],
  },
  {
    id: "empty_both",
    summary: { source_count: 0, dest_count: 0, problems: none, columns: cols },
    backend: [false, true, true, true, true],
  },
  {
    id: "empty_source",
    summary: { source_count: 0, dest_count: 1, problems: { ...none, not_in_source: [1] }, columns: cols },
    backend: [false, true, true, true, true],
  },
  {
    id: "swapped_id_same_count",
    summary: {
      source_count: 2,
      dest_count: 2,
      problems: { ...none, missing_in_dest: [2], not_in_source: [99] },
      columns: cols,
    },
    backend: [true, false, true, true, true],
  },
  {
    id: "extra_not_in_source",
    summary: { source_count: 2, dest_count: 3, problems: { ...none, not_in_source: [99] }, columns: cols },
    backend: [false, true, true, true, true],
  },
  {
    id: "null_id_extra",
    summary: { source_count: 2, dest_count: 3, problems: { ...none, not_in_source: [null] }, columns: cols },
    backend: [false, true, true, true, true],
  },
  {
    id: "missing_column",
    summary: {
      source_count: 1,
      dest_count: 1,
      problems: none,
      columns: {
        source: ["id", "name", "email", "phone"],
        dest: ["id", "name", "email"],
        missing: ["phone"],
        extra: [],
      },
    },
    backend: [true, true, true, true, false],
  },
  // Destination has no PK: two NULL-id rows make Counter() report None as a duplicate.
  {
    id: "two_null_ids",
    summary: {
      source_count: 2,
      dest_count: 3,
      problems: { duplicates: [null], missing_required: [], missing_in_dest: [2], not_in_source: [null] },
      columns: cols,
    },
    backend: [false, false, false, true, true],
  },
  // A NULL-id row with a blank email lands None in missing_required.
  {
    id: "null_id_blank_email",
    summary: {
      source_count: 1,
      dest_count: 1,
      problems: { duplicates: [], missing_required: [null], missing_in_dest: [1], not_in_source: [null] },
      columns: cols,
    },
    backend: [true, false, true, false, true],
  },
];

describe("migration verdict agrees with backend evaluate()", () => {
  it.each(CASES)("$id", ({ summary, backend }) => {
    const parsed = MigrationSummary.safeParse(summary);
    // A parse failure means api() throws and the Migration page / verdict shows an error instead of the verdict.
    expect(parsed.success, JSON.stringify(parsed.error?.issues ?? [])).toBe(true);
    const checks = predictChecks(parsed.data!);
    expect(checks.map((c) => c.name)).toEqual([
      "record_count",
      "primary_keys",
      "duplicates",
      "required_fields",
      "schema",
    ]);
    expect(checks.map((c) => c.passed)).toEqual(backend);
  });
});
