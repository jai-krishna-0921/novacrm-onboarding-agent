"""Tool contracts (typed I/O), per-agent allowlists, and registry wiring.

Providers (real or fake) implement the same methods; payload builders live here so tests
assert on exactly what would be sent to the real API.
"""

from typing import Literal, Protocol

from pydantic import BaseModel

from app.services.store import Store
from app.tools.registry import AmbiguousToolError, PermanentToolError, ToolRegistry

ALLOWLIST: dict[str, set[str]] = {
    "intake": {"ae_lookup", "gmail_reply", "voice_start_call",
               "rocketlane_find_project", "rocketlane_create_project"},
    "comms": {"slack_create_channel", "slack_set_topic", "slack_invite_internal",
              "slack_invite_shared", "slack_post_message"},
    "root": set(),
}


# ---- I/O models ----

class InboundEmail(BaseModel):
    message_id: str          # Gmail message id (dedupe key)
    thread_id: str
    rfc_message_id: str      # RFC 822 Message-ID header, for In-Reply-To
    sender: str
    subject: str
    body: str
    auto_reply: bool = False  # out-of-office / Auto-Submitted mail: never starts a run


class AeLookupIn(BaseModel):
    email: str


class AeLookupOut(BaseModel):
    found: bool
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    active: bool = False


class GmailReplyIn(BaseModel):
    thread_id: str
    rfc_message_id: str
    to: str
    subject: str
    body: str


class GmailReplyOut(BaseModel):
    message_id: str


class VoiceStartIn(BaseModel):
    to_number: str
    ae_name: str
    customer_name: str
    run_id: str
    attempt: int


class VoiceStartOut(BaseModel):
    provider_call_id: str


class RlFindIn(BaseModel):
    customer_name: str
    external_reference_id: str


class RlFindOut(BaseModel):
    project_id: str | None = None


class RlCreateIn(BaseModel):
    project_name: str
    customer_name: str
    owner_email: str
    start_date: str
    template_id: str
    member_emails: list[str]
    customer_contact_email: str
    external_reference_id: str


class RlCreateOut(BaseModel):
    project_id: str


class SlackCreateIn(BaseModel):
    name: str


class SlackCreateOut(BaseModel):
    channel_id: str


class SlackTopicIn(BaseModel):
    channel_id: str
    topic: str


class SlackInviteIn(BaseModel):
    channel_id: str
    user_ids: list[str]


class SlackInviteSharedIn(BaseModel):
    channel_id: str
    email: str


class SlackInviteSharedOut(BaseModel):
    status: Literal["sent", "unsupported"]
    detail: str = ""


class SlackPostIn(BaseModel):
    channel_id: str
    text: str


class Ok(BaseModel):
    ok: bool = True


def rocketlane_project_payload(a: RlCreateIn) -> dict:
    """Body for POST /projects (see docs/reference/rocketlane-api.md §2)."""
    return {
        "projectName": a.project_name,
        "owner": {"emailId": a.owner_email},
        "customer": {"companyName": a.customer_name},
        "autoCreateCompany": True,
        "startDate": a.start_date,
        "sources": [{"templateId": a.template_id, "startDate": a.start_date}],
        "assignProjectOwner": True,
        "teamMembers": {"members": [{"emailId": e} for e in a.member_emails],
                        "customers": [{"emailId": a.customer_contact_email}]},
        "externalReferenceId": a.external_reference_id,
    }


# ---- provider protocols ----

class Directory(Protocol):
    def lookup(self, email: str) -> AeLookupOut: ...


class Gmail(Protocol):
    def reply(self, a: GmailReplyIn) -> GmailReplyOut: ...


class Voice(Protocol):
    def start_call(self, a: VoiceStartIn) -> VoiceStartOut: ...


class Rocketlane(Protocol):
    def find_project(self, customer_name: str, external_reference_id: str) -> str | None: ...
    def create_project(self, payload: dict) -> str: ...


class Slack(Protocol):
    def create_channel(self, name: str) -> str: ...
    def set_topic(self, channel_id: str, topic: str) -> None: ...
    def invite(self, channel_id: str, user_ids: list[str]) -> None: ...
    def invite_shared(self, channel_id: str, email: str) -> SlackInviteSharedOut: ...
    def post(self, channel_id: str, text: str) -> None: ...


class Providers(Protocol):
    directory: Directory
    gmail: Gmail
    voice: Voice
    rocketlane: Rocketlane
    slack: Slack


def build_registry(store: Store, p: Providers, **kw) -> ToolRegistry:
    reg = ToolRegistry(store, ALLOWLIST, **kw)

    def rl_create(a: RlCreateIn) -> RlCreateOut:
        try:
            return RlCreateOut(project_id=p.rocketlane.create_project(rocketlane_project_payload(a)))
        except AmbiguousToolError as e:
            # The create may have been accepted. Look once; if search hasn't indexed it yet, a blind
            # re-POST could duplicate the project, so stop and let a human verify.
            if existing := p.rocketlane.find_project(a.customer_name, a.external_reference_id):
                return RlCreateOut(project_id=existing)
            raise PermanentToolError(f"create outcome unknown after timeout ({e}); verify in Rocketlane") from e

    def slack_topic(a: SlackTopicIn) -> Ok:
        p.slack.set_topic(a.channel_id, a.topic)
        return Ok()

    def slack_invite(a: SlackInviteIn) -> Ok:
        p.slack.invite(a.channel_id, a.user_ids)
        return Ok()

    def slack_post(a: SlackPostIn) -> Ok:
        p.slack.post(a.channel_id, a.text)
        return Ok()

    reg.register("ae_lookup", lambda a: p.directory.lookup(a.email), AeLookupIn, AeLookupOut)
    reg.register("gmail_reply", p.gmail.reply, GmailReplyIn, GmailReplyOut)
    reg.register("voice_start_call", p.voice.start_call, VoiceStartIn, VoiceStartOut)
    reg.register("rocketlane_find_project",
                 lambda a: RlFindOut(project_id=p.rocketlane.find_project(a.customer_name,
                                                                           a.external_reference_id)),
                 RlFindIn, RlFindOut)
    reg.register("rocketlane_create_project", rl_create, RlCreateIn, RlCreateOut)
    reg.register("slack_create_channel",
                 lambda a: SlackCreateOut(channel_id=p.slack.create_channel(a.name)),
                 SlackCreateIn, SlackCreateOut)
    reg.register("slack_set_topic", slack_topic, SlackTopicIn, Ok)
    reg.register("slack_invite_internal", slack_invite, SlackInviteIn, Ok)
    reg.register("slack_invite_shared", lambda a: p.slack.invite_shared(a.channel_id, a.email),
                 SlackInviteSharedIn, SlackInviteSharedOut)
    reg.register("slack_post_message", slack_post, SlackPostIn, Ok)
    return reg
