"""UI-QA-03 (AT-014): a goal awaiting approval is returned to development with a reason; the next iteration plans
from it in the same worktree."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, executors, loop, workflow
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import AuditEvent, Goal, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def test_returned_work_is_planned_again_from_the_reason(monkeypatch):
    prompts = []
    real = executors.OfflineExecutor._plan

    def plan(self, req):
        prompts.append(req.prompt)
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    gid = loop.create_goal("Needs another look", quality_commands=["test -f WORKLOG.md"], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    with pytest.raises(ValueError, match="say what"):
        loop.return_to_development(gid, "user:admin", " ")
    out = loop.return_to_development(gid, "user:admin", "The worklog must name the date.")
    assert out == {"status": "queued", "next_iteration": 2, "extra_iteration": True}
    with session() as s:
        assert s.get(Goal, gid).max_iterations == 2
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    assert "The worklog must name the date." in prompts[-1]
    with session() as s:
        assert s.get(Goal, gid).iteration == 2
    assert [t["to"] for t in workflow.history(gid)][-4:] == ["queued", "planning", "running", "awaiting_approval"]


def test_only_a_goal_awaiting_approval_returns_and_the_api_audits():
    gid = loop.create_goal("Not yet")
    with pytest.raises(ValueError, match="not awaiting approval"):
        loop.return_to_development(gid, "user:admin", "redo")
    gid = loop.create_goal("Ready", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    c = TestClient(app)
    assert c.post(f"/api/goals/{gid}/return", json={"reason": ""}).status_code == 400
    r = c.post(f"/api/goals/{gid}/return", json={"reason": "Add a test for the edge case."})
    assert r.status_code == 200 and r.json()["extra_iteration"] is False
    with session() as s:
        assert s.get(Goal, gid).status == "queued"
        a = s.scalars(select(AuditEvent).where(AuditEvent.action == "goal_return")).one()
        assert "edge case" in a.after["reason"]
