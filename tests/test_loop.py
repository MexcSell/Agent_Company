import subprocess

import pytest
from sqlalchemy import select

from agent_company import executors, loop, worktrees
from agent_company.db import Agent, Lesson, Run, Task, session


def test_goal_passes_and_merges_on_approval():
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval" and out["iterations"] == 1
    g = loop._goal(gid)
    assert "WORKLOG.md" in out["report"]["changed_files"]
    with session() as s:
        tasks = s.scalars(select(Task).where(Task.goal_id == gid)).all()
        assert {t.role for t in tasks} == {"architect", "developer", "qa"}
        assert all(t.status == "done" for t in tasks)
        assert s.scalars(select(Run)).first() is not None
        dev = s.scalars(select(Agent).where(Agent.role == "developer")).first()
        assert dev.xp > 0 and dev.tasks_done == 1 and dev.playbook.startswith("DO:")
    res = loop.approve(gid)
    log = subprocess.run(["git", "log", "--oneline", g.base_branch], cwd=g.repo_path, capture_output=True,
                         text=True).stdout
    assert res["merge_commit"] and "Merge agents/goal-" in log
    with pytest.raises(ValueError):
        loop.approve(gid)


def test_failing_gate_loops_with_feedback_then_fails():
    gid = loop.create_goal("Impossible", quality_commands=["false"], max_iterations=2)
    out = loop.run_goal(gid)
    assert out["status"] == "failed" and out["iterations"] == 2
    with session() as s:
        iters = {t.iteration for t in s.scalars(select(Task).where(Task.goal_id == gid))}
        assert iters == {1, 2}
        failures = s.scalars(select(Lesson).where(Lesson.outcome == "failure")).all()
        assert failures, "agents learn from the failed attempt"
        build2 = s.scalars(select(Task).where(Task.goal_id == gid, Task.iteration == 2,
                                              Task.key == "build")).first()
    # the second attempt was told why the first failed
    wt = loop._goal(gid).worktree
    assert "Command failed" in open(f"{wt}/WORKLOG.md").read()
    assert build2.status == "done"


def test_existing_repo_gets_its_own_worktree(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    worktrees.git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("x")
    worktrees.git(repo, "add", ".")
    worktrees.git(repo, "commit", "-q", "-m", "init")
    gid = loop.create_goal("Change it", repo_path=str(repo), quality_commands=[])
    loop.run_goal(gid)
    g = loop._goal(gid)
    assert g.worktree != str(repo) and (repo / "WORKLOG.md").exists() is False
    assert worktrees.git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_a_stale_folder_at_the_worktree_path_is_moved_aside(tmp_path):
    from agent_company.config import settings

    repo = tmp_path / "proj"
    repo.mkdir()
    worktrees.git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("x")
    worktrees.git(repo, "add", ".")
    worktrees.git(repo, "commit", "-q", "-m", "init")
    # What a test's background goal once left in data/worktrees: another repository's worktree under this name.
    stale = settings().worktrees_dir / "goal-1"
    stale.mkdir(parents=True)
    (stale / ".git").write_text("gitdir: /nowhere/.git/worktrees/goal-1\n")
    gid = loop.create_goal("Change it", repo_path=str(repo), quality_commands=["test -f a.txt"])
    assert gid == 1 and loop.run_goal(gid)["status"] == "awaiting_approval"
    assert worktrees.git(stale, "rev-parse", "--abbrev-ref", "HEAD") == "agents/goal-1"
    aside = [p for p in settings().worktrees_dir.iterdir() if p.name.startswith("goal-1.stale-")]
    assert len(aside) == 1 and (aside[0] / ".git").read_text().startswith("gitdir: /nowhere")
    # The goal's own worktree is resumed as it is, and a branch left by a removed worktree is checked out again.
    assert worktrees.create(repo, "goal-1")[0] == stale and (stale / "a.txt").exists()
    worktrees.remove(repo, stale)
    assert worktrees.create(repo, "goal-1")[0] == stale and (stale / "WORKLOG.md").exists()


def test_rejects_non_git_repo(tmp_path):
    with pytest.raises(ValueError):
        loop.create_goal("x", repo_path=str(tmp_path))


def test_file_blocks_cannot_escape_worktree(env):
    from agent_company.config import settings

    wt = settings().worktrees_dir / "w"
    wt.mkdir(parents=True)
    blocks = executors.parse_file_blocks("=== FILE: ../evil.txt ===\nx\n=== END FILE ===\n")
    with pytest.raises(worktrees.WorktreeError):
        worktrees.write_files(wt, blocks)
    with pytest.raises(worktrees.WorktreeError):
        worktrees.write_files(wt, {".git/config": "x"})
    with pytest.raises(worktrees.WorktreeError):
        worktrees.write_files(env, {"a": "b"})
    assert worktrees.write_files(wt, {"pkg/m.py": "print(1)\n"}) == ["pkg/m.py"]


def test_invalid_plan_falls_back_to_standard_plan(monkeypatch):
    monkeypatch.setattr(executors.OfflineExecutor, "_plan", lambda self, req: "not json")
    gid = loop.create_goal("Anything")
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval"


def test_generated_files_are_never_committed(env):
    from agent_company.config import settings

    repo = env / "r"
    repo.mkdir()
    worktrees.git(repo, "init", "-q", "-b", "main")
    (repo / "m.py").write_text("x = 1\n")
    worktrees.git(repo, "add", ".")
    worktrees.git(repo, "commit", "-q", "-m", "i")
    wt, _, base = worktrees.create(repo, "gen")
    (wt / "__pycache__").mkdir()
    (wt / "__pycache__" / "m.cpython-313.pyc").write_bytes(b"\x00")
    (wt / "pkg" / "node_modules").mkdir(parents=True)
    (wt / "pkg" / "node_modules" / "x.js").write_text("1")
    assert worktrees.commit_all(wt, "junk only") is None
    (wt / "m.py").write_text("x = 2\n")
    worktrees.commit_all(wt, "real change")
    assert worktrees.changed_files(wt, base) == ["m.py"]
    assert settings().worktrees_dir in wt.parents

