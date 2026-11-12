"""Rocketlane REST client (docs/reference/rocketlane-api.md). Maps HTTP failures to registry error classes."""

import time
from collections.abc import Callable

import httpx

from app.services.keys import customer_key
from app.tools.registry import AmbiguousToolError, PermanentToolError, TransientToolError

COMPLETED_LABELS = {"completed", "done"}  # UNVERIFIED: Rocketlane's status list isn't documented (see list_tasks)


class RocketlaneClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.http = httpx.Client(base_url=base_url, timeout=timeout,
                                 headers={"api-key": api_key, "accept": "application/json"})
        self.sleep = sleep
        self._placeholder_ids: dict[str, int] | None = None

    # ---- low level ----
    def _request(self, method: str, path: str, *, write: bool = False, **kw) -> dict:
        try:
            r = self.http.request(method, path, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            raise TransientToolError(f"connect error: {e}") from e  # request never sent
        except httpx.TransportError as e:
            # Read timeout, reset, protocol error after the request went out: a write may have been applied.
            if write:
                raise AmbiguousToolError(f"{type(e).__name__} after sending {method} {path}") from e
            raise TransientToolError(f"{type(e).__name__}: {e}") from e
        if r.status_code == 429:
            raise TransientToolError(f"429: {_err(r)}")  # rate limited: not processed
        if r.status_code >= 500:
            # A gateway 5xx on a write can mean it committed: never re-POST blind.
            exc = AmbiguousToolError if write else TransientToolError
            raise exc(f"{r.status_code}: {_err(r)}")
        if r.status_code >= 400:
            raise PermanentToolError(f"{r.status_code}: {_err(r)}")
        return r.json() if r.content else {}

    def _get_all(self, path: str, **params) -> list[dict]:
        items, token = [], None
        while True:
            q = {**params, "pageSize": 100, **({"pageToken": token} if token else {})}
            body = self._request("GET", path, params=q)
            items += body.get("data", [])
            pg = body.get("pagination") or {}
            if not pg.get("hasMore") or not pg.get("nextPageToken"):
                return items
            token = pg["nextPageToken"]

    # ---- provider interface ----
    def _placeholder_id(self, name: str) -> int:
        if self._placeholder_ids is None:
            self._placeholder_ids = {p["placeholderName"].strip().lower(): int(p["placeholderId"])
                                     for p in self._get_all("/placeholders")}
        try:
            return self._placeholder_ids[name.strip().lower()]
        except KeyError:
            raise PermanentToolError(f"placeholder {name!r} not found in Rocketlane") from None

    def create_project(self, payload: dict) -> str:
        body = {**payload, "sources": [{**s, "templateId": int(s["templateId"])} for s in payload.get("sources", [])]}
        if payload.get("placeholders"):
            # The API wants IDs; config speaks names. Resolve before the POST so a bad name never half-creates.
            body["placeholders"] = [{"placeholderId": self._placeholder_id(p["placeholderName"]), "user": p["user"]}
                                    for p in payload["placeholders"]]
        out = self._request("POST", "/projects", json=body, write=True)
        return str(out["projectId"])

    def find_project(self, customer_name: str, external_reference_id: str, by_name: bool = True) -> str | None:
        hits = self._get_all("/projects", **{"externalReferenceId.eq": external_reference_id,
                                             "includeArchive.eq": "true"})
        if hits:
            return str(hits[0]["projectId"])
        if not by_name:
            return None
        # companyName.eq is exact and case-sensitive, so compare normalized names client-side.
        key = customer_key(customer_name)
        for p in self._get_all("/projects", **{"includeArchive.eq": "true"}):
            if customer_key((p.get("customer") or {}).get("companyName", "")) == key:
                return str(p["projectId"])
        return None

    def find_task(self, project_id: str, name_prefix: str) -> str | None:
        """GET /tasks?projectId.eq=&taskName.cn= (docs/reference/rocketlane-api.md §6). `cn` is a substring
        match, so keep only names that START with the prefix; refuse to guess between two."""
        hits = self._get_all("/tasks", **{"projectId.eq": project_id, "taskName.cn": name_prefix})
        want = name_prefix.strip().lower()
        matches = [t for t in hits if str(t.get("taskName", "")).strip().lower().startswith(want)]
        if len(matches) > 1:
            raise PermanentToolError(f"{len(matches)} tasks named {name_prefix!r} in project {project_id}")
        return str(matches[0]["taskId"]) if matches else None

    def append_task_note(self, task_id: str, note_html: str, marker: str) -> None:
        """Append evidence to the task's private (team-only) note. The public API has no comment endpoint
        (rocketlane-api.md §6), so the note is the documented fallback.

        GET /tasks/{id}?includeFields=taskPrivateNote and PUT /tasks/{id} {taskPrivateNote} are both on
        developer.rocketlane.com (get-task, update-task; checked 2026-11-11). UNVERIFIED against the live API:
        that a PUT with only taskPrivateNote leaves every other field (status, dates, assignees) unchanged.
        The body never carries status/progress: closing the task is a human's job.
        """
        current = self._request("GET", f"/tasks/{task_id}", params={"includeFields": "taskPrivateNote"})
        existing = current.get("taskPrivateNote") or ""
        if marker in existing:
            return  # this batch was already written (e.g. an ambiguous PUT being retried)
        self._request("PUT", f"/tasks/{task_id}", json={"taskPrivateNote": existing + note_html}, write=True)

    def list_tasks(self, project_id: str) -> list[dict]:
        """GET /tasks?projectId.eq= (documented, rocketlane-api.md §6), normalized to RlTask fields.

        VERIFIED live 2026-11-12: `phase` is null unless includeFields=phase is sent, then {phaseId, phaseName};
        default statuses are To do=1, In progress=2, Completed=3."""
        out = []
        for t in self._get_all("/tasks", **{"projectId.eq": project_id, "includeFields": "phase"}):
            status = t.get("status") or {}
            label = str(status.get("label") or "")
            due = t.get("dueDate")
            out.append({"task_id": str(t["taskId"]), "name": str(t.get("taskName", "")),
                        "phase": (t.get("phase") or {}).get("phaseName"),
                        "status_value": status.get("value"), "status_label": label,
                        "completed": label.strip().lower() in COMPLETED_LABELS,
                        "due_date": str(due) if due else None})
        return out

    def list_phases(self, project_id: str) -> list[dict]:
        """GET /phases?projectId= (documented). VERIFIED live 2026-11-12: each phase has its own status
        (To do / In Progress / Completed / Blocked) that a person can set in the UI without touching its tasks."""
        out = []
        for ph in self._get_all("/phases", projectId=project_id):
            out.append({"phase_id": str(ph.get("phaseId")), "name": str(ph.get("phaseName", "")),
                        "status_label": str((ph.get("status") or {}).get("label") or "")})
        return out

    def set_task_status(self, task_id: str, status_value: int) -> None:
        """PUT /tasks/{id} {status: {value}} (documented on update-task). The body carries nothing else.
        UNVERIFIED against the live API: that a status-only PUT leaves notes, dates and assignees unchanged (the
        same was verified for a taskPrivateNote-only PUT on 2026-11-11). Idempotent: same value twice is a no-op."""
        self._request("PUT", f"/tasks/{task_id}", json={"status": {"value": int(status_value)}}, write=True)

    def check_templates(self, configured: dict[str, str]) -> list[str]:
        """Preflight: every configured template ID must exist and be a PROJECT template."""
        found = {str(t["templateId"]): t for t in self._get_all("/templates")}
        problems = []
        for label, tid in configured.items():
            t = found.get(str(tid))
            if not t:
                problems.append(f"{label}: template {tid} not found")
            elif t.get("objectType") != "PROJECT":
                problems.append(f"{label}: template {tid} is a {t.get('objectType')} template, not PROJECT")
        return problems

    def check_placeholders(self, names: list[str]) -> list[str]:
        """Preflight: every placeholder the code fills must exist (GET /placeholders, verified 2026-11-11)."""
        problems = []
        for n in names:
            try:
                self._placeholder_id(n)
            except PermanentToolError:
                problems.append(f"placeholder {n!r} not found")
        return problems


def _err(r: httpx.Response) -> str:
    try:
        return "; ".join(e.get("errorMessage", "") for e in r.json().get("errors", [])) or r.text[:200]
    except ValueError:
        return r.text[:200]
