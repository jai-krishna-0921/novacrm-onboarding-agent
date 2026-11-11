"""Real-provider reconcile lookups and request details that make a resend detectable or deduplicated."""

import json
from types import SimpleNamespace

import httpx
import respx

from app.providers.real.gmail import GmailClient, build_reply
from app.providers.real.retell import RetellVoice
from app.providers.real.slack import SlackClient
from app.tools.definitions import GmailReplyIn, VoiceStartIn


class Web:
    def __init__(self, channels, messages=()):
        self.channels, self.messages, self.kw = channels, list(messages), {}

    def auth_test(self):
        return {"user_id": "UBOT"}

    def conversations_list(self, **kw):
        self.kw["list"] = kw
        return {"channels": self.channels, "response_metadata": {"next_cursor": ""}}

    def conversations_history(self, **kw):
        return {"messages": self.messages}


def test_slack_find_channel_adopts_only_a_channel_this_bot_created():
    assert SlackClient(client=Web([{"id": "C1", "name": "acme-ent", "creator": "UBOT"}])).find_channel("acme-ent") == "C1"
    assert SlackClient(client=Web([{"id": "C2", "name": "acme-ent", "creator": "UHUMAN"}])).find_channel("acme-ent") is None
    assert SlackClient(client=Web([])).find_channel("acme-ent") is None


def test_slack_find_post_matches_our_own_message_with_slack_escaping():
    web = Web([], [{"user": "UBOT", "text": "Welcome &amp; hello &lt;team&gt;"}, {"user": "UX", "text": "Hi"}])
    c = SlackClient(client=web)
    assert c.find_post("C1", "Welcome & hello <team>") is True
    assert c.find_post("C1", "Hi") is False  # posted by someone else


def test_gmail_reply_carries_a_deterministic_message_id_and_find_sent_searches_by_it():
    a = GmailReplyIn(thread_id="t", rfc_message_id="<m@x>", to="ae@x", subject="Re: x", body="b",
                     message_id_header="<clarify-r1@novacrm.example>")
    import base64
    raw = base64.urlsafe_b64decode(build_reply(a, "cs@x")["raw"]).decode()
    assert "Message-ID: <clarify-r1@novacrm.example>" in raw
    seen = {}

    class Req:
        def __init__(self, out):
            self.out = out

        def execute(self):
            return self.out

    def lst(userId, q, maxResults):
        seen["q"] = q
        return Req({"messages": [{"id": "g1"}]})
    svc = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: SimpleNamespace(list=lst)))
    assert GmailClient(svc, "cs@x").find_sent("<clarify-r1@novacrm.example>") == "g1"
    assert seen["q"] == "in:sent rfc822msgid:clarify-r1@novacrm.example"


@respx.mock
def test_retell_create_call_sends_the_idempotency_key():
    route = respx.post("https://api.retellai.com/v2/create-phone-call").mock(
        return_value=httpx.Response(201, json={"call_id": "c-1"}))
    RetellVoice("k", "agent", "+10000000000").start_call(VoiceStartIn(
        to_number="+19999999999", ae_name="Jai", customer_name="Acme", run_id="r", attempt=1,
        idempotency_key="call-r-1-0"))
    assert json.loads(route.calls.last.request.content)["idempotency_key"] == "call-r-1-0"
