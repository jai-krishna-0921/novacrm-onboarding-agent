import { describe, expect, it } from "vitest";
import { checkDetail } from "@/components/run/MigrationTiles";
import { CHECKS, problemCount, problemLabel, rowTone } from "../migration";

describe("migration problem highlighting", () => {
  it("clean rows are not highlighted", () => {
    expect(rowTone({ problems: [] })).toBe("ok");
  });
  it("rows missing from the destination are the most severe", () => {
    expect(rowTone({ problems: ["missing_in_dest"] })).toBe("bad");
    expect(rowTone({ problems: ["duplicate", "missing_in_dest"] })).toBe("bad");
  });
  it("duplicates and blank fields are warnings", () => {
    expect(rowTone({ problems: ["duplicate"] })).toBe("warn");
    expect(rowTone({ problems: ["missing_email"] })).toBe("warn");
    expect(rowTone({ problems: ["missing_name"] })).toBe("warn");
  });
  it("labels problems in plain words, and unknown codes readably", () => {
    expect(problemLabel("missing_required")).toBe("Missing name or email");
    // the row codes the backend actually sends (row_problems in services/migration_check.py)
    expect(problemLabel("missing_email")).toBe("Missing email");
    expect(problemLabel("missing_name")).toBe("Missing name");
    expect(problemLabel("duplicate")).toBe("Duplicate id");
    expect(problemLabel("missing_in_dest")).toBe("Not in destination");
    expect(problemLabel("bad_encoding")).toBe("bad encoding");
  });
  it("counts problems across all lists, including missing columns", () => {
    const columns = { source: ["id", "phone"], dest: ["id", "stage"], missing: ["phone"], extra: ["stage"] };
    expect(
      problemCount({
        source_count: 1,
        dest_count: 1,
        problems: { duplicates: [1, 2], missing_required: [3], missing_in_dest: [], not_in_source: [9] },
        columns,
        last_check: null,
      }),
    ).toBe(5);
    // extra destination columns are allowed, so they are not problems
    expect(
      problemCount({
        source_count: 1,
        dest_count: 1,
        problems: { duplicates: [], missing_required: [], missing_in_dest: [], not_in_source: [] },
        columns: { ...columns, missing: [] },
        last_check: null,
      }),
    ).toBe(0);
  });
  it("describes the schema check in columns, every other check in ids", () => {
    expect(CHECKS.schema.label).toBe("Columns");
    expect(
      checkDetail({ check_name: "schema", details: { expected: 0, actual: 1, sample_ids: ["phone"] } }),
    ).toBe("Expected 0, found 1. missing columns phone");
    expect(
      checkDetail({ check_name: "primary_keys", details: { expected: 0, actual: 2, sample_ids: [12, 31] } }),
    ).toBe("Expected 0, found 2. ids 12, 31");
    expect(
      checkDetail({
        check_name: "schema",
        details: { expected: ["id", "phone"], actual: { columns: ["id"], extra: null } },
      }),
    ).toBe("Expected id, phone, found Columns: id; Extra: none");
  });
});
