import { describe, expect, it } from "vitest";
import { describe as label, parseTranscript, speakerRole } from "../activity";

describe("activity labels and transcript", () => {
  it("labels steps in plain language and hides tool rows from key events", () => {
    expect(
      label({ step: "decide_tier", status: "confirmed", output: { tier: "enterprise" } } as never).title,
    ).toBe("AE confirmed the Enterprise plan");
    expect(label({ step: "tool:slack_post_message", status: "ok" } as never).tech).toBe(true);
  });
  it("splits a transcript into speaker turns", () => {
    const t = parseTranscript("Agent: Which plan?\nUser: Enterprise.\nthree year term");
    expect(t).toEqual([
      { speaker: "Agent", text: "Which plan?" },
      { speaker: "User", text: "Enterprise.\nthree year term" },
    ]);
    expect(speakerRole("User")).toBe("ae");
    expect(parseTranscript(undefined)).toEqual([]);
  });
  it("keeps markup in a transcript as plain text", () => {
    expect(parseTranscript("User: <img src=x onerror=alert(1)>")[0].text).toBe(
      "<img src=x onerror=alert(1)>",
    );
  });
});
