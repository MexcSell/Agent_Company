"""G5 Control Hub UI-G5-MI: the mission form's fields on POST /api/goals and the mission contract (hub.py)."""

import subprocess
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from agent_company import api, hub, loop, prefs, projects
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Goal, Project, User, now, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "member", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": "own"}}


@pytest.fixture
def started(monkeypatch):
    """Goals are created, never run: the request's background start is a no-op here; the users exist."""
    monkeypatch.setattr(api, "_run_quietly", lambda gid: None)
    with session() as s:
        s.add_all([User(id=1, name="admin", is_admin=True, status="active"),
                   User(id=2, name="member", status="active")])


def client(user):
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: user
    app.state.queue = None
    return TestClient(app)


def test_a_basic_mission_stores_deliverable_deadline_budget_and_project(started, tmp_path):
    repo = tmp_path / "shop-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    p = projects.create("Shop")
    with session() as s:
        row = s.get(Project, p["id"])
        row.path, row.quality_commands = str(repo), ["pytest -q"]
    when = (now() + timedelta(days=2)).isoformat()
    r = client(ADMIN).post("/api/goals", json={"title": "Add a cart", "deliverable": "A cart page with tests",
                                               "deadline": when, "budget_usd": 2.5, "project_id": p["id"]})
    assert r.status_code == 200, r.text
    with session() as s:
        g = s.get(Goal, r.json()["id"])
        assert g.project_id == p["id"] and g.budget_usd == 2.5 and g.deadline is not None
        assert g.repo_path == str(repo) and g.quality_commands == ["pytest -q"], "the project's repo and checks"
        assert hub.DELIVERABLE_HEAD in g.description and "A cart page with tests" in g.description
    c = hub.mission_contract(r.json()["id"])
    assert c["objective"] == "Add a cart" and c["deliverable"] == "A cart page with tests"
    assert "`pytest -q` passes" in c["success_criteria"] and c["deadline"]


def test_advanced_limits_and_the_checks_on_the_new_fields(started):
    c = client(ADMIN)
    r = c.post("/api/goals", json={"title": "Refactor", "budget_tokens": 50000, "max_active_agents": 2,
                                   "allowed_paths": ["src/*"], "protect_tests": True, "permits": ["config"]})
    assert r.status_code == 200, r.text
    with session() as s:
        g = s.get(Goal, r.json()["id"])
        assert g.budget_tokens == 50000 and g.max_active_agents == 2
    contract = hub.mission_contract(r.json()["id"])
    assert "changes only in src/*" in contract["constraints"] and "existing tests protected" in contract["constraints"]
    assert "at most 2 agents at once" in contract["constraints"]
    assert c.post("/api/goals", json={"title": "Late", "deadline": (now() - timedelta(hours=1)).isoformat()}
                  ).status_code == 400
    assert c.post("/api/goals", json={"title": "Nowhere", "project_id": 9999}).status_code == 404
    assert c.post("/api/goals", json={"title": "Too many", "max_active_agents": 99}).status_code == 422


def test_a_member_cannot_start_a_mission_in_a_project_they_cannot_see(started):
    p = projects.create("Hidden")
    assert client(MEMBER).post("/api/goals", json={"title": "Sneak in", "project_id": p["id"]}).status_code == 404


def test_the_contract_names_who_approves():
    gid = loop.create_goal("Docs", "Write the docs")
    assert "a person approves the merge" in hub.mission_contract(gid)["approvals"] or \
        "merged when the quality gate passes" in hub.mission_contract(gid)["approvals"]
    prefs.set_value("require_plan_approval", True)
    prefs.set_value("auto_merge", True)
    approvals = hub.mission_contract(gid)["approvals"]
    assert approvals[0] == "a person approves the plan before work starts"
    assert "merged by the owner policy once the gate passes" in approvals
    gh = loop.create_goal("From an issue", "x", origin="github")
    assert any("work from github always waits" in a for a in hub.mission_contract(gh)["approvals"])
    with pytest.raises(KeyError):
        hub.mission_contract(424242)


def test_the_goal_endpoint_carries_the_contract():
    gid = loop.create_goal("Contract", "Objective text\n\n## Expected deliverable\nA report")
    d = client(ADMIN).get(f"/api/goals/{gid}").json()
    assert d["contract"]["deliverable"] == "A report" and d["contract"]["objective"] == "Objective text"
