import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { MigrationCheckRow } from "@/lib/schemas";
import { TooltipProvider } from "../../ui/tooltip";
import { CheckHistory } from "../MigrationTiles";

/** n batches of two checks each, oldest first (as the API returns them). */
function batches(n: number): MigrationCheckRow[] {
  return Array.from({ length: n }, (_, i) =>
    ["row_count", "required_columns"].map((check_name) => ({
      run_id: "r1",
      batch_id: `b${i + 1}`,
      ts: new Date(Date.UTC(2026, 10, 1, 0, i)).toISOString(),
      check_name,
      passed: true,
      details: {},
    })),
  ).flat();
}
const ui = (n: number) =>
  render(
    <TooltipProvider>
      <CheckHistory checks={batches(n)} />
    </TooltipProvider>,
  );

describe("CheckHistory pagination", () => {
  it("pages earlier batches, newest first, counting batches not checks", async () => {
    ui(12);
    expect(screen.getByText("1-10 of 12 checks")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(11); // header + 10
    expect(screen.getByText("b12")).toBeInTheDocument();
    expect(screen.queryByText("b2")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("11-12 of 12 checks")).toBeInTheDocument();
    expect(screen.getByText("b1")).toBeInTheDocument();
    expect(screen.queryByText("b12")).not.toBeInTheDocument();
  });

  it("remembers rows per page (minimum 5)", async () => {
    ui(7);
    await userEvent.click(screen.getByRole("combobox", { name: "Rows per page" }));
    await userEvent.click(await screen.findByRole("option", { name: "5" }));
    expect(screen.getByText("1-5 of 7 checks")).toBeInTheDocument();
    expect(localStorage.getItem("dash.pp.mig-history")).toBe("5");
  });
});
