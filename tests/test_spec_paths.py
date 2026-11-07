"""The planner is told the goal's spec folder; a plan that names another `.claude/specs/` folder or a loop-written
file is rejected and goes back to the planner (K54)."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agent_company import executors, loop, specs
from agent_company.contracts import Condition, TaskContract
from agent_company.db import Event, Task, session

GOAL = SimpleNamespace(id=27, title="Planner uses the goal's spec folder in spec-task acceptance paths", worktree="/tmp/wt")
OWN = ".claude/specs/0027-planner-uses-the-goal-s-spec-folder-in-s"
OUTSIDE = f"outside this goal's spec folder {OWN}/; put spec documents there or in the source tree"
LOOP_MSG = "is written by the loop itself, not by a task (the gate writes decisions.md)"


def contract(*conds: Condition, key: str = "spec", writes: bool = False) -> TaskContract:
    return TaskContract(key=key, title="Write the spec", role="architect", description="Write the spec.",
                        writes=writes, acceptance=list(conds))


def rejected(cond: Condition, goal=GOAL) -> str:
    with pytest.raises(ValueError) as e:
        specs.check_plan(goal, [contract(cond)])
    return str(e.value)


def test_correct_path_unchanged():
    c = contract(Condition(type="file_exists", path=f"{OWN}/spec.md"),
                 Condition(type="command", run=f"grep -q x {OWN}/spec.md && ls {OWN}"))
    assert specs.check_plan(GOAL, [c]) == []
    assert c.acceptance[0].path == f"{OWN}/spec.md" and c.acceptance[1].run == f"grep -q x {OWN}/spec.md && ls {OWN}"


def test_non_spec_path_unchanged():
    c = contract(Condition(type="file_exists", path="tests/x.py"),
                 Condition(type="command", run="pytest -q tests/x.py"), writes=True)
    assert specs.check_plan(GOAL, [c]) == []
    assert c.acceptance[0].path == "tests/x.py" and c.acceptance[1].run == "pytest -q tests/x.py"


def test_foreign_folder_rejected_not_rewritten():
    c = contract(Condition(type="file_exists", path=".claude/specs/req-027-foo/notes.md"))
    with pytest.raises(ValueError) as e:
        specs.check_plan(GOAL, [c])
    assert str(e.value) == f"task spec: .claude/specs/req-027-foo/notes.md is {OUTSIDE}"
    assert c.acceptance[0].path == ".claude/specs/req-027-foo/notes.md"
    assert OUTSIDE in rejected(Condition(type="file_exists", path=".claude/specs/foo"))


@pytest.mark.parametrize("run", ["grep x .claude/specs/other/spec.md", "ls .claude/specs/foo",
                                 "test -d '.claude/specs/foo'", "cat .claude/specs/*/spec.md",
                                 f"cat {OWN}/spec.md .claude/specs/other/spec.md", "test -f .claude/specs/foo;"])
def test_foreign_folder_in_command_rejected(run):
    assert OUTSIDE in rejected(Condition(type="command", run=run))


def test_dot_slash_and_absolute_prefix_normalised():
    a = contract(Condition(type="file_exists", path=f"./{OWN}/design.md"), key="a")
    b = contract(Condition(type="file_exists", path=f"/tmp/wt/{OWN}/spec.md"), key="b")
    report = specs.check_plan(GOAL, [a, b])
    assert a.acceptance[0].path == f"{OWN}/design.md" and b.acceptance[0].path == f"{OWN}/spec.md"
    assert report == [f"a: ./{OWN}/design.md -> {OWN}/design.md", f"b: /tmp/wt/{OWN}/spec.md -> {OWN}/spec.md"]
    assert OUTSIDE in rejected(Condition(type="file_exists", path="./.claude/specs/other/design.md"))
    assert OUTSIDE in rejected(Condition(type="file_exists", path="/tmp/wt/.claude/specs/other/spec.md"))


def test_loop_file_rejected():
    assert f"review.md {LOOP_MSG}" in rejected(Condition(type="file_exists", path=".claude/specs/req-001-x/review.md"))
    assert LOOP_MSG in rejected(Condition(type="command", run="grep PASS .claude/specs/x/tasks.md"))
    assert LOOP_MSG in rejected(Condition(type="file_exists", path=f"{OWN}/requirements.md"))
    specs.check_plan(GOAL, [contract(Condition(type="file_exists", path=f"{OWN}/decisions-notes.md"))])


def test_decisions_condition_rejected():
    for cond in (Condition(type="file_exists", path=f"{OWN}/decisions.md"),
                 Condition(type="file_exists", path=".claude/specs/req-001-x/decisions.md"),
                 Condition(type="command", run=f"grep 'Iteration 1: PASS' {OWN}/decisions.md")):
        assert f"decisions.md {LOOP_MSG}" in rejected(cond), cond


def test_mismatched_slug_rejected():
    for path in (".claude/specs/0019-req-001-merge-approval-bound-to-the-revi/spec.md",
                 ".claude/specs/req-001-reviewed-commit/spec.md"):
        assert OUTSIDE in rejected(Condition(type="file_exists", path=path))
        assert OUTSIDE in rejected(Condition(type="command", run=f"grep -q legacy {path}"))
    g19 = SimpleNamespace(id=19, title="REQ-001: merge approval bound to the reviewed commit", worktree="/tmp/wt19")
    own19 = specs.relative_folder(g19)
    assert own19 == ".claude/specs/0019-req-001-merge-approval-bound-to-the-revi"
    ok = contract(Condition(type="file_exists", path=f"{own19}/spec.md"),
                  Condition(type="command", run=f"grep -q reviewed_sha {own19}/spec.md"))
    assert specs.check_plan(g19, [ok]) == []
    assert "outside this goal's spec folder" in rejected(
        Condition(type="file_exists", path=".claude/specs/req-001-reviewed-commit/spec.md"), g19)
    assert LOOP_MSG in rejected(Condition(type="command", run=f"grep 'Iteration 1: PASS' {own19}/decisions.md"), g19)


def test_all_problems_reported():
    a = contract(Condition(type="file_exists", path=".claude/specs/other/spec.md"), key="first")
    b = contract(Condition(type="command", run=f"grep PASS {OWN}/decisions.md"), key="second")
    with pytest.raises(ValueError) as e:
        specs.check_plan(GOAL, [a, b])
    lines = str(e.value).splitlines()
    assert len(lines) == 2 and lines[0].startswith("task first: ") and lines[1].startswith("task second: ")


def test_planner_note_names_folder():
    note = specs.planner_note(GOAL)
    assert f"`{OWN}/`" in note and note.endswith("\n")
    assert all(f in note for f in specs.LOOP_FILES)


def run_with_plans(monkeypatch, *answers):
    """Run a goal whose planner answers the standard plan with the first task's file_exists path set to
    answers[0], then answers[1], ... (each a function of the goal's own spec folder)."""
    prompts = []
    gid = loop.create_goal("Spec it", quality_commands=["test -f WORKLOG.md"])
    own = specs.relative_folder(loop._goal(gid))

    def plan(self, req):
        prompts.append(req.prompt)
        p = executors.standard_plan(req.data.get("goal", "g"))
        p["tasks"][0]["acceptance"] = [{"type": "file_exists", "path": answers[min(len(prompts), len(answers)) - 1](own)}]
        return json.dumps(p)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    loop.run_goal(gid)
    with session() as s:
        tasks = [(t.key, t.iteration, t.contract) for t in s.scalars(select(Task).where(Task.goal_id == gid))]
        events = [e.message for e in s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "plan"))]
    return own, prompts, tasks, events


