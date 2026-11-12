import { describe, expect, it } from "vitest";
import { phaseState } from "../ProgressTab";

const p = (x: Partial<Parameters<typeof phaseState>[0]>) =>
  ({ name: "Kickoff", done: 0, total: 3, in_progress: 0, blocked: 0, ...x }) as Parameters<
    typeof phaseState
  >[0];

describe("phase chip", () => {
  it("shows in progress as soon as a task is in progress or the phase is marked so", () => {
    expect(phaseState(p({ in_progress: 1 })).label).toBe("In progress");
    expect(phaseState(p({ status: "In Progress", started: true })).label).toBe("In progress");
    expect(phaseState(p({})).label).toBe("Not started");
  });
  it("tells a phase marked done in Rocketlane apart from one whose tasks are all done", () => {
    expect(phaseState(p({ done: 3 })).label).toBe("Done");
    const marked = phaseState(p({ done: 1, status: "Completed" }));
    expect(marked.label).toBe("Marked done");
    expect(marked.tip).toContain("2 task(s) still open");
  });
  it("blocked wins", () => {
    expect(phaseState(p({ blocked: 1, done: 3 })).label).toBe("Blocked");
    expect(phaseState(p({ status: "Blocked" })).tone).toBe("bad");
  });
});
