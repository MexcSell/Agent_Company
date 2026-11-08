"""A writing task's own WORKLOG.md is exempt from the goal's allowed paths; nothing else is (proposal #36)."""

from agent_company import checks, quality, worktrees
from agent_company.db import Goal, Task, session


def repo_with(tmp_path, files):
    repo = tmp_path / "proj"
    repo.mkdir()
    worktrees.git(repo, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    worktrees.git(repo, "add", ".")
    worktrees.git(repo, "commit", "-q", "-m", "init")
    return repo


def wt_with_change(tmp_path, base_files, change):
    repo = repo_with(tmp_path, base_files)
    wt, _, base = worktrees.create(repo, "t")
    worktrees.write_files(wt, change)
    worktrees.commit_all(wt, "change")
    return wt, base


BASE = {"stats.py": "def mean(xs):\n    return sum(xs) / len(xs)\n", "src/other.py": "X = 1\n"}
CHANGE = {"stats.py": "def mean(xs):\n    return sum(xs) / len(xs) if xs else 0.0\n", "WORKLOG.md": "Guarded mean.\n"}


def test_exempt_worklog_is_not_a_scope_violation(tmp_path):
    wt, base = wt_with_change(tmp_path, BASE, CHANGE)
    assert checks.scope_violations(wt, base, ["stats.py"], False, exempt=["WORKLOG.md"]) == []


def test_other_files_stay_outside_the_allowed_paths(tmp_path):
    wt, base = wt_with_change(tmp_path, BASE, CHANGE | {"other.py": "Y = 2\n"})
    problems = checks.scope_violations(wt, base, ["stats.py"], False, exempt=["WORKLOG.md"])
    assert len(problems) == 1 and problems[0].startswith("other.py: outside the allowed paths")


def test_worklog_without_exempt_is_reported(tmp_path):
    wt, base = wt_with_change(tmp_path, BASE, CHANGE)
    problems = checks.scope_violations(wt, base, ["stats.py"], False)
    assert problems == ["WORKLOG.md: outside the allowed paths (stats.py)"]


def gate_scope(tmp_path, change, acceptance_path, writes):
    """Gate a goal limited to stats.py whose one done task asks for `acceptance_path` to exist."""
    wt, base = wt_with_change(tmp_path, BASE, change)
    with session() as s:
        goal = Goal(title="Guard mean", description="", worktree=str(wt), base_branch=base,
                    allowed_paths=["stats.py"], quality_commands=["true"], status="running", iteration=1)
        s.add(goal)
        s.flush()
        s.add(Task(goal_id=goal.id, iteration=1, key="build", title="Implement", role="developer", writes=writes,
                   status="done", output="done",
                   contract={"acceptance": [{"type": "file_exists", "path": acceptance_path}]},
                   gate={"acceptance": [{"type": "file_exists", "passed": True, "detail": "exists"}]}))
        gid = goal.id
    result = quality.evaluate(gid, 1)
    rows = [c for c in result["checks"] if c["check"] == "scope"]
    assert len(rows) == 1, result["checks"]
    return rows[0]


def test_gate_exempts_the_writing_tasks_worklog(tmp_path):
    row = gate_scope(tmp_path, CHANGE, "WORKLOG.md", writes=True)
    assert row["passed"], row["detail"]


def test_gate_exempts_a_dot_slash_worklog_path(tmp_path):
    row = gate_scope(tmp_path, CHANGE, "./WORKLOG.md", writes=True)
    assert row["passed"], row["detail"]


def test_gate_does_not_exempt_other_file_exists_paths(tmp_path):
    row = gate_scope(tmp_path, {"stats.py": CHANGE["stats.py"], "src/other.py": "X = 2\n"}, "src/other.py",
                     writes=True)
    assert not row["passed"] and "src/other.py: outside the allowed paths" in row["detail"]


def test_gate_does_not_exempt_a_non_writing_tasks_worklog(tmp_path):
    row = gate_scope(tmp_path, CHANGE, "WORKLOG.md", writes=False)
    assert not row["passed"] and "WORKLOG.md: outside the allowed paths" in row["detail"]
