"""AE directory edits from the dashboard: input cleaning, the public row shape (never the full phone) and the audit
trail (who, what changed with phones masked, when). Every row is written under the synthetic run id `directory`."""

import re

from app.config import normalize_e164
from app.services.audit import audit, mask_phone
from app.services.store import Store

AUDIT_RUN = "directory"
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")


class Invalid(ValueError):
    """Bad input; the message is safe to show (422)."""


def clean_email(v: str) -> str:
    e = v.strip().lower()
    if len(e) > 254 or not _EMAIL.fullmatch(e):
        raise Invalid("email must look like name@company.com")
    return e


def clean_name(v: str) -> str:
    n = v.strip()
    if not n or len(n) > 200:
        raise Invalid("name is required (at most 200 characters)")
    return n


def clean_phone(v: str) -> str:
    try:
        p = normalize_e164(v)
    except ValueError as e:
        raise Invalid(f"{e} (include the country code with a leading +)") from e
    if not p:
        raise Invalid("phone is required, E.164 with a leading +, e.g. +15108228650")
    return p


def public(ae: dict) -> dict:
    return {"email": ae["email"], "name": ae["name"], "phone_masked": mask_phone(ae.get("phone")),
            "active": bool(ae["active"]), "updated_at": ae.get("updated_at")}


def audit_change(store: Store, by: str, step: str, email: str, before: dict, after: dict) -> None:
    """before/after hold only the changed fields; the audit helper masks `phone` values."""
    audit(store, AUDIT_RUN, "directory", step, input={"by": by},
          output={"email": email, "before": before, "after": after},
          rationale=f"{by} {'added' if step == 'ae_create' else 'changed'} AE {email}: "
                    f"{', '.join(sorted(after)) or 'no fields'}")
