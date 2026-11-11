"""Tool contracts (typed I/O), per-agent allowlists, and registry wiring.

Providers (real or fake) implement the same methods; payload builders live here so tests
assert on exactly what would be sent to the real API.
"""

import math
from typing import Literal, Protocol

from pydantic import BaseModel

from app.services.store import Store
from app.tools.registry import PermanentToolError, ToolRegistry

ALLOWLIST: dict[str, set[str]] = {
    "intake": {"ae_lookup", "gmail_reply", "voice_start_call",
               "rocketlane_find_project", "rocketlane_create_project"},
    "comms": {"slack_create_channel", "slack_set_topic", "slack_invite_internal",
              "slack_invite_shared", "slack_post_message"},
    "root": set(),
    # Migration validation (plan §12): may read tasks and append evidence; it has no tool that changes status.
    "migration": {"rocketlane_find_task", "rocketlane_add_task_note"},
    # Project tracking (services/tracking.py): reads tasks, reopens an unverified "Migration verified", appends
    # private notes, posts template progress lines. No project/channel creation, no invites.
    "tracking": {"rocketlane_list_tasks", "rocketlane_set_task_status", "rocketlane_find_task",
                 "rocketlane_add_task_note", "slack_post_message"},
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
    sender_authenticated: bool = False  # Gmail's DMARC/aligned-DKIM verdict for the From domain; fail closed
    auth_detail: str = ""


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
    message_id_header: str = ""  # deterministic RFC 822 Message-ID of the reply, so a resend can be detected


class GmailReplyOut(BaseModel):
    message_id: str


class VoiceStartIn(BaseModel):
    to_number: str
    ae_name: str
    customer_name: str
    run_id: str
    attempt: int
    idempotency_key: str = ""  # sent to Retell ([A-Za-z0-9_-]); a repeat within 1 h returns the same call


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
    placeholders: dict[str, str] = {}  # template role placeholder name -> user email


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


class RlFindTaskIn(BaseModel):
    project_id: str
    name_prefix: str


class RlFindTaskOut(BaseModel):
    task_id: str | None = None


class RlTaskNoteIn(BaseModel):
    task_id: str
    note_html: str
    marker: str  # unique per check batch; the provider skips the append if it is already in the note


class Ok(BaseModel):
    ok: bool = True


class RlTask(BaseModel):
    """A Rocketlane task normalized by the provider; `completed` is decided there (status label)."""
    task_id: str
    name: str
    phase: str | None = None
    status_value: int | None = None
    status_label: str = ""
    completed: bool = False
    due_date: str | None = None


class RlListTasksIn(BaseModel):
    project_id: str


class RlListTasksOut(BaseModel):
    tasks: list[RlTask]


class RlSetStatusIn(BaseModel):
    task_id: str
    status_value: int


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
        **({"placeholders": [{"placeholderName": n, "user": {"emailId": e}} for n, e in a.placeholders.items()]}
           if a.placeholders else {}),
    }


# ---- provider protocols ----

class Directory(Protocol):
    def lookup(self, email: str) -> AeLookupOut: ...


class Gmail(Protocol):
    def reply(self, a: GmailReplyIn) -> GmailReplyOut: ...
    def find_sent(self, message_id_header: str) -> str | None: ...


class Voice(Protocol):
    def start_call(self, a: VoiceStartIn) -> VoiceStartOut: ...


class Rocketlane(Protocol):
    def find_project(self, customer_name: str, external_reference_id: str, by_name: bool = True) -> str | None: ...
    def create_project(self, payload: dict) -> str: ...
    def find_task(self, project_id: str, name_prefix: str) -> str | None: ...
    def append_task_note(self, task_id: str, note_html: str, marker: str) -> None: ...
    def list_tasks(self, project_id: str) -> list[dict]: ...
    def set_task_status(self, task_id: str, status_value: int) -> None: ...


