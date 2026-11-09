"""FW-R-07 (LangGraph time travel): continue a goal from an earlier iteration's checkpoint commit.
FW-R-08 (OpenHands condensers, SWE-agent history processors): the planner sees earlier iterations condensed."""
import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, auth, executors, loop, prefs, worktrees
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import AuditEvent, Goal, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "m", "is_admin": False, "permissions": dict(DEFAULT_PERMISSIONS)}
NEVER = "test -f NEVER.md"  # the offline simulator never writes it: every iteration fails the gate


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


@pytest.fixture
def prompts(monkeypatch):
    seen = []
    real = executors.OfflineExecutor._plan

    def plan(self, req):
        seen.append(req.prompt)
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    return seen


def _failed_goal(iterations: int = 2) -> Goal:
    gid = loop.create_goal("Write the release notes", quality_commands=[NEVER], max_iterations=iterations)
    assert loop.run_goal(gid)["status"] == "failed"
    with session() as s:
        g = s.get(Goal, gid)
        s.expunge(g)
    assert [h["iteration"] for h in g.history] == list(range(1, iterations + 1))
    return g


def test_fork_starts_at_the_iteration_commit_with_its_plan_and_findings(prompts):
    src = _failed_goal()
    first = src.history[0]
    assert first["commit"] and "NEVER.md" in first["findings"]
    new = loop.fork_goal(src.id, 1, "user:admin")
    with session() as s:
        g = s.get(Goal, new)
        assert g.title.endswith(f"(from #{src.id}, iteration 1)") and g.origin == src.origin
        assert g.quality_commands == [NEVER] and g.repo_path == src.repo_path
        assert g.baseline == src.baseline  # measured on the untouched code, not re-run on the fork's commit
        assert g.checkpoint["fork"]["commit"] == first["commit"]
    repo = src.repo_path
    assert worktrees.git(repo, "rev-parse", f"agents/goal-{new}") == first["commit"]
    prompts.clear()
    loop.run_goal(new)
    assert f"continues goal #{src.id}" in prompts[0] and "The plan of iteration 1 was:" in prompts[0]
    assert "NEVER.md" in prompts[0]
    with session() as s:
        wt = s.get(Goal, new).worktree
    assert worktrees.git(wt, "merge-base", "--is-ancestor", first["commit"], "HEAD", check=False) == ""
    with session() as s:  # the original goal is untouched
        assert s.get(Goal, src.id).status == "failed" and s.get(Goal, src.id).history == src.history


def test_fork_refusals():
    src = _failed_goal(1)
    with pytest.raises(ValueError, match="no checkpoint commit for iteration 5"):
        loop.fork_goal(src.id, 5, "user:admin")
    with session() as s:
        s.get(Goal, src.id).origin = "eval"
    with pytest.raises(ValueError, match="origin eval cannot be forked"):
        loop.fork_goal(src.id, 1, "user:admin")


def test_fork_api_is_audited_and_privileged_goals_need_an_administrator():
    src = _failed_goal(1)
    auth.register("admin", secrets.token_urlsafe(32))  # users 1 and 2: a fork's created_by
    auth.register("member", secrets.token_urlsafe(32))
    app = api.create_app(start_background=False)
    c = TestClient(app)
    app.dependency_overrides[api.current_user] = lambda: MEMBER
    assert c.post(f"/api/goals/{src.id}/fork", json={"iteration": 1}).status_code == 403  # it has quality commands
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    assert c.post(f"/api/goals/{src.id}/fork", json={"iteration": 9}).status_code == 409
    r = c.post(f"/api/goals/{src.id}/fork", json={"iteration": 1})
    assert r.status_code == 200
    with session() as s:
        assert s.get(Goal, r.json()["id"]).status == "queued"
        actions = [a.action for a in s.scalars(select(AuditEvent))]
    assert "goal_fork" in actions and "goal_fork_denied" in actions


def _goal(history, trial=None):
    return SimpleNamespace(history=history, trial=trial or {})


HIST = [{"iteration": i, "passed": False, "score": 0.25 * i, "failed_checks": [f"check-{i}"],
         "findings": f"finding of iteration {i}\nmore"} for i in (1, 2, 3)]


def test_earlier_iterations_condensed_newest_first():
    text = loop._earlier_iterations(_goal(HIST), 4)
    lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
    # iteration 3 is the previous one: its findings reach the planner whole, so it is not repeated here
    assert lines == ["- Iteration 2: gate failed (score 50%); failing: check-2; finding of iteration 2",
                     "- Iteration 1: gate failed (score 25%); failing: check-1; finding of iteration 1"]
    assert loop._earlier_iterations(_goal(HIST), 2) == ""
    assert loop._earlier_iterations(_goal(HIST, {"hide_checks": True}), 4) == ""  # LE-021


def test_earlier_iterations_budget_and_setting():
    try:
        prefs.set_value("feedback_history_chars", 90)
        text = loop._earlier_iterations(_goal(HIST), 4)
        assert "Iteration 2" in text and "- Iteration 1" not in text and "1 earlier iteration(s) left out" in text
        prefs.set_value("feedback_history_chars", 0)
        assert loop._earlier_iterations(_goal(HIST), 4) == ""
    finally:
        prefs.set_value("feedback_history_chars", 2000)


def test_third_plan_carries_the_first_iteration_condensed(prompts):
    _failed_goal(3)
    assert "Earlier iterations, condensed" not in prompts[1]
    assert "Earlier iterations, condensed" in prompts[2] and "- Iteration 1: gate failed" in prompts[2]
