import { ApiError } from "./api";

// Phone input for the AE directory. Accepts what people type ("+91 97899 98350", "(415) 555-0134" with a
// leading +1, "0044 20 7946 0958") and normalises to E.164. The server validates again.

export function normalisePhone(raw: string): string {
  let s = raw.trim().replace(/[\s().-]/g, "");
  if (s.startsWith("00")) s = `+${s.slice(2)}`;
  return s;
}

export function isE164(s: string): boolean {
  return /^\+[1-9]\d{7,14}$/.test(s);
}

export function phoneHint(raw: string): { ok: boolean; text: string } {
  if (!raw.trim()) return { ok: false, text: "Include the country code, for example +91 97899 98350." };
  const n = normalisePhone(raw);
  if (!n.startsWith("+")) return { ok: false, text: "Start with + and the country code, for example +91." };
  if (/[^+\d]/.test(n)) return { ok: false, text: "Use digits only, with spaces or dashes if you like." };
  if (!isE164(n)) return { ok: false, text: `${n} is not a valid international number (8 to 15 digits).` };
  return { ok: true, text: `Saved as ${n}` };
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export type AeForm = { email: string; name: string; phone: string; active: boolean };
export type AeErrors = Partial<Record<"email" | "name" | "phone", string>>;

export function validateAe(
  f: AeForm,
  opts: { requireEmail?: boolean; requirePhone?: boolean } = {},
): AeErrors {
  const e: AeErrors = {};
  const requireEmail = opts.requireEmail ?? true;
  const requirePhone = opts.requirePhone ?? true;
  if (requireEmail) {
    if (!f.email.trim()) e.email = "Enter the AE's work email.";
    else if (!EMAIL.test(f.email.trim())) e.email = "Enter a valid email address.";
  }
  if (!f.name.trim()) e.name = "Enter the AE's name.";
  else if (f.name.trim().length > 200) e.name = "Keep the name under 200 characters.";
  if (requirePhone || f.phone.trim()) {
    const h = phoneHint(f.phone);
    if (!h.ok) e.phone = h.text;
  }
  return e;
}

/** Error message for a failed save. 409 and 422 come from the API contract. */
export function aeSaveError(e: unknown): { field?: keyof AeErrors; message: string } {
  if (e instanceof ApiError) {
    if (e.status === 409) return { field: "email", message: "An AE with this email already exists." };
    if (e.status === 422) {
      const m = e.message.toLowerCase();
      const field = m.includes("phone")
        ? "phone"
        : m.includes("email")
          ? "email"
          : m.includes("name")
            ? "name"
            : undefined;
      return { field, message: e.message };
    }
    return { message: e.message };
  }
  return { message: e instanceof Error ? e.message : String(e) };
}
