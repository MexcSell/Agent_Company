"""A finished task's answer becomes the document its acceptance names (specs.save_answer): in the goal's own spec
folder also over a stale file or where a writing run made none; never a loop file, another goal's folder or .git."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agent_company import executors, loop, specs, worktrees
from agent_company.contracts import Condition, TaskContract
from agent_company.db import Event, Task, session

ANSWER = "# Spec\n\nThe answer."


@pytest.fixture
def goal(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    return SimpleNamespace(id=33, title="Let specs.py write role-provided spec files", worktree=str(wt))


def own(goal) -> str:
    return specs.relative_folder(goal)


def contract(*paths: str, writes: bool = False) -> TaskContract:
    return TaskContract(key="spec", title="Write the spec", role="architect", description="Write the spec.",
                        writes=writes, acceptance=[Condition(type="file_exists", path=p) for p in paths])


def save(goal, *paths, text=ANSWER, writes=False, changed=()):
    return specs.save_answer(goal, contract(*paths, writes=writes), text, writes=writes, changed=list(changed))


def read(goal, rel) -> str:
    return (specs.Path(goal.worktree) / rel).read_text(encoding="utf-8")


def test_read_only_answer_written(goal):
    path = f"{own(goal)}/spec.md"
    assert save(goal, path) == [path]
    assert read(goal, path) == ANSWER + "\n"


def test_writing_task_without_the_file_gets_it(goal):
    path = f"{own(goal)}/notes.md"
    assert save(goal, path, writes=True, changed=["src/x.py"]) == [path]
    assert read(goal, path) == ANSWER + "\n"


def test_stale_file_overwritten_by_read_only_task(goal):
    path = f"{own(goal)}/spec.md"
    save(goal, path, text="iteration 1")
    assert save(goal, path, text="iteration 2") == [path]
    assert read(goal, path) == "iteration 2\n"


def test_writing_task_keeps_the_file_its_run_made(goal):
    path = f"{own(goal)}/spec.md"
    specs._write(specs.Path(goal.worktree) / path, "the run's own spec")
    assert save(goal, path, writes=True, changed=[path]) == []
    assert read(goal, path) == "the run's own spec\n"


def test_writing_task_overwrites_stale_file_it_did_not_touch(goal):
    path = f"{own(goal)}/spec.md"
    specs._write(specs.Path(goal.worktree) / path, "iteration 1")
    assert save(goal, path, writes=True, changed=["src/x.py"]) == [path]
    assert read(goal, path) == ANSWER + "\n"


@pytest.mark.parametrize("name", specs.LOOP_FILES)
@pytest.mark.parametrize("writes", [False, True])
def test_loop_files_refused(goal, name, writes):
    path = f"{own(goal)}/{name}"
    assert save(goal, path, writes=writes) == []
    assert not (specs.Path(goal.worktree) / path).exists()


@pytest.mark.parametrize("writes", [False, True])
def test_other_goal_folder_refused(goal, writes):
    assert save(goal, ".claude/specs/other-goal/spec.md", writes=writes) == []
    assert not (specs.Path(goal.worktree) / ".claude/specs").exists()


@pytest.mark.parametrize("writes", [False, True])
def test_escapes_refused(goal, writes):
    outside = specs.Path(goal.worktree).parent
    assert save(goal, f"{own(goal)}/../../../../x.md", "../outside.md", writes=writes) == []
    assert not (outside / "x.md").exists() and not (outside / "outside.md").exists()


def test_git_refused(goal):
    assert save(goal, ".git/x.md") == []
    assert not (specs.Path(goal.worktree) / ".git").exists()


def test_symlink_outside_not_followed(goal, tmp_path):
    away = tmp_path / "away"
    away.mkdir()
    (specs.Path(goal.worktree) / "docs").symlink_to(away)
    assert save(goal, "docs/x.md") == []
    assert list(away.iterdir()) == []


@pytest.mark.parametrize("text", ["", "  \n"])
def test_empty_answer_writes_nothing(goal, text):
    assert save(goal, f"{own(goal)}/spec.md", "docs/x.md", text=text) == []
    assert list(specs.Path(goal.worktree).iterdir()) == []


def test_non_document_ignored(goal):
    assert save(goal, f"{own(goal)}/check.py", "src/x.py") == []
    assert list(specs.Path(goal.worktree).iterdir()) == []


def test_paths_normalised(goal):
    path = f"{own(goal)}/spec.md"
    assert save(goal, f"./{path}") == [path]
    assert save(goal, f"{goal.worktree}/{path}", text="again") == [path]
    assert read(goal, path) == "again\n"


def test_outside_spec_folder_written_once_by_read_only_task(goal):
    assert save(goal, "docs/x.md", writes=True) == []
    assert not (specs.Path(goal.worktree) / "docs/x.md").exists()
    assert save(goal, "docs/x.md", text="first") == ["docs/x.md"]
    assert save(goal, "docs/x.md", text="second") == []
    assert save(goal, "docs/x.md", text="third", writes=True) == []
    assert read(goal, "docs/x.md") == "first\n"


def test_answer_files_shared():
    assert loop.ANSWER_FILES is specs.ANSWER_FILES


def test_unwritable_target_logged(goal):
    goal.id = loop.create_goal(goal.title)  # the error event names a goal that exists
    path = f"{own(goal)}/spec.md"
    (specs.Path(goal.worktree) / path).mkdir(parents=True)
    assert save(goal, path) == []
    with session() as s:
        errors = [e.message for e in s.scalars(select(Event).where(Event.kind == "error"))]
    assert any(path in m for m in errors)


@pytest.mark.parametrize("name", ["spec.md", "notes.md"])
def test_writing_task_with_unknown_changes_keeps_out_of_its_folder(goal, name):
    path = f"{own(goal)}/{name}"
    specs._write(specs.Path(goal.worktree) / path, "maybe the run's own")
    assert specs.save_answer(goal, contract(path, f"{own(goal)}/new.md", writes=True), ANSWER, writes=True,
                             changed=None) == []
    assert read(goal, path) == "maybe the run's own\n"
    assert not (specs.Path(goal.worktree) / own(goal) / "new.md").exists()


def run_with(monkeypatch, key: str, rel: str, run_writes: str = "") -> int:
    """Run a goal offline whose task `key` names `<own folder>/rel` in its acceptance; a writing run also writes
    `run_writes` itself there."""
    gid = loop.create_goal("Spec it", quality_commands=["test -f WORKLOG.md"])
    path = f"{specs.relative_folder(loop._goal(gid))}/{rel}"
    task = executors.OfflineExecutor._task

    def plan(self, req):
        p = executors.standard_plan(req.data.get("goal", "g"))
        next(t for t in p["tasks"] if t["key"] == key)["acceptance"].append({"type": "file_exists", "path": path})
        return json.dumps(p)

    def run(self, req):
        out = task(self, req)
        return out + f"\n=== FILE: {path} ===\n{run_writes}\n=== END FILE ===\n" if req.writes and run_writes else out

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    monkeypatch.setattr(executors.OfflineExecutor, "_task", run)
    loop.run_goal(gid)
    return gid


def gate_of(gid: int, key: str) -> tuple[str, dict]:
    with session() as s:
        t = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == key, Task.iteration == 1)).one()
        return t.status, t.gate


def test_writing_run_keeps_its_own_non_ascii_file(monkeypatch):
    gid = run_with(monkeypatch, "build", "über.md", run_writes="the run's own über")
    wt, path = loop._goal(gid).worktree, f"{specs.relative_folder(loop._goal(gid))}/über.md"
    status, gate = gate_of(gid, "build")
    assert status == "done" and gate["saved_answer"] == []
    assert path in gate["files"]  # unquoted from git: core.quotepath=off
    assert worktrees.git(wt, "show", f"{gate['commit']}:{path}") == "the run's own über"


def test_read_only_answer_committed_on_its_own(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    checkpoint = loop._checkpoint

    def spy(goal_id, iteration, key, paths=None):
        commit, changed = checkpoint(goal_id, iteration, key, paths)
        calls.append((key, commit, (loop._goal(goal_id).checkpoint or {}).get("commit")))
        return commit, changed

    monkeypatch.setattr(loop, "_checkpoint", spy)
    gid = run_with(monkeypatch, "spec", "answer.md")
    wt, path = loop._goal(gid).worktree, f"{specs.relative_folder(loop._goal(gid))}/answer.md"
    status, gate = gate_of(gid, "spec")
    assert status == "done" and gate["saved_answer"] == [path]
    commit = gate["commit"]
    assert worktrees.git(wt, "log", "-1", "--format=%s", commit) == f"Goal {gid} iteration 1: spec answer"
    assert worktrees.git(wt, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines() == [path]
    assert ("spec answer", commit, commit) in calls  # the goal's checkpoint points at the answer's commit
    status, build = gate_of(gid, "build")
    assert status == "done" and path not in build["files"] and "WORKLOG.md" in build["files"]
    assert worktrees.git(wt, "rev-parse", f"{build['commit']}^") == commit


def test_saved_spec_md_kept_by_write_outputs(monkeypatch):
    gid = run_with(monkeypatch, "review", "spec.md")
    wt, path = loop._goal(gid).worktree, f"{specs.relative_folder(loop._goal(gid))}/spec.md"
    status, gate = gate_of(gid, "review")
    assert status == "done" and gate["saved_answer"] == [path]
    assert gate_of(gid, "spec")[0] == "done"  # the architect's 'spec' task ran in the same iteration
    text = read(SimpleNamespace(worktree=wt), path)
    assert "VERDICT: PASS" in text and "# Specification:" not in text
    assert worktrees.git(wt, "show", f"HEAD:{path}") == text.rstrip("\n")


def test_writing_task_answer_committed_by_the_loop(monkeypatch):
    gid = loop.create_goal("Spec it", quality_commands=["test -f WORKLOG.md"])
    path = f"{specs.relative_folder(loop._goal(gid))}/notes.md"

    def plan(self, req):
        p = executors.standard_plan(req.data.get("goal", "g"))
        p["tasks"][1]["acceptance"].append({"type": "file_exists", "path": path})
        return json.dumps(p)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    loop.run_goal(gid)
    wt = loop._goal(gid).worktree
    with session() as s:
        build = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == "build", Task.iteration == 1)).one()
        status, gate = build.status, build.gate
    assert status == "done"
    commit = gate["commit"]
    assert worktrees.git(wt, "log", "-1", "--format=%s", commit) == f"Goal {gid} iteration 1: build answer"
    assert worktrees.git(wt, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines() == [path]
    assert path not in gate["files"] and "WORKLOG.md" in gate["files"]
    assert "Implemented" in worktrees.git(wt, "show", f"{commit}:{path}")
