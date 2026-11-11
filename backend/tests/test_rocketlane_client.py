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
