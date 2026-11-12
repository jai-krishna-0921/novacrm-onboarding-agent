"""In-app login for the dashboard and its API (replaces Caddy basic auth).

One admin account from settings. A login sets a signed, HttpOnly, SameSite=Strict session cookie (12 h). Every
route except webhooks, /healthz, the login page/assets and /auth/login needs it. State-changing requests also need
a same-origin Origin/Referer (CSRF defence on top of SameSite). Failed logins are rate limited per client IP, in
memory. Without ADMIN_PASSWORD nothing protected is served (503): the app never falls open.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

log = logging.getLogger("onboarding")

COOKIE = "nova_session"
SESSION_SECONDS = 12 * 3600
MAX_FAILURES, FAILURE_WINDOW = 5, 300
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC = {"/healthz", "/ui/login", "/auth/login", "/auth/logout", "/favicon.ico"}
PUBLIC_PREFIXES = ("/webhooks/", "/ui/assets/")
_NEXT = re.compile(r"/ui(?:[/?#][\x21-\x7e]*)?")


def safe_next(raw: str | None) -> str:
    """Where to go after login: only a dashboard path on this origin (no open redirect)."""
    if not raw or not _NEXT.fullmatch(raw) or "\\" in raw or raw.startswith("/ui/login"):
        return "/ui"
    return raw


class Credentials(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=200)
    next: str | None = Field(default=None, max_length=2000)


class Auth:
    def __init__(self, settings: Callable):
        self.settings = settings
        self._random_secret = secrets.token_bytes(32)
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        # Logged-out session ids -> their expiry (pruned once expired). In memory: one app process.
        self._revoked: dict[str, int] = {}

    # ---------- session token: base64url("user|expiry|session id") + "." + HMAC ----------
    def _key(self) -> bytes:
        s = self.settings()
        secret = s.session_secret.encode() if s.session_secret else self._random_secret
        # Bound to the credentials: changing the admin password logs every session out.
        return hmac.new(secret, f"session:{s.admin_username}:{s.admin_password}".encode(), hashlib.sha256).digest()

    def sign(self, user: str, now: float | None = None) -> str:
        exp = int((now if now is not None else time.time()) + SESSION_SECONDS)
        body = base64.urlsafe_b64encode(f"{user}|{exp}|{secrets.token_hex(16)}".encode()).decode().rstrip("=")
        return f"{body}.{hmac.new(self._key(), body.encode(), hashlib.sha256).hexdigest()}"

    def _session(self, token: str | None, now: float | None = None) -> tuple[str, int, str] | None:
        """(user, expiry, session id) of a valid, unexpired, not revoked token, else None."""
        if not token or token.count(".") != 1:
            return None
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, hmac.new(self._key(), body.encode(), hashlib.sha256).hexdigest()):
            return None
        try:
            user, exp, sid = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().rsplit("|", 2)
            expired = int(exp) <= (now if now is not None else time.time())
        except ValueError:
            return None
        if expired or user != self.settings().admin_username or sid in self._revoked:
            return None
        return user, int(exp), sid

    def verify(self, token: str | None, now: float | None = None) -> str | None:
        session = self._session(token, now)
        return session[0] if session else None

    def revoke(self, token: str | None) -> None:
        """Server-side logout: the token (and any copy of it) is refused from now on, until it would expire."""
        t = time.time()
        self._revoked = {sid: exp for sid, exp in self._revoked.items() if exp > t}
        if session := self._session(token):
            self._revoked[session[2]] = session[1]

    def check_credentials(self, username: str, password: str) -> bool:
        s = self.settings()
        # Both compares always run (no short circuit), each constant time.
        u = hmac.compare_digest(username.encode(), s.admin_username.encode())
        p = hmac.compare_digest(password.encode(), s.admin_password.encode())
        return u & p

    # ---------- failed-login rate limit ----------
    def _window(self, ip: str, now: float) -> deque[float]:
        q = self._failures[ip]
        while q and q[0] <= now - FAILURE_WINDOW:
            q.popleft()
        return q

    def record_failure(self, ip: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._window(ip, now).append(now)

    def retry_after(self, ip: str, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        q = self._window(ip, now)
        if not q:
            self._failures.pop(ip, None)
        return int(q[0] + FAILURE_WINDOW - now) + 1 if len(q) >= MAX_FAILURES else 0

    # ---------- request helpers ----------
    def client_ip(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if self.settings().trust_proxy and fwd:
            return fwd.split(",")[-1].strip()  # the hop Caddy appended; earlier entries are client-supplied
        return request.client.host if request.client else "unknown"

    def is_https(self, request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        return bool(self.settings().trust_proxy and request.headers.get("x-forwarded-proto", "").lower() == "https")

    @staticmethod
    def same_origin(request: Request) -> bool:
        src = request.headers.get("origin") or request.headers.get("referer")
        host = request.headers.get("host")
        if not src or not host or src == "null":
            return False
        return urlsplit(src).netloc.lower() == host.lower()

    def enabled(self) -> bool:
        s = self.settings()
        return not (s.auth_disabled and s.provider_mode == "fake")

    # ---------- middleware ----------
    async def guard(self, request: Request, call_next):
        if not self.enabled():
            return await call_next(request)
        path = request.url.path
        if request.method not in SAFE_METHODS and not path.startswith("/webhooks/") and not self.same_origin(request):
            return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
        if path in PUBLIC or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        if not self.settings().admin_password:
            return JSONResponse({"detail": "login is not configured (set ADMIN_PASSWORD)"}, status_code=503)
        user = self.verify(request.cookies.get(COOKIE))
        if user is None:
            if request.method == "GET" and path == "/ui":  # the page; /ui/config etc. are API calls
                target = path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/ui/login?next={quote(safe_next(target), safe='/')}", status_code=303)
            return JSONResponse({"detail": "not signed in"}, status_code=401)
        request.state.user = user
        return await call_next(request)

    # ---------- routes ----------
    def router(self, login_page: Callable[[], Response]) -> APIRouter:
        r = APIRouter(include_in_schema=False)

        @r.post("/auth/login")
        def do_login(body: Credentials, request: Request, response: Response):
            if not self.settings().admin_password:
                raise HTTPException(503, "login is not configured (set ADMIN_PASSWORD)")
            ip = self.client_ip(request)
            wait = self.retry_after(ip)
            if wait:
                return JSONResponse({"detail": f"Too many failed attempts. Try again in {wait // 60 + 1} min."},
                                    status_code=429, headers={"Retry-After": str(wait)})
            if not self.check_credentials(body.username, body.password):
                self.record_failure(ip)
                log.warning("dashboard login failed from %s", ip)
                raise HTTPException(401, "Incorrect username or password.")
            self._failures.pop(ip, None)
            response.set_cookie(COOKIE, self.sign(body.username), max_age=SESSION_SECONDS, path="/", httponly=True,
                                secure=self.is_https(request), samesite="strict")
            return {"user": body.username, "next": safe_next(body.next)}

        @r.post("/auth/logout")
        def do_logout(request: Request, response: Response):
            self.revoke(request.cookies.get(COOKIE))
            response.delete_cookie(COOKIE, path="/", httponly=True, secure=self.is_https(request), samesite="strict")
            return {"ok": True}

        @r.get("/auth/me")
        def me(request: Request):
            return {"user": getattr(request.state, "user", None) or self.settings().admin_username}

        @r.get("/ui/login")
        def page(request: Request):
            if self.enabled() and self.settings().admin_password and self.verify(request.cookies.get(COOKIE)):
                return RedirectResponse("/ui", status_code=303)
            return login_page()

        return r
