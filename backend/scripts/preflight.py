"""`make preflight`: read-only checks of every integration before a real run. Never prints secrets."""

import sys

from app.config import PLACEHOLDER_CSM, PLACEHOLDER_CUSTOMER, PLACEHOLDER_PM, Settings, tier_rules
from app.services.health import real_checks

s = Settings()
results: list[tuple[str, bool, str]] = []


def check(name, fn):
    try:
        detail = fn() or "ok"
        results.append((name, True, detail))
    except Exception as e:  # noqa: BLE001
        results.append((name, False, f"{type(e).__name__}: {str(e)[:160]}"))


def need(*names):
    missing = [n for n in names if not getattr(s, n)]
    if missing:
        raise RuntimeError(f"missing in .env: {', '.join(m.upper() for m in missing)}")


def postgres():
    from app.db.store import PgStore
    st = PgStore(s.database_url)
    ae = st.lookup_ae(s.demo_ae_email) if s.demo_ae_email else None
    if not ae:
        raise RuntimeError("demo AE not in ae_directory (set DEMO_AE_* and run `make seed`)")
    return f"AE directory has {ae['email']} (***{ae['phone'][-4:]})"


def rocketlane():
    need("rocketlane_api_key", "rocketlane_template_enterprise", "rocketlane_template_growth",
         "rocketlane_owner_email", "rocketlane_pm_email", "rocketlane_csm_enterprise", "rocketlane_csm_growth")
    from app.providers.real.rocketlane import RocketlaneClient
    rl = RocketlaneClient(s.rocketlane_base_url, s.rocketlane_api_key)
    rules = tier_rules(s)
    problems = rl.check_templates({t.value: r.template_id for t, r in rules.items()})
    problems += rl.check_placeholders([PLACEHOLDER_PM, PLACEHOLDER_CSM, PLACEHOLDER_CUSTOMER])
    users = {u["email"].lower(): u for u in rl._get_all("/users")}
    for label in ("rocketlane_owner_email", "rocketlane_pm_email", "rocketlane_csm_enterprise", "rocketlane_csm_growth"):
        email = getattr(s, label).lower()
        if email not in users:
            problems.append(f"{label.upper()} {email} is not a Rocketlane user")
    if problems:
        raise RuntimeError("; ".join(problems))
    return "templates are PROJECT templates; owner/PM/CSMs and role placeholders exist"


checks = real_checks(s)  # shared with the dashboard's integration health (app/services/health.py)
anthropic, retell, slack, gmail = checks["anthropic"], checks["retell"], checks["slack"], checks["gmail"]


def public_url():
    need("public_base_url")
    import httpx
    if "<" in s.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL is still the placeholder")
    return f"healthz -> {httpx.get(s.public_base_url.rstrip('/') + '/healthz', timeout=10).status_code}"


for name, fn in [("postgres", postgres), ("rocketlane", rocketlane), ("anthropic", anthropic), ("retell", retell),
                 ("slack", slack), ("gmail", gmail), ("tunnel", public_url)]:
    check(name, fn)
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<11} {detail}")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
