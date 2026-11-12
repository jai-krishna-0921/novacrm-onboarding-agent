import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { server } from "@/mocks/server";
import { api, ApiError, detailMessage, request, setUnauthorizedHandler } from "../api";

describe("api client", () => {
  afterEach(() => setUnauthorizedHandler(() => {}));

  it("calls the unauthorized handler and throws on 401", async () => {
    const onAuth = vi.fn();
    setUnauthorizedHandler(onAuth);
    server.use(http.get("/runs", () => HttpResponse.json({ detail: "not signed in" }, { status: 401 })));
    await expect(request("/runs")).rejects.toMatchObject({ status: 401 });
    expect(onAuth).toHaveBeenCalledOnce();
  });

  it("does not redirect on other errors and surfaces the server detail", async () => {
    const onAuth = vi.fn();
    setUnauthorizedHandler(onAuth);
    server.use(
      http.post("/runs/x/resolve", () =>
        HttpResponse.json({ detail: "tier must be enterprise or growth" }, { status: 422 }),
      ),
    );
    const e = (await request("/runs/x/resolve", { method: "POST", body: {} }).catch(
      (x: unknown) => x,
    )) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(422);
    expect(e.message).toBe("tier must be enterprise or growth");
    expect(onAuth).not.toHaveBeenCalled();
  });

  it("formats FastAPI validation errors", () => {
    expect(detailMessage({ detail: [{ loc: ["body", "phone"], msg: "invalid" }] }, "x")).toBe(
      "phone: invalid",
    );
    expect(detailMessage(null, "fallback")).toBe("fallback");
  });

  it("rejects a response that does not match the schema instead of rendering garbage", async () => {
    server.use(http.get("/thing", () => HttpResponse.json({ nope: true })));
    await expect(api("/thing", z.object({ id: z.string() }))).rejects.toThrow(/Unexpected response/);
  });

  it("sends JSON with same-origin credentials", async () => {
    let seen: { ct: string | null; body: unknown } | null = null;
    server.use(
      http.post("/echo", async ({ request: r }) => {
        seen = { ct: r.headers.get("content-type"), body: await r.json() };
        return HttpResponse.json({ ok: true });
      }),
    );
    await request("/echo", { method: "POST", body: { a: 1 } });
    expect(seen).toEqual({ ct: "application/json", body: { a: 1 } });
  });
});
