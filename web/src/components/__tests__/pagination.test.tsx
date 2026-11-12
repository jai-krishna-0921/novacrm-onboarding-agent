import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { clampPage, Pagination, readPageSize, usePaging } from "../ui/pagination";
import { TooltipProvider } from "../ui/tooltip";

function Harness({ total }: { total: number }) {
  const p = usePaging("test");
  const [t] = useState(total);
  return (
    <Pagination total={t} page={p.page} size={p.size} onPage={p.setPage} onSize={p.setSize} noun="runs" />
  );
}
const ui = (total: number) =>
  render(
    <TooltipProvider>
      <Harness total={total} />
    </TooltipProvider>,
  );

describe("pagination", () => {
  it("shows the range and pages forward and back", async () => {
    ui(34);
    expect(screen.getByText("1-10 of 34 runs")).toBeInTheDocument();
    const prev = screen.getByRole("button", { name: "Previous page" });
    expect(prev).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("11-20 of 34 runs")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("31-34 of 34 runs")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("changes rows per page, resets to page 1 and remembers the choice", async () => {
    ui(34);
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Rows per page" }));
    await userEvent.click(await screen.findByRole("option", { name: "5" }));
    expect(screen.getByText("1-5 of 34 runs")).toBeInTheDocument();
    expect(readPageSize("test")).toBe(5);
  });

  it("offers 5, 10, 25 and 50 and nothing smaller than 5", async () => {
    ui(3);
    await userEvent.click(screen.getByRole("combobox", { name: "Rows per page" }));
    const opts = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(opts).toEqual(["5", "10", "25", "50"]);
  });

  it("ignores a stored size that is not offered", () => {
    localStorage.setItem("dash.pp.x", "3");
    expect(readPageSize("x")).toBe(10);
  });

  it("renders nothing when there are no rows", () => {
    const { container } = ui(0);
    expect(container).toBeEmptyDOMElement();
  });

  it("clamps a page past the end", () => {
    expect(clampPage(9, 12, 5)).toBe(2);
    expect(clampPage(-1, 12, 5)).toBe(0);
  });
});
