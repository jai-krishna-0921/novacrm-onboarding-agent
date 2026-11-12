import type { z } from "zod";

// Same-origin JSON client. The session is an HttpOnly cookie; POSTs are same-origin so the server's CSRF
// Origin check passes. Any 401 sends the user to the login page and back to this exact view afterwards.

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let onUnauthorized: () => void = () => {
  const here = window.location.pathname + window.location.search;
  if (window.location.pathname.startsWith("/ui/login")) return;
  window.location.assign(`/ui/login?next=${encodeURIComponent(here)}`);
};

/** Tests swap the redirect. */
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

/** FastAPI errors: {detail: string} or {detail: [{loc, msg}]} for validation. */
export function detailMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object" || !("detail" in data)) return fallback;
  const d = (data as { detail: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    const parts = d.map((e) => {
      if (e && typeof e === "object" && "msg" in e) {
        const loc = Array.isArray((e as { loc?: unknown }).loc)
          ? (e as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".")
          : "";
        return loc ? `${loc}: ${String((e as { msg: unknown }).msg)}` : String((e as { msg: unknown }).msg);
      }
      return String(e);
    });
    return parts.join("; ") || fallback;
  }
  return fallback;
}

type Opts = { method?: "GET" | "POST" | "PATCH"; body?: unknown; signal?: AbortSignal };

export async function request(path: string, opts: Opts = {}): Promise<unknown> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers,
    credentials: "same-origin",
    signal: opts.signal,
  };
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("Could not reach the server. Check your connection.", 0);
  }
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError("Signed out", 401);
  }
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) throw new ApiError(detailMessage(data, `Request failed (${res.status})`), res.status);
  return data;
}

/** Fetch and validate. A shape mismatch is a clear error instead of a broken page. */
export async function api<T extends z.ZodType>(
  path: string,
  schema: T,
  opts: Opts = {},
): Promise<z.infer<T>> {
  const data = await request(path, opts);
  const parsed = schema.safeParse(data);
  if (!parsed.success) {
    if (import.meta.env.DEV) console.error(`Unexpected response from ${path}`, parsed.error.issues);
    throw new ApiError(`Unexpected response from the server (${path.split("?")[0]})`, 200);
  }
  return parsed.data;
}

export const enc = encodeURIComponent;
