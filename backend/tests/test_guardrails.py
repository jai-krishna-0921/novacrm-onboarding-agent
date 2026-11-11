"""Guardrails are pure code: these tests pin every decision the LLM is NOT allowed to make."""

import pytest

from app.config import Tier, tier_rules
from app.guardrails.decision import CallOutcome, CallResult, decide_tier
from app.guardrails.input import find_opportunity_url, opportunity_id_from_url, validate_deal
from app.guardrails.output import channel_name, check_welcome
from app.llm.schemas import ExtractedDeal
from app.services.audit import mask_phone

GOOD = dict(
    customer_name="Acme Corp",
    customer_contact_name="Dana Lee",
    customer_email="dana@acme.example",
    ae_name="Jai Krishna",
    ae_email="jai@novacrm.example",
    opportunity_id="006Hs00001AbCdEIAV",
    opportunity_url="https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view",
    deal_count=1,
)


# ---------- input validation ----------

def test_complete_deal_has_no_issues():
    assert validate_deal(ExtractedDeal(**GOOD)) == []


@pytest.mark.parametrize("field", ["customer_name", "customer_email", "ae_name", "ae_email"])
def test_each_missing_required_field_is_reported(field):
    issues = validate_deal(ExtractedDeal(**{**GOOD, field: None}))
    assert issues == [f"{field}: missing"]


@pytest.mark.parametrize("bad", ["dana@", "dana at acme dot com", "dana@acme", "  ", "@acme.com"])
def test_malformed_contact_email_is_reported(bad):
    issues = validate_deal(ExtractedDeal(**{**GOOD, "customer_email": bad}))
    assert issues and issues[0].startswith("customer_email:")


def test_whitespace_only_name_counts_as_missing():
    assert validate_deal(ExtractedDeal(**{**GOOD, "customer_name": "   "})) == ["customer_name: missing"]


@pytest.mark.parametrize("url", [
    "https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view",
    "https://novacrm.my.salesforce.com/006Hs00001AbCdE",
    "https://novacrm.my.salesforce.com/006Hs00001AbCdEIAV?srPos=0",
    "https://novacrm.lightning.force.com/lightning/r/opportunity/006Hs00001AbCdEIAV/view",
])
def test_valid_salesforce_opportunity_links(url):
    assert opportunity_id_from_url(url) in ("006Hs00001AbCdEIAV", "006Hs00001AbCdE")
    assert validate_deal(ExtractedDeal(**{**GOOD, "opportunity_url": url})) == []


@pytest.mark.parametrize("url", [None, "", "   "])
def test_missing_opportunity_link_is_reported(url):
    assert validate_deal(ExtractedDeal(**{**GOOD, "opportunity_url": url})) == ["opportunity_url: missing"]


@pytest.mark.parametrize("url", [
    "https://novacrm.lightning.force.example/lightning/r/Opportunity/006Hs00001AbCdEIAV/view",  # wrong host
    "https://force.com.evil.example/lightning/r/Opportunity/006Hs00001AbCdEIAV/view",          # lookalike host
    "https://evilforce.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view",                    # no dot boundary
    "https://novacrm.lightning.force.com@evil.example/lightning/r/Opportunity/006Hs00001AbCdEIAV/view",
    "https://novacrm.lightning.force.com/lightning/r/Account/001Hs00001AbCdEIAV/view",          # not an opportunity
    "https://novacrm.lightning.force.com/lightning/r/Opportunity/001Hs00001AbCdEIAV/view",      # wrong key prefix
    "https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCd/view",          # 14 chars
    "https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIA/view",       # 17 chars
    "https://novacrm.lightning.force.com/lightning/o/Opportunity/list",                         # list view
    "ftp://novacrm.my.salesforce.com/006Hs00001AbCdE",
    "006Hs00001AbCdEIAV",                                                                       # bare id
])
def test_invalid_opportunity_link_is_reported(url):
    assert opportunity_id_from_url(url) is None
    issues = validate_deal(ExtractedDeal(**{**GOOD, "opportunity_url": url}))
    assert len(issues) == 1 and issues[0].startswith("opportunity_url: not a Salesforce Opportunity link")


