"""AICO UI-GM-04: with `require_plan_approval` a goal pauses after planning; Approve plan runs the stored plan,
Request revision plans the iteration again with the person's note as feedback."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, approvals, executors, loop, policies, prefs, projects, quality, router
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.contracts import TaskContract
from agent_company.db import Task, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture
def plan_loop(monkeypatch):
    state = {"plans": [], "ran": []}

    def plan(goal, it, feedback, notes):
        state["plans"].append(feedback)
        return [TaskContract(key="implement", title="implement", role="developer", description="Build it.",
                             writes=True)], None

    real = loop._execute

    def execute(agent, req, purpose, task_id=None, **kw):
        if purpose != "task":
            return real(agent, req, purpose, task_id=task_id, **kw)
        state["ran"].append(req.data["title"])
        Path(req.workdir, "feature.py").write_text("x = 1\n")
        return (executors.Result(text="Done: feature.py"),
                router.Choice(name="offline", kind="offline", model_id="offline", reason="test"))

    monkeypatch.setattr(loop, "_plan", plan)
    monkeypatch.setattr(loop, "_execute", execute)
    monkeypatch.setattr(loop, "enqueue", lambda goal_id: None)
    monkeypatch.setattr(quality, "evaluate", lambda gid, it: {"passed": False, "checks": [], "summary": "stop",
                                                               "feedback": "", "commit": ""})
    return state


def _project_with_plan_approval():
    p = projects.create("Plans")
    policies.set_project_rules(p["id"], {"require_plan_approval": True}, "test")
    return p["id"]


def test_without_the_rule_a_goal_runs_its_plan_at_once(plan_loop):
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    loop.run_goal(gid)
    assert plan_loop["ran"] == ["implement"] and loop._goal(gid).checkpoint.get("paused_for") != "plan"


def test_a_project_rule_pauses_the_goal_until_its_plan_is_approved(plan_loop):
    pid = _project_with_plan_approval()
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1, project_id=pid)
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert out["status"] == "paused" and out["awaiting"] == "plan" and g.checkpoint["paused_for"] == "plan"
    assert plan_loop["ran"] == [], "nothing runs before the plan is approved"
    with session() as s:
        assert [t.status for t in s.scalars(select(Task).where(Task.goal_id == gid))] == ["queued"]
    queue = approvals.queue(ADMIN)
    assert any(x["kind"] == "plan" and x["id"] == gid and x["actions"] == ["approve"] for x in queue)
    assert any(m["meta"].get("awaiting") == "plan" for m in projects.messages(pid))
    loop.approve_plan(gid, "user:admin")
    loop.run_goal(gid)
    assert plan_loop["ran"] == ["implement"] and len(plan_loop["plans"]) == 1, "the stored plan ran, not a new one"
    with pytest.raises(ValueError):
        loop.approve_plan(gid, "user:admin")


def test_request_revision_plans_the_iteration_again_with_the_note(plan_loop):
    pid = _project_with_plan_approval()
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1, project_id=pid)
    loop.run_goal(gid)
    with pytest.raises(ValueError):
        loop.revise_plan(gid, "user:admin", "  ")
    loop.revise_plan(gid, "user:admin", "Split the work into two tasks")
    out = loop.run_goal(gid)
    assert out["awaiting"] == "plan", "the revised plan waits for approval again"
    assert "Split the work into two tasks" in plan_loop["plans"][-1] and plan_loop["ran"] == []
    with session() as s:
        assert len(s.scalars(select(Task).where(Task.goal_id == gid)).all()) == 1, "the first plan was discarded"


def test_the_company_setting_and_the_api(plan_loop):
    prefs.set_value("require_plan_approval", True)
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    assert loop.run_goal(gid)["awaiting"] == "plan", "the company setting holds for goals outside a project"
    eval_goal = loop.create_goal("Benchmark", quality_commands=[], max_iterations=1, origin="eval")
    assert loop.run_goal(eval_goal).get("awaiting") is None, "a benchmark goal never waits for a person"
    loop.run_goal(gid)
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    c = TestClient(app)
    assert c.post(f"/api/goals/{gid}/plan/revise", json={"note": ""}).status_code == 400
    assert c.post(f"/api/goals/{gid}/plan/approve").json() == {"ok": True}
    assert c.post(f"/api/goals/{gid}/plan/approve").status_code == 409
    actions = {r["action"] for r in c.get("/api/audit").json()["records"]}
    assert "goal_plan_approve" in actions


def test_a_project_may_require_plan_approval_but_not_lift_it():
    pid = projects.create("Strict")["id"]
    assert policies.effective("require_plan_approval", pid) is False
    policies.set_project_rules(pid, {"require_plan_approval": True}, "test")
    assert policies.effective("require_plan_approval", pid) is True
    assert policies.set_project_rules(pid, {"require_plan_approval": False}, "test")["own"] == {}
