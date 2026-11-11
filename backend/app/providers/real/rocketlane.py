"""Rocketlane REST client (docs/reference/rocketlane-api.md). Maps HTTP failures to registry error classes."""

import time
from collections.abc import Callable

import httpx

from app.services.keys import customer_key
from app.tools.registry import AmbiguousToolError, PermanentToolError, TransientToolError


class RocketlaneClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.http = httpx.Client(base_url=base_url, timeout=timeout,
                                 headers={"api-key": api_key, "accept": "application/json"})
        self.sleep = sleep

    # ---- low level ----
    def _request(self, method: str, path: str, *, write: bool = False, **kw) -> dict:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.ConnectError as e:
            raise TransientToolError(f"connect error: {e}") from e
        except httpx.TimeoutException as e:
            # A write that timed out after sending may have been applied.
            if write and not isinstance(e, httpx.ConnectTimeout):
                raise AmbiguousToolError(f"timeout after sending {method} {path}") from e
            raise TransientToolError(f"timeout: {e}") from e
        if r.status_code in (429,) or r.status_code >= 500:
            raise TransientToolError(f"{r.status_code}: {_err(r)}")
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
    def create_project(self, payload: dict) -> str:
        body = {**payload, "sources": [{**s, "templateId": int(s["templateId"])} for s in payload.get("sources", [])]}
        out = self._request("POST", "/projects", json=body, write=True)
        return str(out["projectId"])

    def find_project(self, customer_name: str, external_reference_id: str) -> str | None:
        hits = self._get_all("/projects", **{"externalReferenceId.eq": external_reference_id,
                                             "includeArchive.eq": "true"})
        if hits:
            return str(hits[0]["projectId"])
        # companyName.eq is exact and case-sensitive, so compare normalized names client-side.
        key = customer_key(customer_name)
        for p in self._get_all("/projects", **{"includeArchive.eq": "true"}):
            if customer_key((p.get("customer") or {}).get("companyName", "")) == key:
                return str(p["projectId"])
        return None

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


def _err(r: httpx.Response) -> str:
    try:
        return "; ".join(e.get("errorMessage", "") for e in r.json().get("errors", [])) or r.text[:200]
    except ValueError:
        return r.text[:200]