def test_opportunity_link_is_found_in_the_email_body_by_code():
    body = ("Closed! Opp: https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view.\n"
            "Thanks")
    assert find_opportunity_url(body) == \
        "https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view"


def test_a_valid_link_wins_over_an_earlier_bad_one_and_bad_ones_are_still_surfaced():
    bad = "https://novacrm.lightning.force.example/lightning/r/Opportunity/006Hs00001AbCdEIAV/view"
    good = "https://novacrm.my.salesforce.com/006Hs00001AbCdE"
    assert find_opportunity_url(f"see {bad} or {good}") == good
    assert find_opportunity_url(f"see {bad}") == bad  # kept so validation can say what is wrong
    assert find_opportunity_url("Opp ID 006Hs00001AbCdEIAV, docs at https://docs.example/x") is None


# ---------- tier decision ----------

def _call(**kw):
    base = dict(provider_call_id="c1", status="connected", plan_tier="enterprise",
                confirmed=True, transcript="... Enterprise. Yes, confirmed.")
    return CallResult(**{**base, **kw})


def test_clear_confirmed_answer_with_agreeing_cross_check_confirms():
    d = decide_tier(_call(), cross_check="enterprise")
    assert (d.outcome, d.tier) == (CallOutcome.CONFIRMED, Tier.ENTERPRISE)


@pytest.mark.parametrize("status", ["no_answer", "voicemail", "busy", "failed"])
def test_not_connected_is_no_answer_and_never_a_tier(status):
    d = decide_tier(_call(status=status), cross_check="enterprise")
    assert d.outcome == CallOutcome.NO_ANSWER and d.tier is None


@pytest.mark.parametrize("tier", ["unclear", None, "premium", ""])
def test_non_tier_answer_is_unclear(tier):
    d = decide_tier(_call(plan_tier=tier), cross_check="enterprise")
    assert d.outcome == CallOutcome.UNCLEAR and d.tier is None


@pytest.mark.parametrize("confirmed", [False, None])
def test_tier_without_explicit_confirmation_is_unclear(confirmed):
    d = decide_tier(_call(confirmed=confirmed), cross_check="enterprise")
    assert d.outcome == CallOutcome.UNCLEAR and d.tier is None


@pytest.mark.parametrize("cross", ["growth", "unclear", None])
def test_cross_check_disagreement_is_unclear(cross):
    d = decide_tier(_call(), cross_check=cross)
    assert d.outcome == CallOutcome.UNCLEAR and d.tier is None
    assert "cross-check" in d.rationale


def test_tier_comparison_is_case_insensitive():
    d = decide_tier(_call(plan_tier="Growth"), cross_check="GROWTH")
    assert (d.outcome, d.tier) == (CallOutcome.CONFIRMED, Tier.GROWTH)


# ---------- tier rules (template accuracy) ----------

def test_enterprise_rule_is_30_days_dedicated(settings):
    r = tier_rules(settings)[Tier.ENTERPRISE]
    assert (r.duration_days, r.csm_model, r.template_id) == (30, "dedicated", "tmpl-ent")
    assert r.csm_email == "csm-dedicated@novacrm.example"


def test_growth_rule_is_14_days_pooled(settings):
    r = tier_rules(settings)[Tier.GROWTH]
    assert (r.duration_days, r.csm_model, r.template_id) == (14, "pooled", "tmpl-growth")
    assert r.csm_email == "csm-pool@novacrm.example"


# ---------- output guardrails ----------

def _welcome(tier=Tier.ENTERPRISE):
    days = "30-day" if tier == Tier.ENTERPRISE else "14-day"
    csm = "dedicated" if tier == Tier.ENTERPRISE else "pooled"
    return (f"Welcome Acme Corp! Your {days} onboarding starts today with your {csm} "
            f"Customer Success Manager. We'll begin with the kickoff call.")


def test_good_enterprise_welcome_passes(settings):
    assert check_welcome(_welcome(), "Acme Corp", tier_rules(settings)[Tier.ENTERPRISE]) == []


def test_good_growth_welcome_passes(settings):
    rule = tier_rules(settings)[Tier.GROWTH]
    assert check_welcome(_welcome(Tier.GROWTH), "Acme Corp", rule) == []


