"""Real Rocketlane client against mocked HTTP (respx). Shapes follow docs/reference/rocketlane-api.md
and the live account inspected on 2026-11-11 (GET /templates exists, objectType PROJECT|DOCUMENT)."""

import httpx
import pytest
import respx

from app.providers.real.rocketlane import RocketlaneClient
from app.tools.registry import AmbiguousToolError, PermanentToolError, TransientToolError

BASE = "https://api.rocketlane.com/api/1.0"
PAYLOAD = {"projectName": "Globex Onboarding", "owner": {"emailId": "o@x.example"},
           "customer": {"companyName": "Globex"}, "startDate": "2026-11-12",
           "sources": [{"templateId": "5000000114999", "startDate": "2026-11-12"}], "externalReferenceId": "006A"}


@pytest.fixture
def rl():
    return RocketlaneClient(BASE, "test-key", sleep=lambda s: None)


def page(items, more=False, token=None):
    return {"data": items, "pagination": {"hasMore": more, "nextPageToken": token}}


@respx.mock
def test_create_sends_api_key_and_numeric_template_id(rl):
    route = respx.post(f"{BASE}/projects").mock(return_value=httpx.Response(201, json={"projectId": 42}))
    assert rl.create_project(PAYLOAD) == "42"
    req = route.calls.last.request
    assert req.headers["api-key"] == "test-key"
    body = httpx.Response(200, content=req.content).json()
    assert body["sources"][0]["templateId"] == 5000000114999
    assert body["customer"] == {"companyName": "Globex"}


@respx.mock
@pytest.mark.parametrize("status,exc", [(500, TransientToolError), (503, TransientToolError),
                                        (429, TransientToolError), (400, PermanentToolError),
                                        (401, PermanentToolError), (404, PermanentToolError)])
def test_create_maps_http_errors(rl, status, exc):
    respx.post(f"{BASE}/projects").mock(return_value=httpx.Response(
        status, json={"errors": [{"errorCode": "X", "errorMessage": "boom"}]}))
    with pytest.raises(exc):
        rl.create_project(PAYLOAD)


@respx.mock
def test_read_timeout_on_create_is_ambiguous(rl):
    respx.post(f"{BASE}/projects").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AmbiguousToolError):
        rl.create_project(PAYLOAD)


@respx.mock
def test_connect_error_on_create_is_plain_transient(rl):
    respx.post(f"{BASE}/projects").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TransientToolError) as e:
        rl.create_project(PAYLOAD)
    assert not isinstance(e.value, AmbiguousToolError)


@respx.mock
def test_find_by_external_reference_first(rl):
    route = respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=page([{"projectId": 7}])))
    assert rl.find_project("Globex", "006A") == "7"
    params = route.calls[0].request.url.params
    assert params["externalReferenceId.eq"] == "006A" and params["includeArchive.eq"] == "true"


@respx.mock
def test_find_falls_back_to_normalized_customer_name_across_pages(rl):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        if "externalReferenceId.eq" in request.url.params:
            return httpx.Response(200, json=page([]))
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json=page([{"projectId": 1, "customer": {"companyName": "Initech"}}], True, "t2"))
        return httpx.Response(200, json=page([{"projectId": 2, "customer": {"companyName": "GLOBEX Corp."}}]))

    respx.get(f"{BASE}/projects").mock(side_effect=handler)
    assert rl.find_project("Globex Corporation", "006A") == "2"
    assert calls[-1]["pageToken"] == "t2"


@respx.mock
def test_find_returns_none_when_nothing_matches(rl):
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=page([])))
    assert rl.find_project("Globex", "006A") is None


@respx.mock
def test_template_check_reports_missing_and_non_project_templates(rl):
    respx.get(f"{BASE}/templates").mock(return_value=httpx.Response(200, json=page([
        {"templateId": 11, "templateName": "NovaCRM Enterprise Onboarding", "objectType": "PROJECT"},
        {"templateId": 12, "templateName": "Sales Handoff", "objectType": "DOCUMENT"},
    ])))
    problems = rl.check_templates({"enterprise": "11", "growth": "12", "other": "99"})
    assert problems == ["growth: template 12 is a DOCUMENT template, not PROJECT",
                        "other: template 99 not found"]


# ---- placeholder -> user mapping (GET /placeholders verified live on 2026-11-11) ----

PLACEHOLDERS = [{"placeholderId": 5000000094570, "placeholderName": "Customer", "placeholderType": "EXTERNAL"},
                {"placeholderId": 5000000094572, "placeholderName": "Customer success", "placeholderType": "NATIVE"},
                {"placeholderId": 5000000094574, "placeholderName": "Project Manager", "placeholderType": "NATIVE"}]
WITH_PH = {**PAYLOAD, "placeholders": [{"placeholderName": "Project Manager", "user": {"emailId": "pm@x.example"}},
                                       {"placeholderName": "customer SUCCESS", "user": {"emailId": "csm@x.example"}}]}


@respx.mock
def test_create_resolves_placeholder_names_to_ids(rl):
    respx.get(f"{BASE}/placeholders").mock(return_value=httpx.Response(200, json=page(PLACEHOLDERS)))
    route = respx.post(f"{BASE}/projects").mock(return_value=httpx.Response(201, json={"projectId": 7}))
    assert rl.create_project(WITH_PH) == "7"
    body = httpx.Response(200, content=route.calls.last.request.content).json()
    assert body["placeholders"] == [{"placeholderId": 5000000094574, "user": {"emailId": "pm@x.example"}},
                                    {"placeholderId": 5000000094572, "user": {"emailId": "csm@x.example"}}]


@respx.mock
def test_unknown_placeholder_blocks_create_without_posting(rl):
    respx.get(f"{BASE}/placeholders").mock(return_value=httpx.Response(200, json=page(PLACEHOLDERS[:1])))
    route = respx.post(f"{BASE}/projects").mock(return_value=httpx.Response(201, json={"projectId": 7}))
    with pytest.raises(PermanentToolError, match="Project Manager"):
        rl.create_project(WITH_PH)
    assert not route.called


@respx.mock
def test_placeholders_fetched_once_per_client(rl):
    ph = respx.get(f"{BASE}/placeholders").mock(return_value=httpx.Response(200, json=page(PLACEHOLDERS)))
    respx.post(f"{BASE}/projects").mock(return_value=httpx.Response(201, json={"projectId": 7}))
    rl.create_project(WITH_PH)
    rl.create_project(WITH_PH)
    assert ph.call_count == 1


@respx.mock
def test_create_without_placeholders_skips_lookup(rl):
    ph = respx.get(f"{BASE}/placeholders").mock(return_value=httpx.Response(200, json=page(PLACEHOLDERS)))
    respx.post(f"{BASE}/projects").mock(return_value=httpx.Response(201, json={"projectId": 7}))
    rl.create_project(PAYLOAD)
    assert not ph.called


@respx.mock
def test_check_placeholders_reports_missing(rl):
    respx.get(f"{BASE}/placeholders").mock(return_value=httpx.Response(200, json=page(PLACEHOLDERS[:2])))
    assert rl.check_placeholders(["Customer", "Project Manager"]) == ["placeholder 'Project Manager' not found"]
