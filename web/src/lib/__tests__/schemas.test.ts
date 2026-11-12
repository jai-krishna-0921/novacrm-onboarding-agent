import { describe, expect, it } from "vitest";
import { createDb } from "@/mocks/data";
import { Aes, AgentsStatus, MigrationData, MigrationSummary, RunDetail, Runs } from "../schemas";

describe("zod schemas", () => {
  it("parses a run detail and fills defaults for missing blocks", () => {
    const d = RunDetail.parse({
      run: { id: "r1", status: "RECEIVED", rocketlane_project_id: 48213 },
      pending: null,
      audit: [{ step: "start", ts: "2026-11-12T10:00:00Z", input: { subject: "Closed Won: X" } }],
    });
    expect(d.run.rocketlane_project_id).toBe("48213");
    expect(d.tool_calls).toEqual([]);
    expect(d.llm).toEqual({ calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, by_node: [] });
  });

  it("coerces Numeric cost strings from Postgres", () => {
    const d = RunDetail.parse({
      run: { id: "r1" },
      audit: [],
      llm: {
        calls: 1,
        input_tokens: 1,
        output_tokens: 1,
        cost_usd: "0.0123",
        by_node: [{ cost_usd: "0.0123" }],
      },
    });
    expect(d.llm.cost_usd).toBeCloseTo(0.0123);
    expect(d.llm.by_node[0].cost_usd).toBeCloseTo(0.0123);
  });

  it("rejects a run without an id", () => {
    expect(Runs.safeParse([{ status: "RECEIVED" }]).success).toBe(false);
  });

  it("validates the new AE contract", () => {
    expect(
      Aes.safeParse([
        { email: "a@b.co", name: "A", phone_masked: "******8350", active: true, updated_at: null },
      ]).success,
    ).toBe(true);
    expect(Aes.safeParse([{ email: "a@b.co", name: "A", active: "yes" }]).success).toBe(false);
  });

  it("validates migration summary problem lists", () => {
    const s = MigrationSummary.parse({
      source_count: 2,
      dest_count: 1,
      problems: { missing_in_dest: [7] },
      last_check: null,
    });
    expect(s.problems).toEqual({
      duplicates: [],
      missing_required: [],
      missing_in_dest: [7],
      not_in_source: [],
    });
    // payloads from before the column check still parse
    expect(s.columns).toEqual({ source: [], dest: [], missing: [], extra: [] });
  });

  it("parses migration columns and keeps every table column on a data row", () => {
    const s = MigrationSummary.parse({
      source_count: 1,
      dest_count: 1,
      problems: {},
      columns: {
        source: ["id", "phone"],
        dest: ["id", "lifecycle_stage"],
        missing: ["phone"],
        extra: ["lifecycle_stage"],
      },
    });
    expect(s.columns.missing).toEqual(["phone"]);
    const d = MigrationData.parse({
      total: 1,
      rows: [
        {
          id: 1,
          name: "Ava",
          email: null,
          phone: null,
          created_on: "2024-01-12",
          problems: ["missing_email"],
        },
      ],
    });
    expect(d.rows[0]).toMatchObject({ phone: null, created_on: "2024-01-12", problems: ["missing_email"] });
  });

  it("maps an unknown agent status to idle instead of failing the page", () => {
    const s = AgentsStatus.parse({ agents: [{ id: "intake", name: "Intake", status: "exploded" }] });
    expect(s.agents[0].status).toBe("idle");
  });

  it("accepts every mock fixture", () => {
    const db = createDb();
    expect(Runs.safeParse(db.runs).success).toBe(true);
    expect(AgentsStatus.safeParse(db.agents).success).toBe(true);
  });
});
