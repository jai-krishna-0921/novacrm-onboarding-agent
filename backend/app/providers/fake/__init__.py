"""Scripted, in-memory stand-ins for every external system. Used by tests and `make simulate`."""

import itertools
import re

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

    def find_sent(self, message_id_header: str) -> str | None:
        hits = [i for i, m in enumerate(self.sent, 1) if m.get("message_id_header") == message_id_header]
        return f"sent-{hits[0]}" if hits else None


class FakeVoice:
    """`script` holds one normalized call result (dict) per attempt, consumed in order."""

    def __init__(self, down: bool = False):
        self.down, self.script, self.calls = down, [], []

    def start_call(self, a: VoiceStartIn) -> VoiceStartOut:
        if self.down:
            raise TransientToolError("voice provider 503")
        if a.idempotency_key and (seen := [c for c in self.calls if c["idempotency_key"] == a.idempotency_key]):
            return VoiceStartOut(provider_call_id=seen[0]["provider_call_id"])  # Retell replays: no new dial
        call_id = f"call-{len(self.calls) + 1}"
        self.calls.append({**a.model_dump(), "provider_call_id": call_id})
        return VoiceStartOut(provider_call_id=call_id)

    def result_for(self, call_id: str) -> dict:
        outcome = self.script.pop(0) if self.script else {"status": "no_answer", "transcript": ""}
        return {"provider_call_id": call_id, **outcome}


# The two CS-owned templates (docs/ROCKETLANE_SETUP.md): phase -> task names, in plan order.
TEMPLATE_TASKS = {
    "enterprise": {
        "Kickoff": ["Internal sales→CS handoff review", "Kickoff call", "Success plan & goals sign-off"],
        "Data Migration": ["Source data export", "Field mapping review", "Test import", "Full import",
                           "Migration verified (validation checks passed)"],
        "Configuration": ["Pipelines & stages setup", "Roles & permissions", "Integrations setup", "SSO setup"],
        "Go-Live": ["Admin training", "End-user training", "Go-live checklist sign-off", "Handoff to support"],
    },
    "growth": {
        "Kickoff": ["Kickoff call", "Success plan"],
        "Data Migration": ["Data export", "Data import", "Migration verified"],
        "Configuration": ["Pipelines & stages", "Roles & permissions", "Basic integrations"],
        "Go-Live": ["User training", "Go-live checklist", "Handoff to support"],
    },
}
STATUS_LABELS = {1: "To do", 2: "In progress", 3: "Completed"}
TODO, COMPLETED = {"value": 1, "label": "To do"}, {"value": 3, "label": "Completed"}


