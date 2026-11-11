"""Print which settings are configured (set/EMPTY). Never prints values."""

from app.config import Settings

s = Settings()
for name in Settings.model_fields:
    value = getattr(s, name)
    default = Settings.model_fields[name].default
    state = "EMPTY" if value in ("", None) else ("default" if value == default else "set")
    print(f"{name.upper():<34} {state}")
