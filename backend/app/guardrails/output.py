import re
import unicodedata

from app.config import Tier, TierRule

PRICE_RE = re.compile(r"[$₹€£]\s?\d|\b(USD|INR|EUR|GBP|Rs)\b\.?|\bper (month|year)\b", re.I)
_MONTH = (r"(january|february|march|april|may|june|july|august|september|october|november|december"
          r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b\.?")
DATE_RE = re.compile(rf"\b\d{{1,2}}(st|nd|rd|th)?\s+{_MONTH}\b|\b{_MONTH}\s+\d{{1,2}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b"
                     r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
DURATION_RE = re.compile(r"\b(\d{1,3})[- ]day", re.I)


def check_welcome(text: str, customer_name: str, rule: TierRule) -> list[str]:
    """Deterministic fact checks on the LLM-drafted welcome message."""
    problems: list[str] = []
    if customer_name.lower() not in text.lower():
        problems.append("missing customer name")
    # The customer's own name ("Monday Labs", "14-Day Fitness") must not trip the fact checks below.
    text = re.sub(re.escape(customer_name), " ", text, flags=re.I)
    durations = {int(d) for d in DURATION_RE.findall(text)}
    if durations != {rule.duration_days}:
        problems.append(f"duration must be exactly {rule.duration_days}-day, found {sorted(durations)}")
    other = "pooled" if rule.csm_model == "dedicated" else "dedicated"
    if rule.csm_model not in text.lower() or other in text.lower():
        problems.append(f"CSM model must be '{rule.csm_model}' only")
    if PRICE_RE.search(text):
        problems.append("mentions a price")
    if DATE_RE.search(text):
        problems.append("mentions a specific date or day (not in state)")
    return problems


def channel_name(customer_name: str, tier: Tier) -> str:
    ascii_name = unicodedata.normalize("NFKD", customer_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "customer"
    suffix = f"-{tier.value}"
    prefix = "onboarding-"
    slug = slug[: 80 - len(prefix) - len(suffix)].rstrip("-")
    return f"{prefix}{slug}{suffix}"
