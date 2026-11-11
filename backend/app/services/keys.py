"""Normalized identity keys used by every duplicate check (local store and Rocketlane alike)."""

import re

_SUFFIXES = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|gmbh|pvt)\b\.?")


def customer_key(name: str) -> str:
    """'ACME Corp.' / 'Acme-Corp' / ' acme  corporation ' -> 'acme'."""
    n = _SUFFIXES.sub(" ", name.casefold())
    return re.sub(r"[^a-z0-9]", "", n)


def opportunity_key(opportunity_id: str | None) -> str | None:
    """Salesforce 18-char IDs are the 15-char ID plus a case-checksum suffix."""
    return opportunity_id.strip()[:15] if opportunity_id and opportunity_id.strip() else None
