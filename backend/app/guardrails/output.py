import hashlib
import json
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


def channel_name(customer_name: str, tier: Tier, run_id: str | None = None) -> str:
    """Slack-safe channel name. With `run_id` the name ends in a short hash of the run id, so it is unique per run:
    two customers whose names truncate or ASCII-fold to the same slug never share a name, and the create-channel
    reconcile (find by name after a timeout) can only ever adopt the channel this run created."""
    ascii_name = unicodedata.normalize("NFKD", customer_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "customer"
    suffix = f"-{tier.value}" + (f"-{run_suffix(run_id)}" if run_id else "")
    prefix = "onboarding-"
    slug = slug[: 80 - len(prefix) - len(suffix)].rstrip("-")
    return f"{prefix}{slug}{suffix}"


def run_suffix(run_id: str) -> str:
    return hashlib.sha256(run_id.encode()).hexdigest()[:8]


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|<@[A-Z0-9]+>")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NAME_PAIR_RE = re.compile(r"\b([A-Z][a-z]+)\s+((?:Ma?c|O'|De|Van)?[A-Z][a-z]+)\b")  # McArthur, O'Neil
# Capitalised words a handoff legitimately uses; a "Firstname Lastname" pair of other words is treated as a person.
HANDOFF_VOCAB = {"novacrm", "the", "this", "all", "data", "migration", "go", "live", "customer", "success", "manager",
                 "support", "team", "enterprise", "growth", "kickoff", "configuration", "phase", "phases",
                 "onboarding", "handoff", "summary", "next", "project", "csm", "rocketlane", "checks", "check",
                 "verified", "verification", "result", "watch", "note", "tasks", "task", "dedicated", "pooled",
                 "plan", "they", "their", "it", "its", "no", "one", "both", "every", "support's", "go-live"}


def check_handoff(text: str, facts: dict) -> list[str]:
    """Deterministic fact checks on the LLM-drafted handoff summary (no invented dates, prices or people)."""
    problems: list[str] = []
    low = text.lower()
    customer = facts["customer_name"]
    if customer.lower() not in low:
        problems.append("missing customer name")
    if facts["plan"].lower() not in low:
        problems.append(f"missing tier '{facts['plan']}'")
    result = facts["migration"]["result"]
    if "migration" not in low or result.lower() not in low:
        problems.append(f"must state the migration result '{result}'")
    if len(text.split()) > 250:
        problems.append("too long")
    # Mask everything that came from the data before looking for invented specifics.
    corpus = [customer, *(t["name"] for p in facts["phases"] for t in p["tasks"]),
              facts["migration"].get("csm_note") or ""]
    masked = text
    for s in sorted(filter(None, corpus), key=len, reverse=True):
        masked = re.sub(re.escape(s), " ", masked, flags=re.I)
    # Any date that appears anywhere in the input data (task due dates, the migration check time) may be cited.
    # (no trailing \b: timestamps like 2026-11-20T10:00 carry their date too)
    allowed_dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", json.dumps(facts, default=str)))
    if bad := sorted(set(ISO_DATE_RE.findall(masked)) - allowed_dates):
        problems.append(f"dates not in the data: {bad}")
    if DATE_RE.search(ISO_DATE_RE.sub(" ", masked)):
        problems.append("mentions a date or day not written as a YYYY-MM-DD date from the data")
    if PRICE_RE.search(masked):
        problems.append("mentions a price")
    if EMAIL_RE.search(masked):
        problems.append("mentions an email address or user")
    all_done = all(t["completed"] for p in facts["phases"] for t in p["tasks"])
    if not all_done and re.search(r"\ball (four |4 )?phases\b", masked, re.I):
        problems.append("claims all phases are complete, but some tasks are open")
    # Contradictions: another migration outcome or the other tier stated outside the quoted data.
    others = {"passed": ["failed", "not run", "accepted by csm"]}.get(result, ["passed"])
    negated = re.sub(r"\b(no|zero|0|none)\s+(\w+\s+)?failed\b|\bnone failed\b", " ", masked, flags=re.I)
    if said := [w for w in others if re.search(rf"\b{w}\b", negated, re.I)]:
        problems.append(f"migration result is '{result}' but the text also says {said}")
    other_tier = {"enterprise": "growth", "growth": "enterprise"}.get(facts["plan"].lower())
    if other_tier and re.search(rf"\b{other_tier}\b", masked, re.I):
        problems.append(f"tier is {facts['plan']}, but the text mentions {other_tier}")
    durations = {int(d) for d in DURATION_RE.findall(masked)}
    if durations - {facts["duration_days"]}:
        problems.append(f"duration must be {facts['duration_days']}-day, found {sorted(durations)}")
    other = "pooled" if facts["csm_model"] == "dedicated" else "dedicated"
    if other in masked.lower():
        problems.append(f"CSM model is '{facts['csm_model']}', not '{other}'")
    people = [f"{a} {b}" for a, b in NAME_PAIR_RE.findall(masked)
              if a.lower() not in HANDOFF_VOCAB and b.lower() not in HANDOFF_VOCAB]
    if people:
        problems.append(f"names people not in the data: {people}")
    return problems
