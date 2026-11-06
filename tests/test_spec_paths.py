"""The planner is told the goal's spec folder; plans that name another `.claude/specs/` folder are corrected (K54)."""

from types import SimpleNamespace

import pytest

from agent_company import specs
from agent_company.contracts import Condition, TaskContract

GOAL = SimpleNamespace(id=27, title="Planner uses the goal's spec folder in spec-task acceptance paths", worktree="/tmp/wt")
OWN = ".claude/specs/0027-planner-uses-the-goal-s-spec-folder-in-s"


def contract(*conds: Condition, key: str = "spec", writes: bool = False) -> TaskContract:
    return TaskContract(key=key, title="Write the spec", role="architect", description="Write the spec.",
                        writes=writes, acceptance=list(conds))


def test_foreign_folder_rewritten():
    c = contract(Condition(type="file_exists", path=".claude/specs/req-027-foo/notes.md"))
    report = specs.check_plan(GOAL, [c])
    assert c.acceptance[0].path == f"{OWN}/notes.md"
    assert report == [f"spec: .claude/specs/req-027-foo/notes.md -> {OWN}/notes.md"]


def test_correct_path_unchanged():
    c = contract(Condition(type="file_exists", path=f"{OWN}/spec.md"))
    assert specs.check_plan(GOAL, [c]) == []
    assert c.acceptance[0].path == f"{OWN}/spec.md"


def test_non_spec_path_unchanged():
    c = contract(Condition(type="file_exists", path="tests/x.py"),
                 Condition(type="command", run="pytest -q tests/x.py"), writes=True)
    assert specs.check_plan(GOAL, [c]) == []
    assert c.acceptance[0].path == "tests/x.py" and c.acceptance[1].run == "pytest -q tests/x.py"


def test_dot_slash_and_absolute_prefix_normalised():
    a = contract(Condition(type="file_exists", path="./.claude/specs/other/design.md"), key="a")
    b = contract(Condition(type="file_exists", path="/tmp/wt/.claude/specs/other/spec.md"), key="b")
    report = specs.check_plan(GOAL, [a, b])
    assert a.acceptance[0].path == f"{OWN}/design.md"
    assert b.acceptance[0].path == f"{OWN}/spec.md"
    assert len(report) == 2 and report[0].startswith("a: ./.claude/specs/other/design.md -> ")


def test_command_rewritten():
    c = contract(Condition(type="command", run="grep x .claude/specs/other/spec.md"))
    report = specs.check_plan(GOAL, [c])
    assert c.acceptance[0].run == f"grep x {OWN}/spec.md"
    assert report == [f"spec: grep x .claude/specs/other/spec.md -> grep x {OWN}/spec.md"]


def test_loop_file_rejected():
    with pytest.raises(ValueError, match="written by the loop itself"):
        specs.check_plan(GOAL, [contract(Condition(type="file_exists", path=f"{OWN}/decisions.md"))])
    with pytest.raises(ValueError, match="review.md is written by the loop itself"):
        specs.check_plan(GOAL, [contract(Condition(type="file_exists", path=".claude/specs/req-001-x/review.md"))])
    with pytest.raises(ValueError):
        specs.check_plan(GOAL, [contract(Condition(type="command", run="grep PASS .claude/specs/x/tasks.md"))])
    specs.check_plan(GOAL, [contract(Condition(type="file_exists", path=f"{OWN}/decisions-notes.md"))])


def test_planner_note_names_folder():
    note = specs.planner_note(GOAL)
    assert f"`{OWN}/`" in note and note.endswith("\n")
    assert all(f in note for f in specs.LOOP_FILES)


def test_relative_folder_matches_folder():
    assert specs.relative_folder(GOAL) == OWN
    assert str(specs.folder(GOAL)).endswith(specs.relative_folder(GOAL))
