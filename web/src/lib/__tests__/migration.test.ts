import { describe, expect, it } from "vitest";
import { problemCount, problemLabel, rowTone } from "../migration";

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
  it("counts problems across all lists", () => {
    expect(
      problemCount({
        source_count: 1,
        dest_count: 1,
        problems: { duplicates: [1, 2], missing_required: [3], missing_in_dest: [] },
        last_check: null,
      }),
    ).toBe(3);
  });
});
