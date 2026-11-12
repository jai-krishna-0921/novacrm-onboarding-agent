import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/mocks/server";
import { renderApp } from "@/test/render";
import { parseTranscript } from "../CallsTab";

const openTab = async (name: RegExp) => userEvent.click(await screen.findByRole("tab", { name }));

describe("AE calls tab", () => {
  it("lists each attempt with outcome, player only when recorded, and a collapsible transcript", async () => {
    const { container } = renderApp("/runs/r-7f3a91");
    await openTab(/AE calls/);
    expect(await screen.findByText("Attempt 1")).toBeInTheDocument();
    expect(screen.getByText("Attempt 2")).toBeInTheDocument();
    expect(screen.getByText("No answer")).toBeInTheDocument();
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
    const audios = container.querySelectorAll("audio");
    expect(audios).toHaveLength(1);
    expect(audios[0].getAttribute("src")).toBe("/runs/r-7f3a91/calls/call_a2/recording");
    expect(audios[0].getAttribute("preload")).toBe("none");
    expect(screen.queryByText(/Enterprise, the full package/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Show transcript/ }));
    expect(screen.getByText("Enterprise, the full package.")).toBeInTheDocument();
  });

  it("shows an empty state when there are no calls", async () => {
    renderApp("/runs/r-2b8c44");
    await openTab(/AE calls/);
    expect(await screen.findByText("No calls yet")).toBeInTheDocument();
  });

  it("renders transcript markup as text, never HTML", async () => {
    server.use(
      http.get("/runs/:id/calls", () =>
        HttpResponse.json([
          {
            call_id: "c1",
            attempt: 1,
            outcome: "unclear",
            transcript: "Agent: Which plan?\nAE: <img src=x onerror=alert(1)> not sure",
            started_at: null,
            duration_ms: null,
            has_recording: false,
          },
        ]),
      ),
    );
    const { container } = renderApp("/runs/r-7f3a91");
    await openTab(/AE calls/);
    expect(await screen.findByText("Unclear")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show transcript/ }));
    expect(screen.getByText(/<img src=x onerror=alert\(1\)> not sure/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("parses speakers", () => {
    expect(parseTranscript("Agent: hi\nAE: hello\nnoise")).toEqual([
      { who: "agent", text: "hi" },
      { who: "ae", text: "hello" },
      { who: null, text: "noise" },
    ]);
  });
});

describe("customer database card", () => {
  it("connects, shows the display string and row count, never echoes the password, then disconnects", async () => {
    const urls: string[] = [];
    server.events.on("request:start", ({ request }) => void urls.push(request.url));
    renderApp("/runs/r-7f3a91");
    await openTab(/Migration check/);
    expect(await screen.findByText(/Until then the check uses the sample export/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Host"), "postgres");
    await userEvent.type(screen.getByLabelText("Database"), "mavis_crm");
    await userEvent.type(screen.getByLabelText("Username"), "mavis_ro");
    const pw = screen.getByLabelText("Password");
    expect(pw).toHaveAttribute("type", "password");
    await userEvent.type(pw, "readonly");
    await userEvent.click(screen.getByRole("button", { name: /Test connection & save/ }));
    expect(await screen.findByText("postgresql://mavis_ro@postgres:5432/mavis_crm")).toBeInTheDocument();
    expect(screen.getByText(/Connected by admin/)).toBeInTheDocument();
    expect(screen.getByText(/Read \d+ contacts/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("readonly");
    expect(urls.some((u) => u.includes("readonly"))).toBe(false);
    // the verdict preview reads the run-scoped comparison
    await waitFor(() =>
      expect(urls.some((u) => u.includes("/migration/summary?run_id=r-7f3a91"))).toBe(true),
    );

    await userEvent.click(screen.getByRole("button", { name: /Disconnect/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.click(within(dlg).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByRole("button", { name: /Test connection & save/ })).toBeInTheDocument();
    server.events.removeAllListeners();
  });

  it("shows the server's error inline when the connection test fails and clears the password", async () => {
    renderApp("/runs/r-c4e7a0");
    await openTab(/Migration check/);
    await userEvent.type(await screen.findByLabelText("Host"), "postgres");
    await userEvent.type(screen.getByLabelText("Database"), "mavis_crm");
    await userEvent.type(screen.getByLabelText("Username"), "mavis_ro");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-pass");
    await userEvent.click(screen.getByRole("button", { name: /Test connection & save/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/password authentication failed/);
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("blocks saving with missing fields: nothing is sent", async () => {
    let sent = false;
    server.use(http.put("/runs/:id/migration-source", () => ((sent = true), HttpResponse.json({}))));
    renderApp("/runs/r-5a6f19");
    await openTab(/Migration check/);
    await userEvent.type(await screen.findByLabelText("Host"), "postgres");
    await userEvent.click(screen.getByRole("button", { name: /Test connection & save/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Fill in host, database, username and password/,
    );
    expect(sent).toBe(false);
  });
});
