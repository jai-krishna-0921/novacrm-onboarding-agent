"""QA (pre-demo): real-world sloppy deal emails through the full graph with the fake LLM. The LLM output is fixed
(GOOD fields), so these exercise the code paths around it: link discovery, id normalisation, sender check."""

import pytest
from test_workflow import EMAIL, ENTERPRISE_CALL, run

ID18 = "006Hs00001AbCdEIAV"
LIGHTNING = f"https://novacrm.lightning.force.com/lightning/r/Opportunity/{ID18}/view"
SIG = ("\n\n--\nJai Krishna | Account Executive\nNovaCRM | m: +1 555 0100\nhttps://novacrm.example | "
       "https://linkedin.com/in/jai\nCONFIDENTIALITY NOTICE: this email ...")

SLOPPY = {
    "lowercase_typos": ("", f"hey team - closd acme corp today!! contcat dana lee dana@acme.example\nopp {LIGHTNING}"),
    "tracking_params": ("fwd: acme", f"Opp: {LIGHTNING}?utm_source=sfdc&utm_medium=email&ws=%2Flightning"),
    "classic_link_query": ("Closed Won", f"https://novacrm.my.salesforce.com/{ID18[:15]}?srPos=0&srKp=006"),
    "forwarded_thread": ("Fwd: Closed Won: Acme Corp",
                         f"FYI see below{SIG}\n\n---------- Forwarded message ---------\nFrom: Jai\n"
                         f"> Closed Acme Corp. Contact Dana Lee <dana@acme.example>\n> {LIGHTNING}"),
    "link_in_angle_brackets": ("Acme", f"Opportunity <{LIGHTNING}>{SIG}"),
    "lightning_related_tab": ("Acme", LIGHTNING.replace("/view", "/related/OpportunityLineItems/view")),
}


@pytest.mark.parametrize("subject,body", SLOPPY.values(), ids=SLOPPY.keys())
def test_sloppy_but_complete_email_onboards_once(settings, subject, body):
    h, rid = run(settings, calls=[ENTERPRISE_CALL], email=EMAIL.model_copy(update={"subject": subject, "body": body}))
    assert h.status(rid) == "COMPLETED", h.store.list_audit(rid)[-1]
    [p] = h.world.rocketlane.projects
    assert p["externalReferenceId"] == ID18[:15]
    assert h.world.gmail.sent == []                      # no needless clarification email to the AE


# Corporate mail gateways rewrite links. Both still point at one valid Opportunity; today the AE is asked to
# re-send the link ("not a Salesforce Opportunity link"), i.e. a needless clarification round on real mail.
WRAPPED = {
    "outlook_safelinks": "https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fnovacrm.lightning.force"
                         f".com%2Flightning%2Fr%2FOpportunity%2F{ID18}%2Fview&data=05%7C02&reserved=0",
    "google_redirect": f"https://www.google.com/url?q={LIGHTNING}&sa=D&source=hangouts",
}


@pytest.mark.parametrize("url", WRAPPED.values(), ids=WRAPPED.keys())
def test_gateway_wrapped_salesforce_link_is_unwrapped(settings, url):
    h, rid = run(settings, calls=[ENTERPRISE_CALL], email=EMAIL.model_copy(update={"body": f"Closed Acme! {url}"}))
    assert h.world.gmail.sent == [], h.world.gmail.sent[0]["body"][:300]
    assert h.status(rid) == "COMPLETED"


def test_line_wrapped_link_in_quoted_forward_asks_the_ae_never_guesses(settings):
    body = f"> Closed Acme\n> https://novacrm.lightning.force.com/lightning/r/Opportunity/\n> {ID18}/view"
    h, rid = run(settings, calls=[ENTERPRISE_CALL], email=EMAIL.model_copy(update={"body": body}))
    assert h.status(rid) == "AWAITING_CLARIFICATION"
    assert h.world.voice.calls == [] and h.world.rocketlane.projects == []


@pytest.mark.parametrize("sender", ["Jai@NovaCRM.example", " jai@novacrm.example "])
def test_sender_case_and_whitespace_still_match_the_ae_directory(settings, sender):
    h, rid = run(settings, calls=[ENTERPRISE_CALL], email=EMAIL.model_copy(update={"sender": sender}))
    assert h.status(rid) == "COMPLETED", h.store.list_audit(rid)[-1]["rationale"]
