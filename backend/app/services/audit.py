from typing import Any

from app.services.store import Store, now

SECRET_KEYS = {"api_key", "token", "authorization", "password", "secret"}
PHONE_KEYS = {"phone", "phone_e164", "to_number", "from_number"}


def mask_phone(phone: str | None) -> str | None:
    if phone is None:
        return None
    return "*" * max(len(phone) - 4, 0) + phone[-4:] if len(phone) > 4 else "*" * len(phone)


def redact(value: Any) -> Any:
    """Recursively mask phone numbers and drop secrets before anything is logged."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k).lower()
            if any(s in key for s in SECRET_KEYS):
                out[k] = "[redacted]"
            elif key in PHONE_KEYS and isinstance(v, str):
                out[k] = mask_phone(v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def audit(store: Store, run_id: str, agent: str, step: str, *, input: Any = None, output: Any = None,
          rationale: str, status: str = "ok") -> None:
    store.add_audit(dict(run_id=run_id, ts=now(), agent=agent, step=step, input=redact(input),
                         output=redact(output), rationale=rationale, status=status))
