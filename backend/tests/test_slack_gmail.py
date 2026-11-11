import base64
import email

import pytest
from slack_sdk.errors import SlackApiError

from app.providers.real.gmail import GmailClient, build_reply, extract_text, to_inbound
from app.providers.real.slack import SlackClient
from app.tools.definitions import GmailReplyIn
from app.tools.registry import PermanentToolError, TransientToolError

# ---------------- Slack ----------------

def slack_error(code: str, status: int = 200):
    class Resp(dict):
        status_code = status
    return SlackApiError(code, Resp(ok=False, error=code))


class FakeWeb:
    def __init__(self, fail: dict[str, str] | None = None):
        self.fail, self.calls = fail or {}, []

    def _do(self, op, **kw):
        self.calls.append((op, kw))
        if op in self.fail:
            raise slack_error(self.fail[op], 429 if self.fail[op] == "ratelimited" else 200)
        return {"ok": True, "channel": {"id": "C1"}}

    def conversations_create(self, **kw): return self._do("create", **kw)
    def conversations_setTopic(self, **kw): return self._do("topic", **kw)
    def conversations_invite(self, **kw): return self._do("invite", **kw)
    def conversations_inviteShared(self, **kw): return self._do("shared", **kw)
    def chat_postMessage(self, **kw): return self._do("post", **kw)


def test_slack_happy_calls():
    web = FakeWeb()
    s = SlackClient(client=web)
    assert s.create_channel("onboarding-globex-growth") == "C1"
    s.set_topic("C1", "t")
    s.invite("C1", ["U1", "U2"])
    assert s.invite_shared("C1", "dana@globex.example").status == "sent"
    s.post("C1", "hi")
    assert web.calls[0] == ("create", {"name": "onboarding-globex-growth", "is_private": False})
    assert web.calls[2] == ("invite", {"channel": "C1", "users": "U1,U2"})
    assert web.calls[3] == ("shared", {"channel": "C1", "emails": ["dana@globex.example"]})


@pytest.mark.parametrize("code", ["not_paid", "restricted_action", "not_allowed_token_type",
                                  "team_not_found", "invite_not_allowed"])
def test_slack_connect_unavailable_degrades_instead_of_failing(code):
    out = SlackClient(client=FakeWeb({"shared": code})).invite_shared("C1", "x@y.example")
    assert out.status == "unsupported" and code in out.detail


def test_slack_name_taken_is_permanent():
    with pytest.raises(PermanentToolError, match="name_taken"):
        SlackClient(client=FakeWeb({"create": "name_taken"})).create_channel("x")


def test_slack_rate_limit_is_transient():
    with pytest.raises(TransientToolError):
        SlackClient(client=FakeWeb({"post": "ratelimited"})).post("C1", "x")


def test_slack_already_in_channel_is_success():
    SlackClient(client=FakeWeb({"invite": "already_in_channel"})).invite("C1", ["U1"])


# ---------------- Gmail ----------------

def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


MSG = {
    "id": "18c1", "threadId": "18c0",
    "payload": {
        "mimeType": "multipart/alternative",
        "headers": [{"name": "From", "value": "Jai Krishna <Jai@NovaCRM.example>"},
                    {"name": "Subject", "value": "Closed Won: Globex"},
                    {"name": "Message-ID", "value": "<abc@mail.gmail.com>"}],
        "parts": [{"mimeType": "text/plain", "body": {"data": b64("Hi team,\nWe closed Globex.")}},
                  {"mimeType": "text/html", "body": {"data": b64("<p>Hi team</p>")}}],
    },
}


def test_extract_prefers_plain_text():
    assert extract_text(MSG["payload"]) == "Hi team,\nWe closed Globex."


def test_extract_falls_back_to_stripped_html():
    payload = {"mimeType": "text/html", "body": {"data": b64("<div>Closed <b>Globex</b><br>Contact: d@g.example</div>")}}
    text = extract_text(payload)
    assert "Closed Globex" in text and "d@g.example" in text and "<" not in text