class Slack(Protocol):
    def create_channel(self, name: str) -> str: ...
    def find_channel(self, name: str) -> str | None: ...
    def find_post(self, channel_id: str, text: str) -> bool: ...
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

    # Reconcile lookups: after a crash or an ambiguous timeout, prove whether the write already happened
    # (registry.py) instead of repeating it. None = not found.
    def rl_create(a: RlCreateIn) -> RlCreateOut:
        return RlCreateOut(project_id=p.rocketlane.create_project(rocketlane_project_payload(a)))

    def rl_created(a: RlCreateIn) -> RlCreateOut | None:
        # Our own create carries our externalReferenceId; a name match could be someone else's project.
        pid = p.rocketlane.find_project(a.customer_name, a.external_reference_id, by_name=False)
        return RlCreateOut(project_id=pid) if pid else None

    def gmail_sent(a: GmailReplyIn) -> GmailReplyOut | None:
        if not a.message_id_header:
            raise PermanentToolError("no Message-ID to look the reply up by")
        mid = p.gmail.find_sent(a.message_id_header)
        return GmailReplyOut(message_id=mid) if mid else None

    def slack_channel_exists(a: SlackCreateIn) -> SlackCreateOut | None:
        cid = p.slack.find_channel(a.name)
        return SlackCreateOut(channel_id=cid) if cid else None

    def slack_posted(a: SlackPostIn) -> Ok | None:
        return Ok() if p.slack.find_post(a.channel_id, a.text) else None

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
    reg.register("gmail_reply", p.gmail.reply, GmailReplyIn, GmailReplyOut, reconcile=gmail_sent)
    # Retell dedupes on idempotency_key for 1 hour (docs/reference/retell-api.md); stay well inside it.
    reg.register("voice_start_call", p.voice.start_call, VoiceStartIn, VoiceStartOut, repeat_within=50 * 60)
    reg.register("rocketlane_find_project",
                 lambda a: RlFindOut(project_id=p.rocketlane.find_project(a.customer_name,
                                                                           a.external_reference_id)),
                 RlFindIn, RlFindOut)
    reg.register("rocketlane_create_project", rl_create, RlCreateIn, RlCreateOut, reconcile=rl_created)

    def rl_task_note(a: RlTaskNoteIn) -> Ok:
        p.rocketlane.append_task_note(a.task_id, a.note_html, a.marker)
        return Ok()

    reg.register("rocketlane_find_task",
                 lambda a: RlFindTaskOut(task_id=p.rocketlane.find_task(a.project_id, a.name_prefix)),
                 RlFindTaskIn, RlFindTaskOut)
    reg.register("rocketlane_add_task_note", rl_task_note, RlTaskNoteIn, Ok,
                 repeat_within=math.inf)  # the provider skips the append when the marker is already there

    def rl_set_status(a: RlSetStatusIn) -> Ok:
        p.rocketlane.set_task_status(a.task_id, a.status_value)
        return Ok()

    reg.register("rocketlane_list_tasks",
                 lambda a: RlListTasksOut(tasks=[RlTask(**t) for t in p.rocketlane.list_tasks(a.project_id)]),
                 RlListTasksIn, RlListTasksOut)
    # Setting a status, a topic or inviting again is harmless: those may simply be repeated.
    reg.register("rocketlane_set_task_status", rl_set_status, RlSetStatusIn, Ok, repeat_within=math.inf)
    reg.register("slack_create_channel",
                 lambda a: SlackCreateOut(channel_id=p.slack.create_channel(a.name)),
                 SlackCreateIn, SlackCreateOut, reconcile=slack_channel_exists)
    reg.register("slack_set_topic", slack_topic, SlackTopicIn, Ok, repeat_within=math.inf)
    reg.register("slack_invite_internal", slack_invite, SlackInviteIn, Ok, repeat_within=math.inf)
    reg.register("slack_invite_shared", lambda a: p.slack.invite_shared(a.channel_id, a.email),
                 SlackInviteSharedIn, SlackInviteSharedOut, repeat_within=math.inf)
    reg.register("slack_post_message", slack_post, SlackPostIn, Ok, reconcile=slack_posted)
    return reg
