"""AICO UI-APPR-01 (one approval queue) and UI-DASH (the Dashboard's figures, project phases)."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, approvals, dashboard, projects
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import (Agent, Decision, DefinitionCandidate, Goal, Proposal, Requirement, Risk, Run, Task, now,
                              session)

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "member", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": "own"}}


def client(user):
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: user
    return TestClient(app)


def seed():
    p = projects.create("Queue")
    with session() as s:
        agent = s.scalars(select(Agent).where(Agent.role == "developer")).first()
        g = Goal(title="Ship it", description="", repo_path="", project_id=p["id"], status="awaiting_approval")
        s.add(g)
        s.flush()
        s.add_all([
            Decision(goal_id=g.id, project_id=p["id"], topic="Pick a database", requires_human_approval=True),
            Decision(goal_id=g.id, project_id=p["id"], topic="Agent-only", requires_human_approval=False),
            Proposal(goal_id=g.id, project_id=p["id"], agent_id=agent.id, title="Add caching"),
            Proposal(goal_id=g.id, project_id=p["id"], title="Done already", status="approved"),
            DefinitionCandidate(agent_id=agent.id, reason="study"),
            Requirement(project_id=p["id"], key="REQ-1", text="a"), Requirement(project_id=p["id"], key="REQ-2", text="b"),
        ])
        return p["id"], g.id, agent.id


def test_the_queue_lists_what_waits_for_a_person_and_nothing_else():
    pid, gid, aid = seed()
    items = approvals.queue(ADMIN)
    kinds = sorted(x["kind"] for x in items)
    assert kinds == ["decision", "definition", "goal", "proposal", "requirements"]
    by = {x["kind"]: x for x in items}
    assert by["goal"]["link"] == f"#/goals/{gid}" and by["goal"]["project"] == "Queue"
    assert by["decision"]["title"] == "Pick a database" and by["decision"]["actions"] == ["approve", "reject"]
    assert by["proposal"]["actions"] == ["approve", "reject"], "a follow-up is decided from the list"
    assert by["definition"]["link"] == f"#/agents/{aid}" and by["definition"]["actions"] == []
    assert by["requirements"]["count"] == 2 and by["requirements"]["title"] == "2 draft requirements"
    assert approvals.counts(items)["goal"] == 1


def test_a_member_sees_only_goals_of_projects_they_may_see_and_approve():
    seed()
    assert approvals.queue(MEMBER, set()) == [], "no delegation: nothing to approve"
    r = client(MEMBER).get("/api/approvals").json()
    assert r["total"] == 0 and r["items"] == []


def test_api_approvals_and_dashboard_answer_for_an_admin():
    pid, gid, aid = seed()
    with session() as s:
        t = Task(goal_id=gid, iteration=0, key="t1", title="t", role="developer", agent_id=aid, status="done",
                 finished_at=now())
        s.add(t)
        s.flush()
        s.add(Run(task_id=t.id, agent_id=aid, goal_id=gid, kind="cli", model_name="Claude Code", input_tokens=300,
                  output_tokens=100, cost=0.5))
        s.add(Run(agent_id=aid, kind="cli", model_name="Codex CLI", input_tokens=100, output_tokens=0, cost=0.1,
                  started_at=now() - timedelta(days=3)))
    c = client(ADMIN)
    q = c.get("/api/approvals").json()
    assert q["total"] == 5 and q["counts"]["goal"] == 1
    d = c.get("/api/dashboard?period=24h").json()
    assert d["period"] == "24h"
    assert d["current"]["spend"] == 0.5 and d["current"]["tasks_done"] == 1
    assert [m["name"] for m in d["models"]] == ["Claude Code"] and d["models"][0]["share"] == 1.0
    week = c.get("/api/dashboard?period=7d").json()
    assert {m["name"] for m in week["models"]} == {"Claude Code", "Codex CLI"}
    proj = next(p for p in d["projects"] if p["id"] == pid)
    assert proj["phase"] == "review", "a goal awaiting approval puts the project in QA/Review"
    assert any(g["id"] == gid and g["status"] == "awaiting_approval" for g in d["live_goals"])
    assert d["general_manager"] and d["general_manager"]["name"]
    assert sum(r["done"] for r in d["rooms"]) == 1
    assert c.get("/api/dashboard?period=1y").status_code == 400


def test_project_phase_is_derived_from_status_goals_blockers_and_milestones():
    done = [{"status": "completed", "progress": 1.0}]
    assert dashboard.phase("paused", ["running"], 0, []) == "paused"
    assert dashboard.phase("active", [], 0, []) == "draft"
    assert dashboard.phase("active", ["planning"], 0, []) == "planning"
    assert dashboard.phase("active", ["running", "planning"], 0, []) == "active"
    assert dashboard.phase("active", ["running"], 1, []) == "blocked"
    assert dashboard.phase("active", ["awaiting_approval"], 0, []) == "review"
    assert dashboard.phase("active", ["approved"], 0, done) == "release_ready"
    assert dashboard.phase("active", ["approved"], 0, [{"status": "open", "progress": 0.5}]) == "active"


def test_an_open_blocker_shows_the_project_blocked_on_the_dashboard():
    pid, gid, _ = seed()
    with session() as s:
        s.add(Risk(project_id=pid, kind="blocker", title="Waiting for keys", status="open"))
    proj = next(p for p in dashboard.report(ADMIN)["projects"] if p["id"] == pid)
    assert proj["phase"] == "blocked"
