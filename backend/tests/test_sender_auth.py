"""Anti-spoofing: only mail that Gmail itself authenticated for the From domain may start onboarding.

A forged `From: <AE address>` must never reach the LLM, the AE directory, a reply, a call or a project.
"""

import base64

import pytest
from test_workflow import EMAIL, ENTERPRISE_CALL, GOOD

from app.llm.schemas import DealFields
from app.providers.real.gmail import sender_auth, to_inbound
from app.sim import FakeLLM, Harness

# Shape of a real Gmail-stamped header for mail from a gmail.com account.
GMAIL_AR = ("mx.google.com;\r\n       dkim=pass header.i=@gmail.com header.s=20230601 header.b=abc123;\r\n"
            "       spf=pass (google.com: domain of jai@gmail.com designates 209.85.220.41 as permitted sender) "
            "smtp.mailfrom=jai@gmail.com;\r\n"
            "       dmarc=pass (p=NONE sp=QUARANTINE dis=NONE) header.from=gmail.com")


def ar(results: str, authserv: str = "mx.google.com") -> str:
    return f"{authserv}; {results}"


# ---------------- header parsing ----------------

def test_realistic_gmail_header_authenticates_matching_sender():
    ok, detail = sender_auth([GMAIL_AR], "jai@gmail.com")
    assert ok and "gmail.com" in detail


def test_aligned_dkim_pass_is_enough_without_dmarc():
    ok, _ = sender_auth([ar("dkim=pass header.i=@novacrm.example header.s=s1; spf=neutral")], "jai@novacrm.example")
    assert ok


def test_dkim_signing_domain_parent_of_from_domain_is_aligned():
    ok, _ = sender_auth([ar("dkim=pass header.d=novacrm.example header.i=@novacrm.example")],
                        "jai@mail.novacrm.example")
    assert ok


@pytest.mark.parametrize("values,sender", [
    pytest.param([], "jai@gmail.com", id="header_missing"),
    pytest.param([ar("dkim=none; spf=pass smtp.mailfrom=jai@gmail.com; dmarc=fail (p=NONE) header.from=gmail.com")],
                 "jai@gmail.com", id="forged_from_dmarc_fail_spf_only"),
    pytest.param([ar("dkim=pass header.i=@evil.example header.s=s1; spf=pass smtp.mailfrom=x@evil.example; "
                     "dmarc=fail header.from=novacrm.example")], "jai@novacrm.example", id="dkim_unaligned_domain"),
    pytest.param([ar("dkim=pass header.i=@novacrm.example.evil.example")], "jai@novacrm.example",
                 id="dkim_lookalike_suffix_domain"),
    pytest.param([ar("dkim=pass header.i=@sub.novacrm.example")], "jai@novacrm.example",
                 id="dkim_child_domain_of_from_is_not_aligned"),
    pytest.param([ar("dmarc=pass header.from=gmail.com")], "jai@novacrm.example", id="dmarc_pass_for_other_domain"),
    pytest.param([ar("dkim=pass header.i=@gmail.com; dmarc=pass header.from=gmail.com", "evil.example")],
                 "jai@gmail.com", id="untrusted_authserv_id"),
    pytest.param([ar("dkim=pass header.i=@gmail.com; dmarc=pass header.from=gmail.com",
                     "mx.google.com.evil.example")], "jai@gmail.com", id="lookalike_authserv_id"),
    pytest.param([ar("dkim=none; spf=softfail; dmarc=fail header.from=gmail.com"),
                  ar("dkim=pass header.i=@gmail.com; dmarc=pass header.from=gmail.com")],
                 "jai@gmail.com", id="injected_trusted_header_below_gmails_own"),
    pytest.param([ar("spf=pass smtp.mailfrom=jai@gmail.com")], "jai@gmail.com", id="spf_alone"),
])
def test_unauthenticated_sender_is_rejected(values, sender):
    ok, detail = sender_auth(values, sender)
    assert not ok and detail


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def gmail_msg(headers: list[tuple[str, str]]) -> dict:
    return {"id": "m1", "threadId": "t1", "payload": {
        "mimeType": "text/plain", "headers": [{"name": n, "value": v} for n, v in headers],
        "body": {"data": b64("We closed Globex.")}}}


def test_to_inbound_uses_topmost_gmail_header_and_ignores_arc_and_spoofed_ones():
    msg = gmail_msg([
        ("Delivered-To", "cs@novacrm.example"),
        ("ARC-Authentication-Results", "i=1; mx.google.com; dkim=pass header.i=@gmail.com; "
                                       "dmarc=pass header.from=gmail.com"),
        ("Authentication-Results", ar("dkim=none; spf=softfail smtp.mailfrom=x@evil.example; "
                                      "dmarc=fail (p=NONE) header.from=gmail.com")),
        ("From", "Jai <jai@gmail.com>"), ("Subject", "Closed Won: Globex"), ("Message-ID", "<a@b>"),
        ("Authentication-Results", ar("dkim=pass header.i=@gmail.com; dmarc=pass header.from=gmail.com")),
    ])
    e = to_inbound(msg)
    assert e.sender_authenticated is False and "dmarc=fail" in e.auth_detail


