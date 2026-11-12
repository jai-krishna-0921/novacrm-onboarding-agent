import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/mocks/server";
import { renderApp } from "@/test/render";

describe("pages against the mock API", () => {
  it("runs list shows customers and the needs-a-human count", async () => {
    renderApp("/runs");
    expect(await screen.findByText("Larkspur Health")).toBeInTheDocument();
    const attn = screen.getByRole("tab", { name: /Needs a human/ });
    expect(within(attn).getByText(/\d+/)).toBeInTheDocument();
  });

  it("run detail shows the transcript under the tier decision as text", async () => {
    server.use(
      http.get("/runs/r-x", () =>
        HttpResponse.json({
          run: {
            id: "r-x",
            status: "TIER_CONFIRMED",
            customer_name: "Ostara Foods",
            plan_tier: "enterprise",
          },
          pending: null,
          audit: [
            {
              step: "decide_tier",
              status: "confirmed",
              ts: "2026-11-12T10:00:00Z",
              input: { transcript: "Agent: Which plan?\nUser: <img src=x onerror=alert(1)> Enterprise" },
              output: { tier: "enterprise" },
            },
          ],
        }),
      ),
    );
    const { container } = renderApp("/runs/r-x");
    expect(await screen.findByText("AE confirmed the Enterprise plan")).toBeInTheDocument();
    expect(screen.getByText("Call transcript")).toBeInTheDocument();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)> Enterprise/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("tier escalation: confirming without a name is blocked and nothing is posted", async () => {
    let posted = false;
    server.use(http.post("/runs/:id/resolve", () => ((posted = true), HttpResponse.json({ ok: true }))));
    renderApp("/runs/r-2b8c44");
    await userEvent.click(await screen.findByRole("radio", { name: /Enterprise/ }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm tier and continue/ }));
    expect(await screen.findByText(/Enter your name first/)).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it("tier escalation: a tier is required before confirming", async () => {
    let posted = false;
    server.use(http.post("/runs/:id/resolve", () => ((posted = true), HttpResponse.json({ ok: true }))));
    renderApp("/runs/r-2b8c44");
    await userEvent.type(await screen.findByLabelText("Your name"), "Dana");
    await userEvent.click(screen.getByRole("button", { name: /Confirm tier and continue/ }));
    expect(await screen.findByText("Pick Enterprise or Growth first.")).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it("add AE: a duplicate email shows the 409 message on the email field", async () => {
    renderApp("/aes");
    await userEvent.click(await screen.findByRole("button", { name: "Add AE" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Work email"), "priya.raman@novacrm.com");
    await userEvent.type(within(dialog).getByLabelText("Full name"), "Priya Raman");
    await userEvent.type(within(dialog).getByLabelText("Phone"), "+91 97899 98350");
    expect(within(dialog).getByText("Saved as +919789998350")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Add AE" }));
    expect(await within(dialog).findByText("An AE with this email already exists.")).toBeInTheDocument();
  });

  it("add AE: an invalid phone is blocked before any request", async () => {
    let posted = false;
    server.use(http.post("/aes", () => ((posted = true), HttpResponse.json({}, { status: 201 }))));
    renderApp("/aes");
    await userEvent.click(await screen.findByRole("button", { name: "Add AE" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Work email"), "new.ae@novacrm.com");
    await userEvent.type(within(dialog).getByLabelText("Full name"), "New AE");
    await userEvent.type(within(dialog).getByLabelText("Phone"), "97899 98350");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add AE" }));
    expect(within(dialog).getByText(/Start with \+ and the country code/)).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it("migration page highlights problem rows", async () => {
    renderApp("/migration");
    await waitFor(
      () =>
        expect(document.querySelectorAll('tr[data-tone="bad"], tr[data-tone="warn"]').length).toBeGreaterThan(
          0,
        ),
      {
        timeout: 3000,
      },
    );
    await userEvent.click(screen.getByRole("switch", { name: "Only rows with problems" }));
    await waitFor(() => expect(document.querySelectorAll('tr[data-tone="ok"]').length).toBe(0));
  });

  it("agents page shows the three agents", async () => {
    renderApp("/agents");
    expect(await screen.findByText("Agent 1: Intake and routing")).toBeInTheDocument();
    expect(screen.getByText("Agent 3: Project tracking")).toBeInTheDocument();
    expect(screen.getByText(/Expires in \d+ h/)).toBeInTheDocument();
  });

  it("agents page: an integration not checked yet is pending, not failing", async () => {
    renderApp("/agents");
    await screen.findByText("Agent 1: Intake and routing");
    const row = (name: string) =>
      screen.getAllByRole("row").find((r) => r.firstElementChild?.textContent === name)!;
    const pending = row("Anthropic");
    expect(within(pending).getByText("Pending")).toBeInTheDocument();
    const failing = row("Slack");
    expect(within(failing).getByText("Failing")).toBeInTheDocument();
  });
});
