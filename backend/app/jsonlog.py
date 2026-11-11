"""One-JSON-object-per-line log formatter (wired in via uvicorn --log-config logging.json)."""

import json
import logging
import re
from datetime import UTC, datetime

from app.services.audit import mask_phone

_PHONE = re.compile(r"\+\d{8,15}")
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = _PHONE.sub(lambda m: mask_phone(m.group()) or "", record.getMessage())
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(), "level": record.levelname,
               "logger": record.name, "msg": msg}
        out.update({k: v for k, v in vars(record).items() if k not in _STD})  # `extra=` context, e.g. run_id
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)