class FakeRocketlane:
    def __init__(self, failures: int = 0, timeout_after_create: bool = False,
                 existing: list[dict] | None = None, search_lag: int = 0):
        self.failures_left, self.timeout_after_create = failures, timeout_after_create
        self.projects: list[dict] = list(existing or [])
        self.task_updates: list[dict] = []  # PUT /tasks bodies, so tests can prove what was sent
        self.broken_projects: set[str] = set()  # list_tasks raises for these (per-run isolation tests)
        self.phases_down = False  # list_phases raises (Agent 3 must keep its task rules working)
        self._ids = itertools.count(1001)
        # Rocketlane search is eventually consistent ("a couple of seconds"): a new project stays
        # invisible to the next `search_lag` find calls.
        self.search_lag = search_lag
        self._hidden: dict[int, int] = {}

    def find_project(self, customer_name: str, external_reference_id: str, by_name: bool = True) -> str | None:
        for pid in list(self._hidden):
            self._hidden[pid] -= 1
            if self._hidden[pid] < 0:
                del self._hidden[pid]
        for p in self.projects:
            if p["projectId"] in self._hidden:
                continue
            if p.get("externalReferenceId") == external_reference_id or \
                    (by_name and customer_key(p["customer"]["companyName"]) == customer_key(customer_name)):
                return str(p["projectId"])
        return None

    def create_project(self, payload: dict) -> str:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise TransientToolError("rocketlane 503")
        pid = next(self._ids)
        tier = "growth" if "(Growth" in payload.get("projectName", "") else "enterprise"
        ids = itertools.count(pid * 100 + 1)
        project = {**payload, "projectId": pid,
                   "tasks": [{"taskId": next(ids), "taskName": name, "phase": {"phaseName": phase},
                              "status": dict(TODO), "dueDate": None, "taskPrivateNote": ""}
                             for phase, names in TEMPLATE_TASKS[tier].items() for name in names]}
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


    def _project(self, project_id: str) -> dict:
        for p in self.projects:
            if str(p["projectId"]) == str(project_id):
                return p
        raise PermanentToolError(f"404: project {project_id} not found")

    def list_tasks(self, project_id: str) -> list[dict]:
        if str(project_id) in self.broken_projects:
            raise PermanentToolError(f"404: project {project_id} not found")
        return [{"task_id": str(t["taskId"]), "name": t["taskName"], "phase": (t.get("phase") or {}).get("phaseName"),
                 "status_value": t["status"]["value"], "status_label": t["status"]["label"],
                 "completed": t["status"]["label"] == "Completed", "due_date": t.get("dueDate")}
                for t in self._project(project_id).get("tasks", [])]

    def list_phases(self, project_id: str) -> list[dict]:
        if self.phases_down:
            raise TransientToolError("503 on list_phases")
        p = self._project(project_id)
        names = dict.fromkeys((t.get("phase") or {}).get("phaseName") for t in p.get("tasks", []))
        status = p.setdefault("phase_status", {})
        return [{"phase_id": f"ph-{n}", "name": n, "status_label": status.get(n, "To do")} for n in names if n]

    def set_task_status(self, task_id: str, status_value: int) -> None:
        for p in self.projects:
            for t in p.get("tasks", []):
                if str(t["taskId"]) == str(task_id):
                    update = {"status": {"value": status_value}}  # same body as the real PUT
                    self.task_updates.append(update)
                    t["status"] = {"value": status_value, "label": STATUS_LABELS.get(status_value, "Custom")}
                    return
        raise PermanentToolError(f"404: task {task_id} not found")

    # ---- test helpers: what a person does in the Rocketlane UI ----
    def set_phase_status(self, project_id: str, phase: str, label: str) -> None:
        """The phase's own status menu (To do / In Progress / Completed / Blocked). Its tasks are left as they are,
        exactly like Rocketlane (verified live 2026-11-12)."""
        self._project(project_id).setdefault("phase_status", {})[phase] = label

    def mark(self, project_id: str, *name_prefixes: str, phase: str | None = None, status: dict = COMPLETED) -> None:
        tasks = self._project(project_id)["tasks"]
        hits = [t for t in tasks if (phase and (t.get("phase") or {}).get("phaseName") == phase)
                or any(t["taskName"].lower().startswith(n.lower()) for n in name_prefixes)]
        assert hits, f"no task matches {name_prefixes or phase}"
        for t in hits:
            t["status"] = dict(status)


class FakeSlack:
    def __init__(self, connect_enabled: bool = True, taken: set[str] | None = None):
        self.connect_enabled, self.taken = connect_enabled, set(taken or ())
        self.channels: dict[str, dict] = {}

    def create_channel(self, name: str) -> str:
        # `taken` names are given without the run suffix (a test can't know the run id beforehand).
        if name in self.taken or re.sub(r"-[0-9a-f]{8}$", "", name) in self.taken \
                or any(c["name"] == name for c in self.channels.values()):
            raise PermanentToolError("name_taken")
        cid = f"C{len(self.channels) + 1:04d}"
        self.channels[cid] = {"name": name, "topic": "", "members": [], "shared_invites": [],
                              "messages": []}
        return cid

    def find_channel(self, name: str) -> str | None:
        # `taken` names belong to someone else; channels in `self.channels` were created by this app.
        return next((cid for cid, c in self.channels.items() if c["name"] == name), None)

    def find_post(self, channel_id: str, text: str) -> bool:
        return text in self.channels.get(channel_id, {}).get("messages", [])

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
        if channel_id not in self.channels:
            raise PermanentToolError("channel_not_found")  # same as the real Slack client
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