def test_to_inbound_normalizes_sender_and_keeps_rfc_message_id():
    e = to_inbound(MSG)
    assert (e.message_id, e.thread_id, e.sender, e.rfc_message_id) == (
        "18c1", "18c0", "jai@novacrm.example", "<abc@mail.gmail.com>")
    assert e.subject == "Closed Won: Globex"


def test_reply_is_threaded():
    raw = build_reply(GmailReplyIn(thread_id="18c0", rfc_message_id="<abc@mail.gmail.com>", to="jai@novacrm.example",
                                   subject="Re: Closed Won: Globex", body="Please send the contact email."),
                      sender="cs@novacrm.example")
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw["raw"]))
    assert raw["threadId"] == "18c0"
    assert msg["In-Reply-To"] == "<abc@mail.gmail.com>" and msg["References"] == "<abc@mail.gmail.com>"
    assert msg["To"] == "jai@novacrm.example" and msg["Subject"] == "Re: Closed Won: Globex"


class FakeService:
    """Mimics googleapiclient's fluent interface for history/messages."""

    def __init__(self, history, messages):
        self._history, self._messages, self.sent = history, messages, []

    def users(self): return self
    def history(self): return self
    def messages(self): return self

    def list(self, **kw):
        self.last_list = kw
        return _Exec(self._history)

    def get(self, userId, id, format):
        return _Exec(self._messages[id])

    def send(self, userId, body):
        self.sent.append(body)
        return _Exec({"id": "sent1"})


class _Exec:
    def __init__(self, v): self.v = v
    def execute(self): return self.v


def test_new_messages_since_cursor_filters_to_deal_emails_and_dedupes():
    other = {**MSG, "id": "18c2", "payload": {**MSG["payload"], "headers": [
        {"name": "From", "value": "news@spam.example"}, {"name": "Subject", "value": "Weekly digest"}]}}
    history = {"historyId": "900", "history": [
        {"messagesAdded": [{"message": {"id": "18c1", "labelIds": ["INBOX"]}}]},
        {"messagesAdded": [{"message": {"id": "18c1", "labelIds": ["INBOX"]}},
                           {"message": {"id": "18c2", "labelIds": ["INBOX"]}}]},
    ]}
    g = GmailClient(service=FakeService(history, {"18c1": MSG, "18c2": other}), self_address="cs@novacrm.example")
    emails, cursor = g.new_deal_emails(start_history_id="800")
    assert [e.message_id for e in emails] == ["18c1"] and cursor == "900"
    assert g.service.last_list["startHistoryId"] == "800"


def test_own_replies_are_ignored():
    mine = {**MSG, "payload": {**MSG["payload"], "headers": [
        {"name": "From", "value": "cs@novacrm.example"}, {"name": "Subject", "value": "Re: Closed Won: Globex"}]}}
    history = {"historyId": "901", "history": [{"messagesAdded": [{"message": {"id": "18c1"}}]}]}
    g = GmailClient(service=FakeService(history, {"18c1": mine}), self_address="cs@novacrm.example")
    assert g.new_deal_emails("800")[0] == []


def test_custom_relevance_predicate_keeps_ae_mail_with_non_deal_subject():
    ae_mail = {**MSG, "payload": {**MSG["payload"], "headers": [
        {"name": "From", "value": "jai@novacrm.example"}, {"name": "Subject", "value": "RE: RE: quarter end"}]}}
    history = {"historyId": "902", "history": [{"messagesAdded": [{"message": {"id": "18c1"}}]}]}
    g = GmailClient(service=FakeService(history, {"18c1": ae_mail}), self_address="cs@novacrm.example")
    assert g.new_deal_emails("800")[0] == []  # default: subject filter only
    emails, _ = g.new_deal_emails("800", is_relevant=lambda e: e.sender == "jai@novacrm.example")
    assert [e.message_id for e in emails] == ["18c1"]


def test_slack_transport_errors_are_transient():
    from urllib.error import URLError

    class Down(FakeWeb):
        def chat_postMessage(self, **kw):
            raise URLError("connection reset")

    with pytest.raises(TransientToolError):
        SlackClient(client=Down()).post("C1", "x")
