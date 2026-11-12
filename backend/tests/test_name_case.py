"""Customer and contact names typed all-lowercase in a hurried email are shown title-cased (project name,
Slack, dashboard). Any other casing is the customer's own (eBay, ACME, McDonald's) and is kept as written."""

import pytest
from test_workflow import ENTERPRISE_CALL, GOOD, run

from app.guardrails.input import display_name


@pytest.mark.parametrize("raw,shown", [
    ("nimbus bakery", "Nimbus Bakery"),
    ("waffle hut logistics", "Waffle Hut Logistics"),
    ("o'neil & sons", "O'neil & Sons"),
    ("  acme  corp ", "Acme Corp"),
    ("eBay", "eBay"),
    ("ACME", "ACME"),
    ("McDonald's", "McDonald's"),
    ("Nimbus bakery", "Nimbus bakery"),
    ("3m", "3m"),
    ("", ""),
])
def test_display_name_only_touches_all_lowercase(raw, shown):
    assert display_name(raw) == shown


def test_lowercase_customer_gets_a_title_cased_project_and_channel(settings):
    h, rid = run(settings, calls=[ENTERPRISE_CALL],
                 fields={**GOOD, "customer_name": "nimbus bakery", "customer_contact_name": "priya"})
    assert h.status(rid) == "COMPLETED"
    [p] = h.world.rocketlane.projects
    assert p["projectName"].startswith("Nimbus Bakery Onboarding")
    assert h.store.get_run(rid)["customer_name"] == "Nimbus Bakery"
    [ch] = h.world.slack.channels.values()
    assert ch["name"].startswith("onboarding-nimbus-bakery-")
