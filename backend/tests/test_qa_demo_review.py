"""QA review of af6ddba..b4cdafe: display_name title-casing vs duplicate detection, welcome fact-check and Slack
name; Retell call_started/call_ended progress events (display only, never change run state)."""

import pytest
from test_api import env, only_run, progress, push, retell_post  # noqa: F401  (env is a fixture)
from test_workflow import EMAIL, ENTERPRISE_CALL, GOOD, run

from app.config import Tier, tier_rules
from app.guardrails.input import display_name
from app.guardrails.output import channel_name, check_welcome
from app.llm.schemas import DealFields
from app.services.keys import customer_key
from app.sim import FakeLLM, Harness

# ---------------- display_name x customer_key / welcome / channel ----------------

@pytest.mark.parametrize("typed,canonical", [
    ("nimbus bakery", "Nimbus Bakery"),
    ("  nimbus   bakery ", "Nimbus Bakery"),
    ("acme gmbh", "Acme GmbH"),
    ("straße werke", "Straße Werke"),
    ("ßeta labs", "ßeta Labs"),
    ("élan vital", "Élan Vital"),
])
def test_title_casing_never_changes_the_duplicate_key(typed, canonical):
    assert customer_key(display_name(typed)) == customer_key(canonical) == customer_key(typed)


@pytest.mark.parametrize("raw", ["123", "!!!", "--", "4 2", "   "])
def test_names_without_letters_are_left_alone(raw):
    assert display_name(raw) == " ".join(raw.split())


@pytest.mark.parametrize("typed,llm_writes", [
    ("nimbus bakery", "nimbus bakery"),       # LLM copies the email's casing
    ("acme gmbh", "Acme GmbH"),               # LLM uses the real legal-form casing
    ("ßeta labs", "ßeta Labs"),               # LLM keeps the sharp s; display_name upper()s it to 'SS'
])
def test_welcome_fact_check_accepts_the_customer_name_in_any_casing(settings, typed, llm_writes):
    rule = tier_rules(settings)[Tier.ENTERPRISE]
    text = (f"Welcome to NovaCRM, {llm_writes}! Your {rule.duration_days}-day onboarding starts now with a "
            f"{rule.csm_model} CSM.")
    assert check_welcome(text, display_name(typed), rule) == []


def test_channel_name_is_identical_for_lowercase_and_title_case():
    assert channel_name(display_name("nimbus bakery"), Tier.GROWTH, "r1") == channel_name("Nimbus Bakery",
                                                                                         Tier.GROWTH, "r1")


def test_lowercase_email_is_a_duplicate_of_an_existing_title_cased_project(settings):
    """Same customer, different opportunity: only the customer-name check can catch it."""
    h, rid = run(settings, calls=[ENTERPRISE_CALL],
                 fields={**GOOD, "customer_name": "acme corp"},
                 existing_projects=[{"projectId": 77, "customer": {"companyName": "Acme Corp"},
                                     "externalReferenceId": "006Hs00009ZzZzZIAV"}])
    assert h.status(rid) == "ESCALATED"
    assert len(h.world.rocketlane.projects) == 1
    assert "existing" in h.escalation(rid)["reason"].lower()


def test_lowercase_welcome_from_llm_is_not_regenerated(settings):
    rule = tier_rules(settings)[Tier.ENTERPRISE]
    llm = FakeLLM(fields=DealFields(**{**GOOD, "customer_name": "nimbus bakery"}),
                  welcome=f"Welcome to NovaCRM, nimbus bakery! Your {rule.duration_days}-day onboarding starts "
                          f"now with a {rule.csm_model} CSM.")
    h = Harness(settings, llm=llm)
    h.world.voice.script = [ENTERPRISE_CALL]
    rid = h.receive(EMAIL)
    assert h.status(rid) == "COMPLETED"
    assert llm.calls["draft_welcome"] == 1


# ---------------- call_started / call_ended ----------------

def test_call_progress_never_changes_run_state_or_places_calls(env):  # noqa: F811
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    before = (h.status(rid), len(h.world.voice.calls), len(h.world.rocketlane.projects))
    for ev in ("call_started", "call_ended", "call_ended", "call_started"):
        assert retell_post(client, progress(rid, ev)).status_code == 204
    assert (h.status(rid), len(h.world.voice.calls), len(h.world.rocketlane.projects)) == before


@pytest.mark.parametrize("call", [None, "x", [], {"call_id": "call-1", "metadata": "x"},
                                  {"metadata": {"run_id": "r"}}])
def test_malformed_progress_payloads_are_400_not_500(env, call):  # noqa: F811
    client, h, _ = env
    assert retell_post(client, {"event": "call_started", "call": call}).status_code == 400


def test_progress_with_non_string_ids_is_ignored(env):  # noqa: F811
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    body = {"event": "call_ended", "call": {"call_id": 123, "metadata": {"run_id": rid}}}
    assert retell_post(client, body).status_code == 204
    assert not [a for a in h.store.list_audit(rid) if a["step"] == "call_progress"]


def test_progress_after_call_analyzed_is_not_recorded(env):  # noqa: F811
    """Retell can deliver call_ended after call_analyzed. Once the run moved on, nothing is shown."""
    from test_api import analyzed
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    retell_post(client, analyzed(rid))
    assert h.status(rid) == "COMPLETED"
    retell_post(client, progress(rid, "call_ended"))
    assert not [a for a in h.store.list_audit(rid) if a["step"] == "call_progress"]


def test_progress_naming_another_runs_call_is_ignored(env):  # noqa: F811
    client, h, _ = env
    push(client), push(client)
    rid = only_run(h)
    retell_post(client, progress("some-other-run", "call_started"))
    retell_post(client, progress(rid, "call_started", call_id="call-of-another-run"))
    assert not [a for a in h.store.list_audit(rid) if a["step"] == "call_progress"]
    assert not h.store.list_audit("some-other-run")
