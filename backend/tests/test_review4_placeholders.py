"""Fourth tester review: Rocketlane role placeholders on project create. Real client + real registry,
HTTP mocked with respx. Tests marked DEFECT fail on the current code on purpose; do not weaken them."""

import contextlib

import httpx
import respx
from test_review2_defects import CREATE, RL_BASE, _rl_registry

from app.services.store import MemoryStore
from app.tools.registry import ToolFailed

PH = [{"placeholderId": 11, "placeholderName": "Project Manager", "placeholderType": "ROLE"}]
CREATE_PH = {**CREATE, "placeholders": {"Project Manager": "pm@x.example"}}


def _page(items):
    return httpx.Response(200, json={"data": items, "pagination": {"hasMore": False}})


@respx.mock
def test_transient_placeholder_lookup_is_retried_and_posts_exactly_once():
    """A 503 on GET /placeholders happens before the POST: safe to retry, must not escalate, one POST."""
    ph = respx.get(f"{RL_BASE}/placeholders").mock(side_effect=[httpx.Response(503), _page(PH)])
    posts = respx.post(f"{RL_BASE}/projects").mock(return_value=httpx.Response(201, json={"projectId": 7}))
    out = _rl_registry(MemoryStore()).call("intake", "rocketlane_create_project", CREATE_PH, run_id="r1",
                                           idempotency_key="project:r1")
    assert out.project_id == "7"
    assert ph.call_count == 2 and posts.call_count == 1
    body = httpx.Response(200, content=posts.calls.last.request.content).json()
    assert body["placeholders"] == [{"placeholderId": 11, "user": {"emailId": "pm@x.example"}}]


@respx.mock
def test_failed_lookup_after_ambiguous_create_does_not_repost():
    """DEFECT (CRITICAL outcome): POST /projects 504 -> AmbiguousToolError -> rl_create calls find_project.
    If that GET /projects also 5xxs, find_project raises TransientToolError from inside the except block
    (definitions.py rl_create), the registry treats it as retryable and re-POSTs: duplicate project.
    Expected: exactly one POST; the call fails (escalate for human verification)."""
    respx.get(f"{RL_BASE}/placeholders").mock(return_value=_page(PH))
    posts = respx.post(f"{RL_BASE}/projects").mock(side_effect=[
        httpx.Response(504, text="gateway timeout"), httpx.Response(201, json={"projectId": 99})])
    respx.get(f"{RL_BASE}/projects").mock(return_value=httpx.Response(503))
    with contextlib.suppress(ToolFailed):
        _rl_registry(MemoryStore()).call("intake", "rocketlane_create_project", CREATE_PH, run_id="r1",
                                         idempotency_key="project:r1")
    assert posts.call_count == 1, f"POST /projects sent {posts.call_count}x after an ambiguous 504"