def test_planner_gets_spec_path_error_and_replans(monkeypatch):
    own, prompts, tasks, events = run_with_plans(monkeypatch, lambda d: f"{d}/decisions.md", lambda d: f"{d}/spec.md")
    assert len(prompts) >= 2 and all(f"`{own}/`" in p for p in prompts)
    retry = prompts[1].split("Your previous answer was not a valid plan:", 1)[1]
    assert "written by the loop itself" in retry and own in retry
    first = [c for key, it, c in tasks if key == "spec" and it == 1]
    assert first and first[0]["acceptance"][0]["path"] == f"{own}/spec.md"
    assert not any("decisions.md" in json.dumps(c.get("acceptance")) for _, _, c in tasks)
    assert not any("using the standard plan" in m for m in events)


def test_repeated_bad_plan_falls_back(monkeypatch):
    own, prompts, tasks, events = run_with_plans(monkeypatch, lambda d: ".claude/specs/req-001-elsewhere/spec.md",
                                                 lambda d: ".claude/specs/0019-other/spec.md")
    assert any("using the standard plan" in m for m in events)
    assert tasks
    mine = own.rsplit("/", 1)[1]
    for _, _, c in tasks:
        text = json.dumps(c.get("acceptance"))
        assert all(x == mine for x in specs.SPEC_DIR.findall(text)), text


def test_relative_folder_matches_folder():
    assert specs.relative_folder(GOAL) == OWN
    assert str(specs.folder(GOAL)).endswith(specs.relative_folder(GOAL))
