"""Normalized identity keys used by every duplicate check (local store and Rocketlane alike)."""

import hashlib
import re
import unicodedata

_SUFFIXES = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|gmbh|pvt)\b\.?")


def customer_key(name: str) -> str:
    """'ACME Corp.' / 'Acme-Corp' / ' acme  corporation ' -> 'acme'. Letters/digits of every script are kept
    ('トヨタ自動車' stays itself), so non-Latin names never collapse to one key; never empty (falls back to a hash)."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    key = "".join(ch for ch in _SUFFIXES.sub(" ", folded) if ch.isalnum())
    return key or "#" + hashlib.sha256(folded.strip().encode()).hexdigest()[:16]


def opportunity_key(opportunity_id: str | None) -> str | None:
    """Salesforce 18-char IDs are the 15-char ID plus a case-checksum suffix."""
    return opportunity_id.strip()[:15] if opportunity_id and opportunity_id.strip() else None
