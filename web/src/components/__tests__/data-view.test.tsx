import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { plainError } from "@/lib/format";
import { DataView, humanizeKey, stripHtml } from "../ui/data-view";
import { TooltipProvider } from "../ui/tooltip";

const ui = (value: unknown) =>
  render(
    <TooltipProvider>
      <DataView value={value} />
    </TooltipProvider>,
  );

describe("DataView", () => {
  it("humanizes keys", () => {
    expect(humanizeKey("rl_project_id")).toBe("Rocketlane project ID");
    expect(humanizeKey("customer_name")).toBe("Customer name");
    expect(humanizeKey("startDate")).toBe("Start date");
    expect(humanizeKey("latency_ms")).toBe("Latency (ms)");
  });

  it("renders null, empty and missing values as None", () => {
    const { container } = ui({ a: null, b: "", c: [], d: {} });
    expect(screen.getAllByText("None")).toHaveLength(4);
    expect(container.textContent).not.toMatch(/null|\{|\}|\[|\]/);
    container.remove();
    ui(null);
    expect(screen.getByText("None")).toBeInTheDocument();
  });

  it("renders scalars as labelled rows: Yes/No, formatted numbers, ids with copy", () => {
    const { container } = ui({ is_active: true, dry_run: false, amount: 125000, project_id: "proj_8812345" });
    expect(screen.getByText("Is active")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument();
    expect(screen.getByText("125,000")).toBeInTheDocument();
    expect(screen.getByText("Project ID")).toBeInTheDocument();
    expect(screen.getByText("proj_8812345")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(container.querySelector("pre")).toBeNull();
  });

  it("indents nested objects one level and hides deeper ones behind a disclosure", async () => {
    ui({ project: { name: "Acme", owner: { email: "cs@nova.io", team: "CS" } } });
    expect(screen.getByText("Project")).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.queryByText("cs@nova.io")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show 2 fields/ }));
    expect(screen.getByText("cs@nova.io")).toBeInTheDocument();
    expect(screen.getByText("Team")).toBeInTheDocument();
  });

  it("renders scalar arrays as chips and flat object arrays as a table", () => {
    ui({
      missing_fields: ["phone", "arr"],
      results: [
        { check: "row_count", passed: true },
        { check: "columns", passed: false },
      ],
    });
    expect(screen.getByText("phone")).toBeInTheDocument();
    expect(screen.getByText("arr")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Check" })).toBeInTheDocument();
    expect(within(table).getByText("row_count")).toBeInTheDocument();
    expect(within(table).getByText("Yes")).toBeInTheDocument();
    expect(within(table).getByText("No")).toBeInTheDocument();
  });

  it("renders arrays of nested objects as a compact list", () => {
    ui({ tasks: [{ name: "Kickoff", owner: { name: "Sam" } }] });
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText("Kickoff")).toBeInTheDocument();
  });

  it("strips HTML to plain text and never renders markup", () => {
    const note =
      "<p><b>Migration check FAILED</b> for batch b1</p><ul><li>Row count 57 vs 60</li><li>Missing column: phone &amp; fax</li></ul><script>alert(1)</script>";
    const { container } = ui({ note });
    expect(container.querySelector("b, p > b, ul, script")).toBeNull();
    expect(container.textContent).not.toContain("<");
    expect(container.textContent).toContain("Migration check FAILED");
    expect(container.textContent).toContain("• Missing column: phone & fax");
    expect(container.textContent).not.toContain("alert");
  });

  it("expands JSON strings instead of showing them raw", () => {
    const { container } = ui({ payload: '{"tier":"enterprise","confirmed":true}' });
    expect(screen.getByText("Tier")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(container.textContent).not.toContain("{");
  });

  it("stripHtml handles breaks and entities", () => {
    expect(stripHtml("a<br/>b &lt;c&gt; &#39;d&#39;")).toBe("a\nb <c> 'd'");
  });
});

describe("plainError", () => {
  it("drops exception class names and retry tags", () => {
    expect(
      plainError("ToolFailed: rocketlane_list_tasks failed: permanent: 404: project 1001 not found"),
    ).toBe("rocketlane_list_tasks failed: 404: project 1001 not found");
    expect(plainError("Rocketlane project not created: transient: rocketlane 503")).toBe(
      "Rocketlane project not created: rocketlane 503",
    );
  });
});