def test_welcome_missing_customer_name_fails(settings):
    text = _welcome().replace("Acme Corp", "there")
    assert "missing customer name" in check_welcome(text, "Acme Corp", tier_rules(settings)[Tier.ENTERPRISE])


def test_welcome_with_wrong_duration_fails(settings):
    rule = tier_rules(settings)[Tier.GROWTH]
    problems = check_welcome(_welcome(Tier.ENTERPRISE), "Acme Corp", rule)
    assert any("duration" in p for p in problems)
    assert any("csm" in p.lower() for p in problems)


@pytest.mark.parametrize("extra", ["Your plan costs $499/month.", "Pricing: USD 1,200", "₹40,000 per year"])
def test_welcome_mentioning_prices_fails(settings, extra):
    problems = check_welcome(_welcome() + " " + extra, "Acme Corp", tier_rules(settings)[Tier.ENTERPRISE])
    assert any("price" in p for p in problems)


@pytest.mark.parametrize("ok", ["We'll make 2 decisions together in Kickoff.",
                                "Expect 3 marches through the checklist.", "Ping us 24/7 here."])
def test_welcome_date_check_has_no_false_positives_on_ordinary_words(settings, ok):
    assert check_welcome(_welcome() + " " + ok, "Acme Corp", tier_rules(settings)[Tier.ENTERPRISE]) == []


@pytest.mark.parametrize(
    "customer,tier,expected",
    [
        ("Acme Corp", Tier.ENTERPRISE, "onboarding-acme-corp-enterprise"),
        ("Zoë & Partners, Inc.", Tier.GROWTH, "onboarding-zoe-partners-inc-growth"),
        ("!!!", Tier.GROWTH, "onboarding-customer-growth"),
    ],
)
def test_channel_name_is_slack_safe(customer, tier, expected):
    assert channel_name(customer, tier) == expected


def test_channel_name_never_exceeds_80_chars():
    name = channel_name("A" * 200, Tier.ENTERPRISE)
    assert len(name) <= 80 and name.endswith("-enterprise")


# ---------- audit masking ----------

@pytest.mark.parametrize("raw,masked", [("+919876543210", "*********3210"), ("12", "**"), (None, None)])
def test_phone_masking_keeps_last_four(raw, masked):
    assert mask_phone(raw) == masked


@pytest.mark.parametrize("customer,tier", [("Monday Labs", Tier.GROWTH), ("14-Day Fitness Studios", Tier.ENTERPRISE),
                                           ("March Analytics", Tier.ENTERPRISE), ("Pricing Partners LLC", Tier.GROWTH)])
def test_customer_name_itself_never_trips_the_fact_checks(settings, customer, tier):
    rule = tier_rules(settings)[tier]
    text = (f"Welcome {customer}! Your {rule.duration_days}-day onboarding starts now with your {rule.csm_model} "
            f"Customer Success Manager.")
    assert check_welcome(text, customer, rule) == []


def test_name_masking_does_not_hide_a_real_violation_elsewhere(settings):
    rule = tier_rules(settings)[Tier.GROWTH]
    text = "Welcome Monday Labs! Your 14-day onboarding with your pooled CSM starts Monday."
    assert any("date" in p for p in check_welcome(text, "Monday Labs", rule))


# ---------------- tester: Opportunity-link edge cases ----------------

def test_two_different_opportunity_links_are_not_silently_resolved_to_the_first():
    """A forwarded/quoted older deal must not donate its Opportunity id to the new one (wrong external ref,
    or a false 'duplicate'). Expect the ambiguity to surface (no url / clarification), not a silent pick."""
    old = "https://novacrm.my.salesforce.com/006Hs00001OLDOLAAA"
    new = "https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view"
    body = f"> ---------- Forwarded message ----------\n> Opp: {old}\n\nNew deal closed! Opp: {new}"
    picked = find_opportunity_url(body)
    assert opportunity_id_from_url(picked) is None
    [issue] = validate_deal(ExtractedDeal(**{**GOOD, "opportunity_url": picked}))
    assert "several different opportunities" in issue


def test_same_opportunity_linked_twice_is_not_ambiguous():
    body = ("https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view and "
            "https://novacrm.my.salesforce.com/006Hs00001AbCdE")
    assert opportunity_id_from_url(find_opportunity_url(body)) == "006Hs00001AbCdEIAV"
