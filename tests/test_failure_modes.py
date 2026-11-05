"""Failure modes (docs/requirements/guides-and-other-sources.md G-R-03): executor timeout, provider rate limit,
worker crash mid-goal, malformed planner JSON, failing dependency task, merge rollback. The rate limit, crash and
malformed-plan cases are in test_safety.py and test_loop.py; the rest are here."""

import pytest
from sqlalchemy import select

from agent_company import executors, loop, router, worktrees
from agent_company.db import Goal, LiveProcess, Task, session


def test_an_executor_that_runs_too_long_is_killed_and_reported(env):
    from agent_company.config import settings

    wt = settings().worktrees_dir / "slow"
    wt.mkdir(parents=True)

    class Slow(executors.Executor):
        kind = "claude_code"

        def _run(self, req):
            proc = self._spawn(["sleep", "20"], req, wt)
            return executors.Result(text=proc.stdout)

    res = Slow(router.Choice("x", "claude_code", "", "t"), timeout=1).run(
        executors.Request(purpose="task", role="developer", system="", prompt=""))
    assert not res.ok and "TimeoutExpired" in res.error and res.duration < 10
    assert loop.transient(res.error), "a timeout is retried like other transient errors"
    with session() as s:
        assert s.scalars(select(LiveProcess)).first() is None


def test_a_failing_dependency_skips_its_dependents_and_fails_the_gate(monkeypatch):
    real = executors.OfflineExecutor._task
    monkeypatch.setattr(executors.OfflineExecutor, "_task",
                        lambda self, req: "too short" if req.role == "architect" else real(self, req))
    gid = loop.create_goal("Broken spec", max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] == "failed"
    with session() as s:
        status = {t.key: t.status for t in s.scalars(select(Task).where(Task.goal_id == gid))}
    assert status == {"spec": "failed", "build": "skipped", "review": "skipped"}
    assert "Task 'Write the specification' (architect) failed" in out["report"]["feedback"]


def repo(env):
    r = env / "conflict"
    r.mkdir()
    worktrees.git(r, "init", "-q", "-b", "main")
    (r / "WORKLOG.md").write_text("original\n")
    worktrees.git(r, "add", ".")
    worktrees.git(r, "commit", "-q", "-m", "init")
    return r


def test_a_conflicting_merge_is_rolled_back_and_the_goal_waits(env):
    r = repo(env)
    gid = loop.create_goal("Conflict", repo_path=str(r), quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    (r / "WORKLOG.md").write_text("someone else's change\n")  # the base moved on meanwhile
    worktrees.git(r, "commit", "-q", "-am", "concurrent change")
    head = worktrees.git(r, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="rolled back"):
        loop.approve(gid)
    assert worktrees.git(r, "rev-parse", "HEAD") == head
    assert worktrees.git(r, "status", "--porcelain") == "" and not (r / ".git" / "MERGE_HEAD").exists()
    assert (r / "WORKLOG.md").read_text() == "someone else's change\n"
    with session() as s:
        assert s.get(Goal, gid).status == "awaiting_approval"


def test_an_automatic_merge_that_conflicts_leaves_the_goal_for_a_person(env, monkeypatch):
    from agent_company import config

    r = repo(env)
    config.settings().require_approval = False
    real = loop._merge

    def merge_after_a_concurrent_commit(goal_id):
        (r / "WORKLOG.md").write_text("concurrent\n")
        worktrees.git(r, "commit", "-q", "-am", "concurrent")
        return real(goal_id)

    monkeypatch.setattr(loop, "_merge", merge_after_a_concurrent_commit)
    gid = loop.create_goal("Auto merge", repo_path=str(r), quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval"
    with session() as s:
        assert "automatic merge was refused" in s.get(Goal, gid).result
    assert not (r / ".git" / "MERGE_HEAD").exists()
