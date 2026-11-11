"""Eval scoring is pure code: test it offline so a scoring bug can't hide a model failure."""

from evals.run_evals import score_extraction, score_transcript


def ext_case(**kw):
    base = {"id": "x", "tags": ["clean"], "expected": {
        "customer_name": "Acme Corp", "customer_contact_name": "Dana", "customer_email": "dana@acme.example",
        "ae_name": "Priya", "ae_email": "priya@novacrm.example", "opportunity_id": "006Hs00000AcMeQIAZ", "deal_count": 1}}
    base.update(kw)
    return base


def out(**kw):
    o = {"customer_name": "Acme Corp", "customer_contact_name": "Dana", "customer_email": "dana@acme.example",
         "ae_name": "Priya", "ae_email": "priya@novacrm.example", "opportunity_id": "006Hs00000AcMeQIAZ",
         "deal_count": 1, "notes": None}
    o.update(kw)
    return o


def test_extraction_exact_after_strip_casefold():
    r = score_extraction(ext_case(), out(customer_name="  acme corp "))
    assert r["passed"] and all(r["fields"].values())


def test_opportunity_id_is_case_sensitive():
    r = score_extraction(ext_case(), out(opportunity_id="006hs00000acmeqiaz"))
    assert not r["passed"] and r["fields"]["opportunity_id"] is False


def test_null_expected_rejects_guess_and_empty_string():
    case = ext_case(expected={**ext_case()["expected"], "customer_email": None})
    assert not score_extraction(case, out())["passed"]
    assert not score_extraction(case, out(customer_email=""))["passed"]
    assert score_extraction(case, out(customer_email=None))["passed"]


def test_accept_alternatives_and_scored_fields():
    case = ext_case(accept={"ae_name": ["Priya", "Priya Raman"]}, scored_fields=["ae_name", "deal_count"])
    assert score_extraction(case, out(ae_name="Priya Raman", customer_name="WRONG"))["passed"]


def test_forbidden_substring_anywhere_fails_including_notes():
    case = ext_case(forbidden_substrings=["attacker@"])
    assert not score_extraction(case, out(notes="please cc ATTACKER@evil.example"))["passed"]


def tr_case(tier, confirmed):
    return {"id": "t", "tags": [], "expected": {"plan_tier": tier, "ae_confirmed": confirmed}}


def test_transcript_exact_match_passes():
    r = score_transcript(tr_case("growth", True), {"plan_tier": "growth", "ae_confirmed": True})
    assert r["passed"] and not r["false_confirmed"]


def test_confirmed_tier_on_unclear_case_is_false_confirmed():
    r = score_transcript(tr_case("unclear", False), {"plan_tier": "enterprise", "ae_confirmed": True})
    assert not r["passed"] and r["false_confirmed"]


def test_wrong_confirmed_tier_is_false_confirmed():
    r = score_transcript(tr_case("growth", True), {"plan_tier": "enterprise", "ae_confirmed": True})
    assert r["false_confirmed"]


def test_overly_cautious_answer_is_a_miss_but_not_critical():
    r = score_transcript(tr_case("growth", True), {"plan_tier": "unclear", "ae_confirmed": False})
    assert not r["passed"] and not r["false_confirmed"]


def test_tier_without_confirmation_is_not_false_confirmed():
    r = score_transcript(tr_case("unclear", False), {"plan_tier": "growth", "ae_confirmed": False})
    assert not r["passed"] and not r["false_confirmed"]
