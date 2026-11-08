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


def gate_checks(tmp_path, change, tasks, iteration=1):
    """Gate `iteration` of a goal limited to stats.py; `tasks` are done (iteration, writes, acceptance path)."""
    wt, base = wt_with_change(tmp_path, BASE, change)
    with session() as s:
        goal = Goal(title="Guard mean", description="", worktree=str(wt), base_branch=base,
                    allowed_paths=["stats.py"], quality_commands=["true"], status="running", iteration=iteration)
        s.add(goal)
        s.flush()
        for n, (it, writes, path) in enumerate(tasks):
            s.add(Task(goal_id=goal.id, iteration=it, key=f"build{n}", title="Implement", role="developer",
                       writes=writes, status="done", output="done",
                       contract={"acceptance": [{"type": "file_exists", "path": path}]},
                       gate={"acceptance": [{"type": "file_exists", "passed": True, "detail": "exists"}]}))
        gid = goal.id
    return {c["check"]: c for c in quality.evaluate(gid, iteration)["checks"]}


def gate_scope(tmp_path, change, acceptance_path, writes):
    """Gate a goal limited to stats.py whose one done task asks for `acceptance_path` to exist."""
    return gate_checks(tmp_path, change, [(1, writes, acceptance_path)])["scope"]


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


def test_gate_exempts_a_worklog_from_an_earlier_iteration(tmp_path):
    # the scope check diffs base...HEAD, so iteration 1's WORKLOG.md is still in the change at iteration 2
    rows = gate_checks(tmp_path, CHANGE, [(1, True, "WORKLOG.md"), (2, True, "stats.py")], iteration=2)
    assert rows["scope"]["passed"], rows["scope"]["detail"]


def test_gate_exempts_a_nested_worklog_only_on_an_exact_match(tmp_path):
    nested = {"stats.py": CHANGE["stats.py"], "docs/WORKLOG.md": "Guarded mean.\n"}
    (tmp_path / "exact").mkdir()
    (tmp_path / "other").mkdir()
    exact = gate_checks(tmp_path / "exact", nested, [(1, True, "docs/WORKLOG.md")])["scope"]
    assert exact["passed"], exact["detail"]
    other = gate_checks(tmp_path / "other", nested, [(1, True, "WORKLOG.md")])["scope"]
    assert not other["passed"] and "docs/WORKLOG.md: outside the allowed paths" in other["detail"]


def test_gate_does_not_exempt_notes_md(tmp_path):
    row = gate_scope(tmp_path, {"stats.py": CHANGE["stats.py"], "NOTES.md": "Notes.\n"}, "NOTES.md", writes=True)
    assert not row["passed"] and "NOTES.md: outside the allowed paths" in row["detail"]


def test_exempt_worklog_still_fails_the_secret_scan(tmp_path):
    key = "AKIA" + "Q" * 16  # built at runtime so this file holds no credential-shaped literal
    rows = gate_checks(tmp_path, {"stats.py": CHANGE["stats.py"], "WORKLOG.md": f"Used {key} to test.\n"},
                       [(1, True, "WORKLOG.md")])
    assert rows["scope"]["passed"], rows["scope"]["detail"]
    assert not rows["secret scan"]["passed"] and "WORKLOG.md" in rows["secret scan"]["detail"]
