"""A plan's file_exists acceptance outside the goal's allowed paths can never pass: rejected at plan time, dropped
from the fallback plan."""

from types import SimpleNamespace

import pytest

from agent_company import specs
from agent_company.contracts import Condition, TaskContract

TITLE = "Check plan acceptance against allowed paths"
OWN = specs.relative_folder(SimpleNamespace(id=110, title=TITLE))


def goal(tmp_path, allowed=("src/*",)):
    return SimpleNamespace(id=110, title=TITLE, worktree=str(tmp_path), allowed_paths=list(allowed))


def contract(*conds: Condition, key: str = "implement") -> TaskContract:
    return TaskContract(key=key, title="Do it", role="developer", description="Do it.", writes=True,
                        acceptance=list(conds))


def exists(path: str) -> Condition:
    return Condition(type="file_exists", path=path)


def test_allowed_file_passes(tmp_path):
    c = contract(exists("src/x.py"))
    assert specs.check_plan(goal(tmp_path), [c]) == []
    assert c.acceptance[0].path == "src/x.py"


@pytest.mark.parametrize("pattern", ["src/", "src"])
def test_directory_pattern_passes(tmp_path, pattern):
    c = contract(exists("src/a/b.py"))
    assert specs.check_plan(goal(tmp_path, [pattern]), [c]) == []


def test_disallowed_new_file_rejected(tmp_path):
    with pytest.raises(ValueError) as e:
        specs.check_plan(goal(tmp_path), [contract(exists("docs/x.md"))])
    msg = str(e.value)
    assert "task implement: docs/x.md" in msg and "src/*" in msg
    assert "contains" in msg and "command" in msg


def test_existing_file_passes(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("x\n")
    c = contract(exists("docs/x.md"))
    assert specs.check_plan(goal(tmp_path), [c]) == []
    assert len(c.acceptance) == 1


def test_scope_gate_exemptions_pass(tmp_path):
    c = contract(exists(f"{OWN}/answer.md"), exists(".claude/agents/x.md"), exists(".mcp.json"),
                 exists("docs/adr/0001-x.md"))
    assert specs.check_plan(goal(tmp_path), [c]) == []
    assert len(c.acceptance) == 4


def test_foreign_spec_folder_rewritten_then_exempt(tmp_path):
    c = contract(exists(".claude/specs/other/notes.md"))
    assert specs.check_plan(goal(tmp_path), [c]) == [f"implement: .claude/specs/other/notes.md -> {OWN}/notes.md"]


def test_no_allowed_paths_passes(tmp_path):
    assert specs.check_plan(goal(tmp_path, []), [contract(exists("docs/x.md"))]) == []
    bare = SimpleNamespace(id=110, title=TITLE, worktree=str(tmp_path))  # no allowed_paths attribute
    assert specs.check_plan(bare, [contract(exists("docs/x.md"))]) == []


def test_prefixes_normalised(tmp_path):
    ok = contract(exists("./src/x.py"), exists(f"{tmp_path}/src/y.py"))
    specs.check_plan(goal(tmp_path), [ok])
    with pytest.raises(ValueError) as e:
        specs.check_plan(goal(tmp_path), [contract(exists(f"{tmp_path}/docs/y.md"), exists("././docs/z.md"))])
    assert "task implement: docs/y.md" in str(e.value) and "task implement: docs/z.md" in str(e.value)
    assert str(tmp_path) not in str(e.value).split("Allowed paths")[0]


def test_all_violations_named_in_one_error(tmp_path):
    a = contract(exists("docs/a.md"), key="first")
    b = contract(exists("src/ok.py"), exists("README.md"), key="second")
    with pytest.raises(ValueError) as e:
        specs.check_plan(goal(tmp_path), [a, b])
    msg = str(e.value)
    assert "task first: docs/a.md" in msg and "task second: README.md" in msg and "src/ok.py" not in msg


def test_drop_mode_removes_and_reports(tmp_path):
    keep = exists("src/ok.py")
    c = contract(keep, exists("WORKLOG.md"))
    report = specs.check_plan(goal(tmp_path), [c], drop_out_of_scope=True)
    assert report == ["implement: dropped file_exists WORKLOG.md (outside the allowed paths)"]
    assert c.acceptance == [keep]


def test_drop_mode_keeps_rewrite_report(tmp_path):
    c = contract(exists(".claude/specs/other/notes.md"), exists("docs/x.md"))
    report = specs.check_plan(goal(tmp_path), [c], drop_out_of_scope=True)
    assert report == [f"implement: .claude/specs/other/notes.md -> {OWN}/notes.md",
                      "implement: dropped file_exists docs/x.md (outside the allowed paths)"]
    assert [a.path for a in c.acceptance] == [f"{OWN}/notes.md"]


def test_loop_file_still_rejected(tmp_path):
    with pytest.raises(ValueError, match="written by the loop itself"):
        specs.check_plan(goal(tmp_path), [contract(exists(f"{OWN}/decisions.md"))])
    with pytest.raises(ValueError, match="written by the loop itself"):
        specs.check_plan(goal(tmp_path), [contract(exists(f"{OWN}/decisions.md"))], drop_out_of_scope=True)


def test_fallback_plan_drops_worklog_outside_scope(monkeypatch):
    """The loop's standard plan names WORKLOG.md; with allowed paths elsewhere it is dropped, never raised."""
    from sqlalchemy import select

    from agent_company import executors, loop
    from agent_company.db import Event, Task, session

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", lambda self, req: "not json")
    gid = loop.create_goal("Scoped fallback", quality_commands=["true"], allowed_paths=["src/*"])
    loop.run_goal(gid)
    with session() as s:  # iteration 1 only: later ones find the WORKLOG.md the offline developer wrote
        tasks = s.scalars(select(Task).where(Task.goal_id == gid, Task.iteration == 1)).all()
        events = [e.message for e in s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "plan"))]
    assert tasks
    assert not any(a.get("path") == "WORKLOG.md" for t in tasks for a in t.contract.get("acceptance", []))
    assert any(m.startswith("Standard plan corrected: ") and "dropped file_exists WORKLOG.md" in m for m in events)
