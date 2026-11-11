"""Server logs are one JSON object per line (machine-parseable on the EC2 host / any log shipper)."""

import json
import logging

from app.jsonlog import JsonFormatter


def _record(msg, *args, exc=None, **extra):
    r = logging.LogRecord("onboarding", logging.INFO, __file__, 1, msg, args, exc)
    r.__dict__.update(extra)
    return r


def test_formats_one_json_object_with_core_fields():
    line = JsonFormatter().format(_record("deal email %s -> run %s", "m1", "r1"))
    d = json.loads(line)
    assert "\n" not in line
    assert d["level"] == "INFO" and d["logger"] == "onboarding" and d["msg"] == "deal email m1 -> run r1"
    assert d["ts"].endswith("+00:00")


def test_includes_extra_context_and_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        d = json.loads(JsonFormatter().format(_record("failed", exc=sys.exc_info(), run_id="r9")))
    assert d["run_id"] == "r9" and "ValueError: boom" in d["exc"]


def test_masks_phone_numbers_in_messages():
    d = json.loads(JsonFormatter().format(_record("calling %s", "+919789998350")))
    assert "9789998350" not in d["msg"] and d["msg"].endswith("8350")


def test_logging_config_file_is_valid():
    import logging.config
    from pathlib import Path
    cfg = json.loads((Path(__file__).parents[1] / "logging.json").read_text())
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        logging.config.dictConfig(cfg)
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers[:], _ = saved[0], root.setLevel(saved[1])
