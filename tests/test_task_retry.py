"""UI-WF-03: retry a failed task of a paused goal, optionally with another agent of its role."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, executors, loop, registry
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import AuditEvent, Goal, Task, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def failed_and_paused(monkeypatch):
    """The build task fails while a person pauses the goal: it stops before its gate."""
    real, box = executors.OfflineExecutor._task, {"fail": True}

    def task(self, req):
        if req.writes and box["fail"]:
            box["fail"] = False
            loop.pause(box["gid"], actor="user:admin", reason="look at the failure")
            raise RuntimeError("the build broke")
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = box["gid"] = loop.create_goal("Retry me", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "paused"
    with session() as s:
        failed = [t for t in s.scalars(select(Task).where(Task.goal_id == gid)) if t.status == "failed"]
    assert len(failed) == 1, [failed]
    return gid, failed[0].id


def test_a_failed_task_of_a_paused_goal_runs_again_with_the_chosen_agent(monkeypatch):
    gid, tid = failed_and_paused(monkeypatch)
    other = registry.create_agent("developer", "Second Developer")["id"]
    out = loop.retry_task(tid, "user:admin", agent_id=other)
    assert out["resumed"] is True and out["agent_id"] == other
    with session() as s:
        t = s.get(Task, tid)
        assert (t.status, t.stage, t.gate["retry"]["by"]) == ("queued", "reopened", "user:admin")
        assert s.get(Goal, gid).status == "queued"
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    with session() as s:
        t = s.get(Task, tid)
        assert t.status == "done" and t.agent_id == other


def test_retry_is_refused_for_a_running_or_finished_goal_or_a_done_task(monkeypatch):
    gid, tid = failed_and_paused(monkeypatch)
    with session() as s:
        done = next(t.id for t in s.scalars(select(Task).where(Task.goal_id == gid)) if t.status == "done")
    with pytest.raises(ValueError, match="only a failed or skipped task"):
        loop.retry_task(done, "user:admin")
    qa = registry.staff("qa", goal_id=None)
    registry.release(qa.id)
    with pytest.raises(ValueError, match="the task needs a developer"):
        loop.retry_task(tid, "user:admin", agent_id=qa.id)
    loop.resume(gid, "user:admin")
    with pytest.raises(ValueError, match="pause it first"):
        loop.retry_task(tid, "user:admin")


def test_the_endpoint_checks_visibility_and_audits(monkeypatch):
    gid, tid = failed_and_paused(monkeypatch)
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    c = TestClient(app)
    assert c.post("/api/tasks/999999/retry").status_code == 404
    r = c.post(f"/api/tasks/{tid}/retry", json={})
    assert r.status_code == 200 and r.json()["resumed"] is True
    assert c.post(f"/api/tasks/{tid}/retry", json={}).status_code == 409, "queued now: nothing to retry"
    with session() as s:
        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "task_retry")).first() is not None
