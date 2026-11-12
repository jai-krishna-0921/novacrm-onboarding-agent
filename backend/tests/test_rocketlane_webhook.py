"""Rocketlane webhook (Settings -> Webhooks, sent by a task automation): a task change triggers an immediate
Agent 3 sync of that project. Authenticated by a shared token (Basic Auth password or ?token=); the payload only
says which project changed, everything else is re-read through the API."""

import base64

import pytest
from test_tracking import api, mark, project
from test_workflow import GROWTH_CALL, run

import app.main as main

TOKEN = "rl-hook-secret"


@pytest.fixture
def env(settings, monkeypatch):
    monkeypatch.setattr(main, "RL_SETTLE_SECONDS", 0)
    s = settings.model_copy(update={"rocketlane_webhook_token": TOKEN})
    h, rid = run(s, calls=[GROWTH_CALL])
    return api(h, settings=s), h, rid


def event(pid, kind="TASK_UPDATED"):
    return {"eventType": kind, "resourceType": "TASK", "data": {"task": {
        "taskId": 1, "taskName": "x", "project": {"projectId": int(pid), "projectName": "p"}}}}


def basic(password, user="rocketlane"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def customer_posts(h, rid):
    return h.world.slack.channels[h.store.get_run(rid)["slack_channel_id"]]["messages"][1:]


def test_task_change_syncs_the_project_right_away(env):
    client, h, rid = env
    pid = project(h)["projectId"]
    mark(h, phase="Kickoff")
    r = client.post("/webhooks/rocketlane", json=event(pid), headers=basic(TOKEN))
    assert r.status_code == 204
    assert any("*Kickoff* phase" in m and "complete" in m for m in customer_posts(h, rid))
    assert h.store.get_tracking(rid)["tasks"]                       # snapshot stored: the dashboard updates


def test_token_in_the_url_also_works(env):
    client, h, rid = env
    mark(h, phase="Kickoff")
    assert client.post(f"/webhooks/rocketlane?token={TOKEN}", json=event(project(h)["projectId"])).status_code == 204
    assert customer_posts(h, rid)


@pytest.mark.parametrize("headers,query", [({}, ""), (basic("wrong"), ""), ({}, "?token=wrong"),
                                           ({"Authorization": "Basic !!!"}, "")])
def test_bad_or_missing_token_is_rejected_and_nothing_syncs(env, headers, query):
    client, h, rid = env
    mark(h, phase="Kickoff")
    r = client.post(f"/webhooks/rocketlane{query}", json=event(project(h)["projectId"]), headers=headers)
    assert r.status_code == 401 and customer_posts(h, rid) == []


def test_endpoint_is_closed_when_no_token_is_configured(settings):
    h, rid = run(settings, calls=[GROWTH_CALL])
    client = api(h)
    assert client.post("/webhooks/rocketlane?token=", json=event(project(h)["projectId"])).status_code == 401


def test_unknown_project_or_odd_payload_is_ignored_safely(env):
    client, h, rid = env
    for body in (event(999999), {"eventType": "TASK_UPDATED"}, {"data": {"task": {"project": {"projectId": "x;"}}}},
                 []):
        assert client.post("/webhooks/rocketlane", json=body, headers=basic(TOKEN)).status_code == 204
    assert client.post("/webhooks/rocketlane", content=b"{not json", headers=basic(TOKEN)).status_code == 400
    assert h.store.get_tracking(rid) is None                          # nothing synced
