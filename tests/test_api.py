import secrets
import time

import pytest
from fastapi.testclient import TestClient

from agent_company.api import create_app


@pytest.fixture
def app():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        yield c


def secret():
    return secrets.token_urlsafe(32)


def test_first_administrator_only_from_the_server_itself(app):
    remote = TestClient(app.app, client=("203.0.113.7", 40000))
    r = remote.post("/api/auth/register", json={"name": "intruder", "device_secret": secret()})
    assert r.status_code == 403 and r.json()["code"] == "setup_local_only"
    assert app.post("/api/auth/register", json={"name": "luca", "device_secret": secret()}).json()["user"]["is_admin"]
    r = remote.post("/api/auth/register", json={"name": "visitor", "device_secret": secret()})
    assert r.status_code == 200 and r.json()["user"]["status"] == "pending"


def test_device_registration_and_approval_flow(app):
    assert app.get("/api/agents").status_code == 401
    admin_secret = secret()
    r = app.post("/api/auth/register", json={"name": "luca", "device_secret": admin_secret})
    assert r.json()["signed_in"] and r.json()["user"]["is_admin"]
    admin_cookie = app.cookies.get("ac_session")
    assert app.get("/api/auth/me").json()["name"] == "luca"

    other = TestClient(app.app)
    s2 = secret()
    r = other.post("/api/auth/register", json={"name": "thomas", "device_secret": s2,
                                               "device_info": {"label": "Firefox on Linux"}})
    assert r.json()["signed_in"] is False and r.json()["user"]["status"] == "pending"
    assert other.post("/api/auth/device", json={"device_secret": s2}).json()["code"] == "pending"
    assert other.post("/api/auth/register", json={"name": "thomas", "device_secret": secret()}).status_code == 409
    assert other.post("/api/auth/register", json={"name": "luca2", "device_secret": s2}).status_code == 409
    assert other.get("/api/users").status_code == 401

    users = app.get("/api/users").json()
    uid = next(u["id"] for u in users if u["name"] == "thomas")
    assert app.post(f"/api/users/{uid}/approve").json()["status"] == "active"
    r = other.post("/api/auth/device", json={"device_secret": s2})
    assert r.status_code == 200
    assert other.get("/api/auth/me").json()["name"] == "thomas"
    assert other.get("/api/users").status_code == 403
    # a computer that was never registered is refused
    assert other.post("/api/auth/device", json={"device_secret": secret()}).json()["code"] == "unknown_device"
    # disabling ends the session
    app.cookies.set("ac_session", admin_cookie)
    app.post(f"/api/users/{uid}/disable")
    assert other.get("/api/auth/me").status_code == 401
    assert other.post("/api/auth/device", json={"device_secret": s2}).json()["code"] == "disabled"
    assert app.post(f"/api/users/{users[0]['id']}/disable").status_code == 400  # not yourself


def test_goal_lifecycle_over_http(app):
    app.post("/api/auth/register", json={"name": "admin", "device_secret": secret()})
    gid = app.post("/api/goals", json={"title": "Write a hello world", "quality_commands": ["test -f WORKLOG.md"]}).json()["id"]
    for _ in range(100):
        g = app.get(f"/api/goals/{gid}").json()
        if g["status"] not in {"queued", "planning", "running", "recovering"}:
            break
        time.sleep(0.1)
    assert g["status"] == "awaiting_approval", g
    assert len(g["tasks"]) == 3 and g["events"]
    assert app.post(f"/api/goals/{gid}/approve").json()["status"] == "approved"
    o = app.get("/api/overview").json()
    assert o["goals"]["approved"] == 1
    u = app.get("/api/usage").json()
    assert u["totals"]["runs"] > 0
    d = app.get("/api/agents/1").json()
    assert "lessons" in d and "related" in d


def test_non_admin_cannot_run_commands(app):
    app.post("/api/auth/register", json={"name": "admin", "device_secret": secret()})
    other = TestClient(app.app)
    s2 = secret()
    other.post("/api/auth/register", json={"name": "member", "device_secret": s2})
    uid = next(u["id"] for u in app.get("/api/users").json() if u["name"] == "member")
    app.post(f"/api/users/{uid}/approve")
    other.post("/api/auth/device", json={"device_secret": s2})
    r = other.post("/api/goals", json={"title": "x" * 5, "quality_commands": ["rm -rf /"]})
    assert r.status_code == 403
    assert other.post("/api/models", json={"name": "a", "kind": "llm", "model_id": "x/y"}).status_code == 403
    assert other.patch("/api/agents/1", json={"enabled": False}).status_code == 403


def test_models_and_dashboard(app):
    assert app.get("/").status_code == 200
    app.post("/api/auth/register", json={"name": "admin", "device_secret": secret()})
    m = app.get("/api/models").json()
    assert any(x["kind"] == "offline" for x in m["models"]) and m["routes"]["developer"] == "Claude Code"
    r = app.post("/api/models", json={"name": "Local Qwen", "kind": "llm", "model_id": "ollama/qwen3-coder"})
    assert r.status_code == 200 and r.json()["available"] is False
    assert app.put("/api/routes/qa", json={"model_name": "Local Qwen"}).status_code == 200
    assert app.put("/api/routes/qa", json={"model_name": "nope"}).status_code == 400
    assert app.patch(f"/api/models/{r.json()['id']}", json={"name": "Claude Code"}).status_code == 400
    assert app.patch(f"/api/models/{r.json()['id']}", json={"name": "Qwen"}).json()["name"] == "Qwen"
    assert app.get("/api/models").json()["routes"]["qa"] == "Qwen"
    assert app.delete(f"/api/models/{r.json()['id']}").status_code == 200
    assert app.patch("/api/agents/1", json={"name": "renamed"}).status_code == 400


def test_cli_bootstrap_does_not_free_busy_agents():
    from agent_company import registry
    from agent_company.api import bootstrap

    a = registry.staff("developer")
    bootstrap()  # what `agents`/`models`/`goal` CLI commands and the supervisor's candidate run
    assert next(x for x in registry.list_agents() if x["id"] == a.id)["status"] == "busy"