def test_to_inbound_marks_genuine_gmail_mail_authenticated():
    e = to_inbound(gmail_msg([("Authentication-Results", GMAIL_AR), ("From", "Jai <Jai@Gmail.com>"),
                              ("Subject", "Closed Won: Globex")]))
    assert e.sender_authenticated is True


def test_to_inbound_without_auth_header_is_not_authenticated():
    e = to_inbound(gmail_msg([("From", "jai@gmail.com"), ("Subject", "Closed Won: Globex")]))
    assert e.sender_authenticated is False


# ---------------- graph: a spoofed email is stopped before anything happens ----------------

def test_unauthenticated_email_from_real_ae_address_escalates_before_any_side_effect(settings):
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL]
    spoofed = EMAIL.model_copy(update={"sender_authenticated": False,
                                       "auth_detail": "dkim=none spf=pass dmarc=fail"})
    rid = h.receive(spoofed)
    assert h.status(rid) == "ESCALATED"
    esc = h.escalation(rid)
    assert esc["kind"] == "input" and "spoof" in esc["reason"].lower()
    assert h.llm.calls == {}
    assert h.world.gmail.sent == [] and h.world.voice.calls == []
    assert h.world.rocketlane.projects == [] and h.world.slack.channels == {}
    assert not any(r["tool"] == "ae_lookup" for r in h.store.tool_calls if r["run_id"] == rid)


def test_retry_on_spoof_escalation_does_not_bypass_the_check(settings):
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    h.world.voice.script = [ENTERPRISE_CALL]
    rid = h.receive(EMAIL.model_copy(update={"sender_authenticated": False}))
    h.resolve(rid, {"action": "retry", "by": "priya@novacrm.example"})
    assert h.status(rid) == "ESCALATED"
    assert h.world.voice.calls == [] and h.world.rocketlane.projects == []


def test_email_dumped_without_auth_fields_is_treated_as_unauthenticated(settings):
    """Fail closed: InboundEmail defaults to not authenticated."""
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**GOOD)))
    raw = EMAIL.model_dump(exclude={"sender_authenticated", "auth_detail"})
    from app.tools.definitions import InboundEmail
    rid = h.receive(InboundEmail(**raw))
    assert h.status(rid) == "ESCALATED" and h.llm.calls == {}


# ---------------- tester: parser edge cases (header is Gmail-written, but parse defensively) ----------------

@pytest.mark.parametrize("value", [
    pytest.param(ar('dkim=fail (x (y) dmarc=pass header.from=novacrm.example) header.i=@novacrm.example'),
                 id="nested_comment_hiding_dmarc_pass"),
    pytest.param(ar('dkim=pass header.i="@evil.example; dmarc=pass header.from=novacrm.example"'),
                 id="quoted_string_with_semicolon"),
    pytest.param(ar("dkim=pass header.i=@"), id="dkim_pass_empty_signer"),
    pytest.param(ar("dkim=pass header.i=@novacrm.example", "i=1; mx.google.com"), id="arc_style_instance_prefix"),
])
def test_malformed_or_smuggled_verdicts_do_not_authenticate(value):
    ok, _ = sender_auth([value], "jai@novacrm.example")
    assert not ok


@pytest.mark.parametrize("value", [
    ar("DKIM=PASS header.i=@NovaCRM.example"),
    ar("dmarc=pass header.from=novacrm.example", "MX.Google.COM 1"),
    ar("dmarc=pass header.from=novacrm.example", "mx.google.com(gmail)"),
])
def test_case_version_and_comment_on_authserv_are_tolerated(value):
    ok, _ = sender_auth([value], "jai@novacrm.example")
    assert ok


@pytest.mark.parametrize("from_value", [
    "jai@novacrm.example, x@evil.example",            # two mailboxes in one From
    "x@evil.example)<jai@novacrm.example>",          # CVE-2023-27043-style parser confusion
])
def test_ambiguous_from_header_is_not_authenticated(from_value):
    e = to_inbound(gmail_msg([("Authentication-Results", ar("dmarc=pass header.from=novacrm.example")),
                              ("From", from_value), ("Subject", "Closed Won: Globex")]))
    assert e.sender_authenticated is False


def test_duplicate_from_headers_cannot_pair_one_domains_pass_with_the_others_address():
    # Attacker passes DMARC for evil.example, then appends a second From with the AE address.
    e = to_inbound(gmail_msg([("Authentication-Results", ar("dmarc=pass header.from=evil.example")),
                              ("From", "x@evil.example"), ("From", "jai@novacrm.example"),
                              ("Subject", "Closed Won: Globex")]))
    assert not (e.sender_authenticated and e.sender == "jai@novacrm.example")


def test_html_only_email_keeps_the_opportunity_href():
    """Gmail/Outlook rich text often links the word 'Opportunity'; stripping tags must not drop the URL,
    or a correct deal email bounces back to the AE as 'opportunity_url: missing'."""
    from app.guardrails.input import find_opportunity_url
    url = "https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view"
    msg = {"id": "m1", "threadId": "t1", "payload": {
        "mimeType": "text/html", "headers": [{"name": "From", "value": "jai@gmail.com"}],
        "body": {"data": b64(f'<p>Closed Globex. See the <a href="{url}">Opportunity</a>.</p>')}}}
    assert find_opportunity_url(to_inbound(msg).body) == url
