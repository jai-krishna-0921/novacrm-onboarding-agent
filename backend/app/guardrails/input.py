import re

from app.llm.schemas import DealFields

REQUIRED = ("customer_name", "customer_email", "ae_name", "ae_email")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def validate_deal(fields: DealFields) -> list[str]:
    """Return human-readable issues; empty list means the deal can proceed."""
    issues: list[str] = []
    for name in REQUIRED:
        value = (getattr(fields, name) or "").strip()
        if not value:
            issues.append(f"{name}: missing")
        elif name.endswith("_email") and not EMAIL_RE.match(value):
            issues.append(f"{name}: invalid format ({value})")
    return issues
