"""Verification phase 5: project registration. TEST-PROJECT-001 and -002 are registered through the API, stored,
reopened, updated and checked for isolation: what one project's user, goals, agents and files can reach of the
other. Project goals run through the real loop on phase 4's two stub providers (GM: Model PM, developer: Model DEV).
Report: docs/verification/phase-5-projects.md."""
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_phase4_communication import DEV_REPLY, TASK, blocks, plan, pm_dev  # noqa: F401 - pm_dev is a fixture

from agent_company import auth, loop, organization, projects, registry
from agent_company.api import create_app
from agent_company.db import Agent, Goal, Run, Task, User, session

FIELDS = {"description": "Simple calculator application", "stack": ["python"], "rooms": ["development"],
          "priority": "normal"}


def _client(user_id, app=None):
    secret = secrets.token_urlsafe(32)
    with session() as s:
        s.get(User, user_id).device_hash = auth._hash(secret)
    c = TestClient(app.app if app is not None else create_app(start_background=False), client=("127.0.0.1", 50000))
    assert c.post("/api/auth/device", json={"device_secret": secret}).status_code == 200
    return c


@pytest.fixture
def clients():
    auth.register("admin", secrets.token_urlsafe(32))
    auth.register("member", secrets.token_urlsafe(32))
    auth.admin_action(1, 2, "approve")
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        admin = _client(1, app)
        admin.put("/api/users/2/permissions", json={"create_projects": True, "run": True})
        yield admin, _client(2, app)


def _agent(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None)).order_by(Agent.id)).first().id


def _register(client, name):
    """5.1: the plan's registration form: name, description, type, status, manager, developer, QA."""
    r = client.post("/api/projects", json={"name": name, "description": FIELDS["description"]})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    return pid


# --- 5.1 / 5.2 registration and storage --------------------------------------------------------------------------

def test_a_project_is_registered_stored_and_survives_a_new_session(clients):
    admin, member = clients
    pid = _register(member, "TEST-PROJECT-001")
    gm, dev, qa = _agent("general_manager"), _agent("developer"), _agent("qa")
    # the remaining fields: type (stack), participating rooms, and the developer and QA on the general topic
    assert admin.patch(f"/api/projects/{pid}", json={k: v for k, v in FIELDS.items() if k != "description"}).status_code == 200
    assert admin.patch(f"/api/projects/{pid}/topics/general", json={"agent_ids": [dev, qa]}).status_code == 200
    # a new app and a new sign-in: what a refreshed page sees comes from the database
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app2:
        fresh = _client(2, app2)
        p = fresh.get(f"/api/projects/{pid}").json()
        assert isinstance(pid, int) and p["id"] == pid and p["name"] == "TEST-PROJECT-001"
        assert (p["description"], p["stack"], p["rooms"], p["status"]) == (
            "Simple calculator application", ["python"], ["development"], "active")
        assert p["manager_id"] == gm and p["created_by"] == 2 and p["created_at"]
        assert {m["id"] for m in p["members"]} >= {gm}
        general = next(t for t in fresh.get(f"/api/projects/{pid}/topics").json() if t["name"] == "general")
        assert general["agent_ids"] == [dev, qa]
        assert [x["name"] for x in fresh.get("/api/projects").json()] == ["TEST-PROJECT-001"], "the member's own"
    # the manager position is on record (S01-R-02)
    assert organization.holder(f"project:{pid}:gm") == gm


def test_a_project_is_updated_paused_reopened_archived_and_reopened(clients):
    admin, member = clients
    pid = _register(member, "TEST-PROJECT-001")
    assert admin.patch(f"/api/projects/{pid}", json={"description": "Calculator with history"}).json()[
        "description"] == "Calculator with history"
    for to, then in (("paused", "paused"), ("active", "active"), ("archived", "archived"), ("active", "active")):
        r = member.post(f"/api/projects/{pid}/status", json={"status": to, "reason": f"phase 5: {to}"})
        assert r.status_code == 200 and r.json()["status"] == then, r.text
        assert member.get(f"/api/projects/{pid}").json()["status"] == then
    assert member.post(f"/api/projects/{pid}/status", json={"status": "completed"}).json()["status"] == "completed"
    assert member.post(f"/api/projects/{pid}/status", json={"status": "active"}).status_code == 400, \
        "a completed project is archived, not reopened"
    r = member.post(f"/api/projects/{pid}/command", json={"content": "Add a square root"})
    assert r.status_code == 200 and "goal_id" not in r.json(), "a closed project takes no new goals"


