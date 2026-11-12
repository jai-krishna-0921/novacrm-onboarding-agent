import { Eye, EyeOff } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { detailMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ErrorBox } from "@/components/ui/empty-state";
import { Field, Input } from "@/components/ui/input";
import { Brand } from "@/components/layout/Sidebar";

/** Where to go after sign-in. The server sanitises `next` again (dashboard paths only). */
export function wantedNext(search: string, hash: string): string {
  const n = new URLSearchParams(search).get("next") || "/ui";
  const safe = n.startsWith("/ui") && !n.startsWith("/ui/login") && !n.includes("\\") ? n : "/ui";
  return safe.includes("#") || !hash ? safe : safe + hash;
}

export function LoginPage() {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const userRef = useRef<HTMLInputElement>(null);
  const passRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    document.title = "Sign in | NovaCRM Onboarding";
    userRef.current?.focus();
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!user.trim() || !pass) {
      setError("Enter your username and password.");
      (user.trim() ? passRef : userRef).current?.focus();
      return;
    }
    setError("");
    setBusy(true);
    try {
      const r = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          username: user.trim(),
          password: pass,
          next: wantedNext(location.search, location.hash),
        }),
      });
      let data: unknown = null;
      try {
        data = await r.json();
      } catch {
        /* non-JSON */
      }
      if (r.ok) {
        const next =
          data && typeof data === "object" && "next" in data
            ? String((data as { next: unknown }).next)
            : "/ui";
        window.location.replace(next || "/ui");
        return;
      }
      const msg = detailMessage(data, "");
      setError(
        r.status === 401
          ? "Incorrect username or password."
          : r.status === 429
            ? msg || "Too many failed attempts. Try again in a few minutes."
            : r.status === 503
              ? "Sign-in is not configured on this server. Set ADMIN_PASSWORD and restart."
              : msg || `Sign-in failed (${r.status}). Try again.`,
      );
      passRef.current?.select();
    } catch {
      setError("Could not reach the server. Check your connection and try again.");
    }
    setBusy(false);
  };

  return (
    <div className="grid min-h-dvh place-items-center bg-canvas px-4 py-10">
      <main className="w-full max-w-[400px]">
        <div className="mb-6 flex justify-center">
          <Brand />
        </div>
        <form
          onSubmit={submit}
          noValidate
          className="rounded-card border border-line bg-surface p-6 shadow-[0_1px_2px_rgb(16_24_40/0.04),0_12px_32px_-12px_rgb(16_24_40/0.12)]"
        >
          <h1 className="text-[20px] font-semibold tracking-[-0.015em] text-ink">Sign in</h1>
          <p className="mt-1 text-sm text-ink-3">Follow onboarding runs and handle escalations.</p>
          <div className="mt-5 flex flex-col gap-4">
            {error ? <ErrorBox error={error} /> : null}
            <Field label="Username">
              {(p) => (
                <Input
                  {...p}
                  ref={userRef}
                  name="username"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={user}
                  aria-invalid={!!error && !user.trim()}
                  onChange={(e) => {
                    setUser(e.target.value);
                    setError("");
                  }}
                />
              )}
            </Field>
            <Field label="Password">
              {(p) => (
                <div className="relative">
                  <Input
                    {...p}
                    ref={passRef}
                    name="password"
                    type={show ? "text" : "password"}
                    autoComplete="current-password"
                    value={pass}
                    aria-invalid={!!error}
                    className="pr-10"
                    onChange={(e) => {
                      setPass(e.target.value);
                      setError("");
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => setShow((s) => !s)}
                    aria-pressed={show}
                    aria-label={show ? "Hide password" : "Show password"}
                    className="absolute top-1/2 right-1.5 grid size-7 -translate-y-1/2 place-items-center rounded-[6px] text-ink-3 hover:bg-idle-soft hover:text-ink"
                  >
                    {show ? (
                      <EyeOff className="size-4" aria-hidden />
                    ) : (
                      <Eye className="size-4" aria-hidden />
                    )}
                  </button>
                </div>
              )}
            </Field>
            <Button
              type="submit"
              variant="primary"
              size="lg"
              loading={busy}
              className="mt-1 w-full justify-center"
            >
              {busy ? "Signing in" : "Sign in"}
            </Button>
          </div>
        </form>
        <p className="mt-4 text-center text-[13px] text-ink-3">
          For the customer success team. Sessions end after 12 hours.
        </p>
      </main>
    </div>
  );
}
