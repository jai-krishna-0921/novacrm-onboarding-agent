"""Scripted, in-memory stand-ins for every external system. Used by tests and `make simulate`."""

import itertools

from app.services.keys import customer_key
from app.tools.definitions import (
    AeLookupOut,
    GmailReplyIn,
    GmailReplyOut,
    SlackInviteSharedOut,
    VoiceStartIn,
    VoiceStartOut,
)
from app.tools.registry import AmbiguousToolError, PermanentToolError, TransientToolError

DEFAULT_AES = {
    "jai@novacrm.example": AeLookupOut(found=True, name="Jai Krishna", email="jai@novacrm.example",
                                       phone="+919876543210", active=True),
    "maya@novacrm.example": AeLookupOut(found=True, name="Maya Rao", email="maya@novacrm.example",
                                        phone="+14155550101", active=True),
}


class FakeDirectory:
    def __init__(self, aes: dict[str, AeLookupOut] | None = None):
        self.aes = dict(DEFAULT_AES if aes is None else aes)

    def lookup(self, email: str) -> AeLookupOut:
        return self.aes.get(email.strip().lower(), AeLookupOut(found=False))


class FakeGmail:
    def __init__(self):
        self.sent: list[dict] = []

    def reply(self, a: GmailReplyIn) -> GmailReplyOut:
        self.sent.append(a.model_dump())
        return GmailReplyOut(message_id=f"sent-{len(self.sent)}")


class FakeVoice:
    """`script` holds one normalized call result (dict) per attempt, consumed in order."""

    def __init__(self, down: bool = False):
        self.down, self.script, self.calls = down, [], []

    def start_call(self, a: VoiceStartIn) -> VoiceStartOut:
        if self.down:
            raise TransientToolError("voice provider 503")
        call_id = f"call-{len(self.calls) + 1}"
        self.calls.append({**a.model_dump(), "provider_call_id": call_id})
        return VoiceStartOut(provider_call_id=call_id)

    def result_for(self, call_id: str) -> dict:
        outcome = self.script.pop(0) if self.script else {"status": "no_answer", "transcript": ""}
        return {"provider_call_id": call_id, **outcome}


class FakeRocketlane:
    def __init__(self, failures: int = 0, timeout_after_create: bool = False,
                 existing: list[dict] | None = None, search_lag: int = 0):
        self.failures_left, self.timeout_after_create = failures, timeout_after_create
        self.projects: list[dict] = list(existing or [])
        self.task_updates: list[dict] = []  # PUT /tasks bodies, so tests can prove no status is ever sent
        self._ids = itertools.count(1001)
        # Rocketlane search is eventually consistent ("a couple of seconds"): a new project stays
        # invisible to the next `search_lag` find calls.
        self.search_lag = search_lag
        self._hidden: dict[int, int] = {}

    def find_project(self, customer_name: str, external_reference_id: str) -> str | None:
        for pid in list(self._hidden):
            self._hidden[pid] -= 1
            if self._hidden[pid] < 0:
                del self._hidden[pid]
        for p in self.projects:
            if p["projectId"] in self._hidden:
                continue
            if p.get("externalReferenceId") == external_reference_id or \
                    customer_key(p["customer"]["companyName"]) == customer_key(customer_name):
                return str(p["projectId"])
        return None

    def create_project(self, payload: dict) -> str:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise TransientToolError("rocketlane 503")
        pid = next(self._ids)
        # Both tier templates contain a "Migration verified" task owned by the PM placeholder.
        project = {**payload, "projectId": pid,
                   "tasks": [{"taskId": pid * 100 + 1, "taskName": "Migration verified (validation checks passed)",
                              "status": {"value": 1, "label": "To do"}, "taskPrivateNote": ""}]}
        self.projects.append(project)
        if self.search_lag:
            self._hidden[project["projectId"]] = self.search_lag
        if self.timeout_after_create:
            self.timeout_after_create = False
            raise AmbiguousToolError("read timeout after request was sent")
        return str(project["projectId"])

    def find_task(self, project_id: str, name_prefix: str) -> str | None:
        want = name_prefix.lower()
        for p in self.projects:
            if str(p["projectId"]) == str(project_id):
                hits = [t for t in p.get("tasks", []) if t["taskName"].lower().startswith(want)]
                if len(hits) > 1:
                    raise PermanentToolError(f"{len(hits)} tasks named {name_prefix!r}")
                return str(hits[0]["taskId"]) if hits else None
        raise PermanentToolError(f"404: project {project_id} not found")

    def append_task_note(self, task_id: str, note_html: str, marker: str) -> None:
        for p in self.projects:
            for t in p.get("tasks", []):
                if str(t["taskId"]) == str(task_id):
                    if marker not in t["taskPrivateNote"]:
                        update = {"taskPrivateNote": t["taskPrivateNote"] + note_html}  # same body as the real PUT
                        self.task_updates.append(update)
                        t.update(update)
                    return
        raise PermanentToolError(f"404: task {task_id} not found")


class FakeSlack:
    def __init__(self, connect_enabled: bool = True, taken: set[str] | None = None):
        self.connect_enabled, self.taken = connect_enabled, set(taken or ())
        self.channels: dict[str, dict] = {}

    def create_channel(self, name: str) -> str:
        if name in self.taken or any(c["name"] == name for c in self.channels.values()):
            raise PermanentToolError("name_taken")
        cid = f"C{len(self.channels) + 1:04d}"
        self.channels[cid] = {"name": name, "topic": "", "members": [], "shared_invites": [],
                              "messages": []}
        return cid

    def set_topic(self, channel_id: str, topic: str) -> None:
        self.channels[channel_id]["topic"] = topic

    def invite(self, channel_id: str, user_ids: list[str]) -> None:
        self.channels[channel_id]["members"] += user_ids

    def invite_shared(self, channel_id: str, email: str) -> SlackInviteSharedOut:
        if not self.connect_enabled:
            return SlackInviteSharedOut(status="unsupported", detail="not_paid")
        self.channels[channel_id]["shared_invites"].append(email)
        return SlackInviteSharedOut(status="sent")

    def post(self, channel_id: str, text: str) -> None:
        self.channels[channel_id]["messages"].append(text)


class FakeWorld:
    def __init__(self, *, voice_down=False, rocketlane_failures=0, rocketlane_timeout_after_create=False,
                 existing_projects=None, slack_connect_enabled=True, taken_channel_names=None, aes=None,
                 rocketlane_search_lag=0):
        self.directory = FakeDirectory(aes)
        self.gmail = FakeGmail()
        self.voice = FakeVoice(down=voice_down)
        self.rocketlane = FakeRocketlane(rocketlane_failures, rocketlane_timeout_after_create,
                                         existing_projects, rocketlane_search_lag)
        self.slack = FakeSlack(slack_connect_enabled, taken_channel_names)