def test_the_projects_own_manager_plans_its_goals(pm_dev, clients):  # noqa: F811
    """V21: TEST-PROJECT-002 is given its own General Manager; its goals must be planned by that agent."""
    admin, member = clients
    pid1, pid2 = _register(member, "TEST-PROJECT-001"), _register(member, "TEST-PROJECT-002")
    gm1 = _agent("general_manager")
    gm2 = registry.create_agent("general_manager", title="Project Two Manager")["id"]
    with session() as s:
        s.get(Agent, gm2).model_pref = "Model PM"
    assert admin.patch(f"/api/projects/{pid2}", json={"manager_id": gm2, "manager_reason": "phase 5"}).status_code == 200
    for pid, gm in ((pid1, gm1), (pid2, gm2)):
        gid = loop.create_goal("factorial", "factorial with tests", repo_path=projects.get(pid, {"is_admin": True}).path,
                               project_id=pid, max_iterations=1)
        loop.run_goal(gid)
        with session() as s:
            [planner] = s.scalars(select(Run.agent_id).where(Run.goal_id == gid, Run.purpose == "plan")).all()
            [t] = s.scalars(select(Task).where(Task.goal_id == gid)).all()
            assert (planner, t.planned_by) == (gm, gm), f"project #{pid}'s goal planned by agent {planner}, not {gm}"
            assert t.status == "done"


# --- 5.3 isolation ------------------------------------------------------------------------------------------------

def test_one_projects_user_cannot_reach_the_others_project_goals_or_messages(pm_dev, clients):  # noqa: F811
    admin, member = clients
    own = _register(member, "TEST-PROJECT-001")
    other = _register(admin, "TEST-PROJECT-002")
    assert member.post(f"/api/projects/{other}/command", json={"content": "Read project 001"}).status_code == 404
    r = admin.post(f"/api/projects/{other}/command", json={"content": "/status"})
    assert r.status_code == 200
    gid = loop.create_goal("002's goal", "x", repo_path=projects.get(other, {"is_admin": True}).path,
                           project_id=other)
    loop._update_goal(gid, status="cancelled")
    for path in (f"/api/projects/{other}", f"/api/projects/{other}/messages", f"/api/projects/{other}/topics",
                 f"/api/goals/{gid}"):
        assert member.get(path).status_code == 404, path
    assert gid not in [g["id"] for g in member.get("/api/goals").json()]
    assert [p["id"] for p in member.get("/api/projects").json()] == [own]
    # each project's conversation is its own
    member.post(f"/api/projects/{own}/command", json={"content": "/status"})
    texts_own = [m["content"] for m in member.get(f"/api/projects/{own}/messages").json()]
    texts_other = [m["content"] for m in admin.get(f"/api/projects/{other}/messages").json()]
    assert "/status" in texts_own and "/status" in texts_other
    with session() as s:  # each message row belongs to one project
        from agent_company.db import Message

        assert {m.project_id for m in s.scalars(select(Message).where(Message.id.in_(
            [m["id"] for m in projects.messages(own)])))} == {own}


def test_one_projects_goal_cannot_write_the_other_projects_files(pm_dev):  # noqa: F811
    pm, dev = pm_dev
    p1, p2 = projects.create("TEST-PROJECT-001"), projects.create("TEST-PROJECT-002")
    assert Path(p1["path"]) != Path(p2["path"])
    target = Path(p2["path"]) / "calculator.py"
    target.write_text("ORIGINAL\n")
    rel = Path("..") / ".." / Path(p2["path"]).relative_to(Path(p1["path"]).parent.parent) / "calculator.py"
    pm.reply = plan({**TASK, "acceptance": [{"type": "min_length", "value": 5}]})
    dev.reply = blocks({str(rel): "HACKED\n"}) + "done"
    gid = loop.create_goal("factorial", "x", repo_path=p1["path"], project_id=p1["id"], max_iterations=1)
    loop.run_goal(gid)
    assert target.read_text() == "ORIGINAL\n", "project 002's file is untouched"
    with session() as s:
        [run] = s.scalars(select(Run).where(Run.goal_id == gid, Run.purpose == "task")).all()
        assert not run.ok and "outside the worktree" in run.error


def test_project_assignments_are_independent(clients):
    admin, member = clients
    pid1, pid2 = _register(member, "TEST-PROJECT-001"), _register(member, "TEST-PROJECT-002")
    gm2 = registry.create_agent("general_manager", title="Project Two Manager")["id"]
    dev, qa = _agent("developer"), _agent("qa")
    admin.patch(f"/api/projects/{pid2}", json={"manager_id": gm2})
    admin.patch(f"/api/projects/{pid1}/topics/general", json={"agent_ids": [dev]})
    admin.patch(f"/api/projects/{pid2}/topics/general", json={"agent_ids": [qa]})
    p1, p2 = member.get(f"/api/projects/{pid1}").json(), member.get(f"/api/projects/{pid2}").json()
    assert (p1["manager_id"], p2["manager_id"]) == (_agent("general_manager"), gm2)
    t1 = member.get(f"/api/projects/{pid1}/topics").json()[0]
    t2 = member.get(f"/api/projects/{pid2}/topics").json()[0]
    assert (t1["agent_ids"], t2["agent_ids"]) == ([dev], [qa])
    with session() as s:
        assert s.scalars(select(Goal)).all() == []
