import re
from urllib.parse import parse_qs, urlsplit

from app.llm.schemas import ExtractedDeal
from app.services.keys import opportunity_key

REQUIRED = ("customer_name", "customer_email", "ae_name", "ae_email")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")

SF_HOST_SUFFIXES = (".salesforce.com", ".force.com")
# Lightning `/lightning/r/[Opportunity/]<id>/...` or classic `/<id>`; ids are 15 or 18 chars, key prefix 006.
OPP_PATH_RE = re.compile(r"^/(?:(?i:lightning/r/(?:opportunity/)?))?(006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?)(?:/|$)")
# Mail gateways that rewrite links: host suffix -> query parameter holding the original URL.
REDIRECTORS = {"safelinks.protection.outlook.com": "url", "google.com": "q"}


def unwrap(url: str) -> str:
    """The original link inside an Outlook SafeLinks / Google redirect wrapper (one level); else unchanged."""
    try:
        u = urlsplit(url)
        host = (u.hostname or "").lower()
    except ValueError:
        return url
    for suffix, param in REDIRECTORS.items():
        if host == suffix or host.endswith("." + suffix):
            if suffix == "google.com" and u.path != "/url":
                return url
            inner = parse_qs(u.query).get(param, [""])[0]
            return inner if inner.lower().startswith(("http://", "https://")) else url
    return url
URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.I)


def opportunity_id_from_url(url: str | None) -> str | None:
    """The Opportunity record id if `url` is a Salesforce Opportunity link, else None."""
    url = unwrap((url or "").strip())
    if not url or any(c.isspace() for c in url):
        return None
    try:
        u = urlsplit(url)
        host = (u.hostname or "").lower()
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not host.endswith(SF_HOST_SUFFIXES):
        return None
    m = OPP_PATH_RE.match(u.path)
    return m.group(1) if m else None


def display_name(name: str | None) -> str:
    """'nimbus bakery' -> 'Nimbus Bakery'. Only an all-lowercase name is changed (a hurried email); any other
    casing is the customer's own spelling (eBay, ACME, McDonald's) and is kept. Whitespace is collapsed."""
    name = " ".join((name or "").split())
    if not name or name != name.lower() or not any(c.isalpha() for c in name):
        return name
    return " ".join(w[:1].upper() + w[1:] for w in name.split(" "))


def find_opportunity_url(text: str) -> str | None:
    """The valid Salesforce Opportunity link in the email. Links to two different opportunities (e.g. an
    older deal quoted in a forward) are returned space-joined so validation rejects them rather than
    guessing; with no valid link, the first link that looks like one (so validation can say what is
    wrong); None if there is no such link."""
    urls = [unwrap(u.rstrip(".,;:!?")) for u in URL_RE.findall(text)]
    valid: dict[str | None, str] = {}
    for u in urls:
        if opp := opportunity_id_from_url(u):
            valid.setdefault(opportunity_key(opp), u)
    if valid:
        return " ".join(valid.values())
    return next((u for u in urls if re.search(r"salesforce|force\.com|/opportunity/", u, re.I)), None)


def validate_deal(fields: ExtractedDeal) -> list[str]:
    """Return human-readable issues; empty list means the deal can proceed."""
    issues: list[str] = []
    for name in REQUIRED:
        value = (getattr(fields, name) or "").strip()
        if not value:
            issues.append(f"{name}: missing")
        elif name.endswith("_email") and not EMAIL_RE.match(value):
            issues.append(f"{name}: invalid format ({value})")
    url = (fields.opportunity_url or "").strip()
    if not url:
        issues.append("opportunity_url: missing")
    elif len(url.split()) > 1:
        issues.append(f"opportunity_url: links to several different opportunities, send exactly one ({url[:300]})")
    elif not opportunity_id_from_url(url):
        issues.append(f"opportunity_url: not a Salesforce Opportunity link ({url[:200]})")
    return issues
