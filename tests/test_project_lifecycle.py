"""ASEAO05 S05-R-06: project registration fields (stack, required skills, rooms, budget, deadline, priority) and
the lifecycle (pause, resume, archive, cancel, complete)."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import auth, executors, loop, projects, workflow
from agent_company.api import create_app
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Goal, Run, User, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def no_run(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    auth.register("admin", secrets.token_urlsafe(32))


def status(gid):
    with session() as s:
        return s.get(Goal, gid).status


def test_registration_fields_are_validated_and_shown():
    p = projects.create("Shop")
    out = projects.update(p["id"], {"stack": ["Python", "FastAPI", "Python"], "required_skills": ["API design"],
                                    "rooms": ["development"], "budget_usd": "12.5", "deadline": "2026-12-31",
                                    "priority": "high"})
    assert out["stack"] == ["Python", "FastAPI"] and out["required_skills"] == ["api_design"]
    assert out["rooms"] == ["development"] and out["budget_usd"] == 12.5 and out["priority"] == "high"
    assert out["deadline"].startswith("2026-12-31") and out["status"] == "active" and out["spent_usd"] == 0
    for bad in ({"rooms": ["nowhere"]}, {"priority": "urgent"}, {"budget_usd": -1}, {"deadline": "soon"},
                {"stack": "python"}, {"status": "paused"}):
        with pytest.raises(ValueError):
            projects.update(p["id"], bad)
    assert projects.update(p["id"], {"deadline": "2020-01-01"})["overdue"] is True
    assert "overdue" in projects.status_text(p["id"])


def test_pausing_pauses_its_goals_refuses_new_ones_and_resuming_brings_them_back():
    p = projects.create("Pausable")
    gid = projects.command(p["id"], ADMIN, "Build the thing")["goal_id"]
    own = loop.create_goal("Paused by hand", project_id=p["id"])
    loop.pause(own, "admin")
    out = projects.set_status(p["id"], "paused", "admin", "waiting for the customer")
    assert out["goals_changed"] == [gid] and status(gid) == "paused"
    out = projects.command(p["id"], ADMIN, "Another order")
    assert "goal_id" not in out, "a paused project starts no goal"
    assert "waiting for the customer" in projects.messages(p["id"])[-1]["content"]
    with pytest.raises(ValueError, match="paused"):
        loop.create_goal("sneaky", project_id=p["id"])
    assert projects.command(p["id"], ADMIN, "/status")["message_id"], "commands still answer"
    with pytest.raises(ValueError, match="resume the project first"):
        loop.resume(gid, "admin")
    out = projects.set_status(p["id"], "active", "admin")
    assert out["goals_changed"] == [gid] and status(gid) == "queued"
    assert status(own) == "paused", "a goal paused on its own stays paused"
    assert workflow.history(gid)[-1]["reason"] == "resumed"


def test_cancel_is_final_and_archive_or_complete_need_no_open_goals():
    p = projects.create("Closing")
    gid = loop.create_goal("Open work", project_id=p["id"])
    for to in ("archived", "completed"):
        with pytest.raises(ValueError, match="open goal"):
            projects.set_status(p["id"], to, "admin")
    loop.cancel(gid)
    assert projects.set_status(p["id"], "completed", "admin")["status"] == "completed"
    with pytest.raises(ValueError, match="can become: archived"):
        projects.set_status(p["id"], "active", "admin")
    assert projects.set_status(p["id"], "archived", "admin")["status"] == "archived"
    assert projects.set_status(p["id"], "active", "admin")["status"] == "active", "an archive can be restored"
    g2 = loop.create_goal("More work", project_id=p["id"])
    out = projects.set_status(p["id"], "cancelled", "admin", "dropped")
    assert out["goals_changed"] == [g2] and status(g2) == "cancelled"
    with pytest.raises(ValueError, match="final"):
        projects.set_status(p["id"], "active", "admin")
    q = projects.create("Waiting")
    done = loop.create_goal("Finished work", project_id=q["id"], max_iterations=1)
    assert loop.run_goal(done)["status"] == "awaiting_approval"
    projects.set_status(q["id"], "cancelled", "admin")
    with pytest.raises(ValueError, match="cancelled"):
        loop.approve(done)
    platform = next(x for x in projects.list_projects(ADMIN) if x["protected"])
    with pytest.raises(ValueError, match="platform"):
        projects.set_status(platform["id"], "paused", "admin")


def test_the_project_budget_caps_all_its_goals():
    p = projects.create("Capped")
    projects.update(p["id"], {"budget_usd": 0.05})
    gid = loop.create_goal("Spends", project_id=p["id"])
    with session() as s:
        s.add(Run(goal_id=gid, model_name="m", kind="llm", cost=0.06))
    with pytest.raises(loop.BudgetExhausted, match="project's budget"):
        loop._budget_note(loop._goal(gid), 0.0)
    with pytest.raises(ValueError, match="budget"):
        loop.create_goal("One more", project_id=p["id"])
    projects.update(p["id"], {"budget_usd": 1.0})
    assert loop.create_goal("Now it fits", project_id=p["id"])


def test_the_planner_sees_the_project_and_stays_in_its_rooms(monkeypatch):
    p = projects.create("Planned")
    projects.update(p["id"], {"stack": ["rust"], "required_skills": ["embedded"], "rooms": ["development"],
                              "priority": "critical"})
    prompts = []
    real = executors.Executor.run

    def spy(self, req):
        if req.purpose == "plan":
            prompts.append(req.prompt)
        return real(self, req)

    monkeypatch.setattr(executors.Executor, "run", spy)
    gid = loop.create_goal("Firmware", project_id=p["id"], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    assert "Technology stack: rust" in prompts[0] and "Required skills: embedded" in prompts[0]
    assert "- research:" not in prompts[0], "roles of other rooms are not offered"
    projects.update(p["id"], {"rooms": ["information_collection"]})
    gid = loop.create_goal("Firmware again", project_id=p["id"], max_iterations=1)
    with pytest.raises(RuntimeError, match="participating rooms"):
        loop.run_goal(gid)
    assert status(gid) == "failed"
    with session() as s:
        assert "participating rooms" in (s.get(Goal, gid).result or "")


def test_the_queue_takes_higher_priority_projects_first():
    low = projects.create("Someday")
    high = projects.create("Urgent")
    projects.update(low["id"], {"priority": "low"})
    projects.update(high["id"], {"priority": "critical"})
    a = loop.create_goal("low first in", project_id=low["id"])
    b = loop.create_goal("no project")
    c = loop.create_goal("critical last in", project_id=high["id"])
    q = loop.GoalQueue(workers=0)
    for gid in (a, b, c):
        q.submit(gid)
    assert [q.q.get()[2] for _ in range(3)] == [c, b, a]


def test_only_an_administrator_or_the_owner_changes_the_status():
    auth.register("member", secrets.token_urlsafe(32))
    auth.admin_action(1, 2, "approve")
    theirs = projects.create("Theirs", created_by=2)
    other = projects.create("Other")
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        admin_secret, member_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(admin_secret)
            s.get(User, 2).device_hash = auth._hash(member_secret)
        member = TestClient(app.app)
        assert member.post("/api/auth/device", json={"device_secret": member_secret}).status_code == 200
        assert app.post("/api/auth/device", json={"device_secret": admin_secret}).status_code == 200
        app.put("/api/users/2/permissions", json={"projects": "all"})
        r = member.post(f"/api/projects/{theirs['id']}/status", json={"status": "paused", "reason": "holiday"})
        assert r.status_code == 200 and r.json()["status"] == "paused"
        assert member.post(f"/api/projects/{other['id']}/status", json={"status": "paused"}).status_code == 403
        assert app.post(f"/api/projects/{other['id']}/status", json={"status": "nonsense"}).status_code == 400
        assert app.get(f"/api/projects/{theirs['id']}").json()["status_reason"] == "holiday"
    from agent_company.db import AuditEvent

    with session() as s:
        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "project_status")).first()
