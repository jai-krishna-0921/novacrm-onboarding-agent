// Login page. Same-origin JSON POST to /auth/login; the server sets an HttpOnly session cookie and returns a
// sanitised `next` (dashboard paths only). Text is set with textContent only.
"use strict";

const form = document.getElementById("login-form");
const user = document.getElementById("username");
const pass = document.getElementById("password");
const toggle = document.getElementById("pw-toggle");
const submit = document.getElementById("login-submit");
const error = document.getElementById("login-error");

// The hash survives the server redirect (/ui#/runs/x -> /ui/login?next=/ui#/runs/x), so keep it.
function wanted() {
  const n = new URLSearchParams(location.search).get("next") || "/ui";
  return n.includes("#") || !location.hash ? n : n + location.hash;
}

function showError(msg) {
  error.textContent = msg;
  error.hidden = !msg;
  const bad = !!msg;
  user.setAttribute("aria-invalid", String(bad && !user.value.trim()));
  pass.setAttribute("aria-invalid", String(bad));
}

function busy(on) {
  submit.disabled = on;
  submit.classList.toggle("loading", on);
  submit.querySelector(".label").textContent = on ? "Signing in…" : "Sign in";
}

toggle.addEventListener("click", () => {
  const show = pass.type === "password";
  pass.type = show ? "text" : "password";
  toggle.textContent = show ? "Hide" : "Show";
  toggle.setAttribute("aria-pressed", String(show));
  toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
  pass.focus();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!user.value.trim() || !pass.value) {
    showError("Enter your username and password.");
    (user.value.trim() ? pass : user).focus();
    return;
  }
  showError("");
  busy(true);
  try {
    const r = await fetch("/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ username: user.value.trim(), password: pass.value, next: wanted() }),
    });
    let data = null;
    try { data = await r.json(); } catch (_) { /* non-JSON */ }
    if (r.ok) {
      submit.querySelector(".label").textContent = "Signed in";
      location.replace((data && data.next) || "/ui");
      return;
    }
    const msg = data && typeof data.detail === "string" ? data.detail : "";
    showError(r.status === 401 ? "Incorrect username or password."
      : r.status === 429 ? msg || "Too many failed attempts. Try again in a few minutes."
      : r.status === 503 ? "Sign-in is not configured on this server. Set ADMIN_PASSWORD and restart."
      : msg || `Sign-in failed (${r.status}). Try again.`);
    pass.select();
  } catch (_) {
    showError("Could not reach the server. Check your connection and try again.");
  }
  busy(false);
});

for (const i of [user, pass]) i.addEventListener("input", () => { if (!error.hidden) showError(""); });
