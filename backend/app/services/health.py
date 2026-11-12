"""Integration health for the agent status page: cheap read-only checks (the same calls `make preflight` makes),
run in a background thread every HEALTH_INTERVAL seconds in real mode. Requests only ever read the cached result.
The Anthropic check costs tokens, so it runs at most once per LLM_CHECK_EVERY seconds."""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.config import Settings, tier_rules

INTEGRATIONS = ("gmail", "retell", "rocketlane", "slack", "anthropic")
HEALTH_INTERVAL = 300
LLM_CHECK_EVERY = 3600
PAID = {"anthropic"}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


class HealthMonitor:
    def __init__(self, checks: dict[str, Callable[[], str]], clock: Callable[[], float] = time.time):
        self.checks, self.clock = checks, clock
        self._results: dict[str, dict] = {}
        self._results_lock = threading.Lock()  # held only to swap a result, never during a check
        self._pass = threading.Lock()          # one check pass at a time
        self._last_paid: dict[str, float] = {}

    def run_once(self) -> None:
        if not self._pass.acquire(blocking=False):
            return  # a pass is already running
        try:
            for name, fn in self.checks.items():
                now = self.clock()
                if name in PAID and name in self._last_paid and now - self._last_paid[name] < LLM_CHECK_EVERY:
                    continue
                if name in PAID:
                    self._last_paid[name] = now
                try:
                    ok, detail = True, str(fn() or "ok")
                except Exception as e:  # noqa: BLE001 - any failure is the health verdict
                    ok, detail = False, f"{type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}"
                with self._results_lock:
                    self._results[name] = {"name": name, "ok": ok, "detail": detail, "checked_at": iso(self.clock())}
        finally:
            self._pass.release()

    def snapshot(self) -> list[dict]:
        with self._results_lock:
            return [dict(self._results.get(n) or {"name": n, "ok": False, "detail": "first check pending",
                                                  "checked_at": None}) for n in self.checks]


def fake_monitor() -> HealthMonitor:
    m = HealthMonitor({n: (lambda: "fake provider (PROVIDER_MODE=fake)") for n in INTEGRATIONS})
    m.run_once()
    return m


def need(s: Settings, *names: str) -> None:
    missing = [n for n in names if not getattr(s, n)]
    if missing:
        raise RuntimeError(f"missing in .env: {', '.join(m.upper() for m in missing)}")


def real_checks(s: Settings) -> dict[str, Callable[[], str]]:
    """Read-only checks; each returns a detail string or raises. Also used by scripts/preflight.py."""
    def gmail() -> str:
        need(s, "google_client_id", "google_client_secret", "google_refresh_token", "cs_inbox_address")
        from app.providers.real.gmail import build_service
        p = build_service(s.google_client_id, s.google_client_secret, s.google_refresh_token).users().getProfile(
            userId="me").execute()
        if p["emailAddress"].lower() != s.cs_inbox_address.lower():
            raise RuntimeError(f"token is for {p['emailAddress']}, CS_INBOX_ADDRESS is {s.cs_inbox_address}")
        return f"inbox {p['emailAddress']}"

    def retell() -> str:
        need(s, "retell_api_key", "retell_agent_id", "retell_from_number")
        from retell import Retell
        agent = Retell(api_key=s.retell_api_key).agent.retrieve(s.retell_agent_id)
        hook = getattr(agent, "webhook_url", "")
        if not hook or not hook.startswith(s.public_base_url):
            raise RuntimeError(f"agent webhook_url {hook!r} does not match PUBLIC_BASE_URL")
        return f"agent {agent.agent_name!r}, webhook ok"

    def rocketlane() -> str:
        need(s, "rocketlane_api_key", "rocketlane_template_enterprise", "rocketlane_template_growth")
        from app.providers.real.rocketlane import RocketlaneClient
        rl = RocketlaneClient(s.rocketlane_base_url, s.rocketlane_api_key)
        if problems := rl.check_templates({t.value: r.template_id for t, r in tier_rules(s).items()}):
            raise RuntimeError("; ".join(problems))
        return "API reachable; tier templates exist"

    def slack() -> str:
        need(s, "slack_bot_token")
        from slack_sdk import WebClient
        a = WebClient(token=s.slack_bot_token).auth_test()
        return f"bot {a['user']} in workspace {a['team']}"

    def anthropic() -> str:
        need(s, "anthropic_api_key")
        from langchain_anthropic import ChatAnthropic
        ChatAnthropic(model=s.llm_model, api_key=s.anthropic_api_key, max_tokens=1).invoke(  # type: ignore[call-arg,arg-type]
            "ping")
        return f"{s.llm_model} reachable (1-token call)"

    return {"gmail": gmail, "retell": retell, "rocketlane": rocketlane, "slack": slack, "anthropic": anthropic}
