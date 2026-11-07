"""A writing task that names a `BLOCKER:` and changed no file pauses the goal for a person (S02-R-02): its review
does not run, the gate does not judge the empty iteration and no new iteration starts."""

from pathlib import Path

import pytest
from sqlalchemy import select

from agent_company import executors, loop, quality, router
from agent_company.contracts import TaskContract
from agent_company.db import Notification, Task, session

BLOCKER = "BLOCKER: missing credentials"


@pytest.fixture
def goal_loop(monkeypatch):
    """A developer task then a QA review of it; `_execute` answers with `outputs[key]` and writes `writes[key]`."""
    plan = [TaskContract(key="implement", title="implement", role="developer", description="Build it.", writes=True),
            TaskContract(key="review", title="review", role="qa", description="Review it.", depends_on=["implement"])]
    monkeypatch.setattr(loop, "_plan", lambda goal, it, fb, notes: ([p.model_copy() for p in plan], None))
    monkeypatch.setattr(loop, "enqueue", lambda goal_id: None)
    state = {"outputs": {"implement": BLOCKER, "review": "The change does what the task asks.\nVERDICT: PASS"}, "writes": {}, "ran": [], "evaluated": []}

    real = loop._execute

    def execute(agent, req, purpose, task_id=None, **kw):
        if purpose != "task":  # lessons and summaries: the offline simulator
            return real(agent, req, purpose, task_id=task_id, **kw)
        key = req.data["title"]
        state["ran"].append(key)
        for name, text in state["writes"].get(key, {}).items():
            Path(req.workdir, name).write_text(text)
        return (executors.Result(text=state["outputs"][key]),
                router.Choice(name="offline", kind="offline", model_id="offline", reason="test"))

    def evaluate(goal_id, iteration):
        state["evaluated"].append(iteration)
        return {"passed": False, "checks": [], "summary": "stop here", "feedback": "", "commit": ""}

    monkeypatch.setattr(loop, "_execute", execute)
    monkeypatch.setattr(quality, "evaluate", evaluate)
    return state


def _tasks(gid):
    with session() as s:
        return {t.key: (t.status, t.output or "", t.iteration, t.stage)
                for t in s.scalars(select(Task).where(Task.goal_id == gid))}


def test_a_blocked_build_with_no_change_pauses_the_goal(goal_loop):
    gid = loop.create_goal("Add a feature", quality_commands=[])
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert out["status"] == "paused" and g.status == "paused", out
    assert out["blocker"] == "missing credentials"
    assert g.checkpoint["paused_for"] == "blocker" and g.checkpoint["paused_why"] == "missing credentials"
    status, output, iteration, stage = _tasks(gid)["implement"]
    assert status == "skipped" and stage == "blocked" and output == loop.BLOCKER_SKIP + "missing credentials"
    assert iteration == 1 and g.iteration == 1 and not g.history
    assert goal_loop["ran"] == ["implement"] and _tasks(gid)["review"][0] != "done"  # the review did not run
    assert goal_loop["evaluated"] == []  # the gate did not judge the empty iteration
    with session() as s:
        titles = [n.title for n in s.scalars(select(Notification).where(Notification.goal_id == gid))]
    assert f"Goal #{gid} paused: a task is blocked" in titles


def test_after_the_blocker_is_cleared_the_same_iteration_continues(goal_loop):
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "paused"
    goal_loop["outputs"]["implement"] = "Done: the feature is in feature.py."
    goal_loop["writes"]["implement"] = {"feature.py": "x = 1\n"}
    loop.resume(gid, "user:admin")
    loop.run_goal(gid)
    tasks = _tasks(gid)
    assert tasks["implement"][:3] == ("done", "Done: the feature is in feature.py.", 1) and tasks["review"][0] == "done"
    assert goal_loop["evaluated"] == [1] and loop._goal(gid).iteration == 1
    assert loop._goal(gid).checkpoint.get("paused_for") != "blocker"


def test_a_blocker_with_a_changed_file_does_not_pause(goal_loop):
    goal_loop["writes"]["implement"] = {"feature.py": "x = 1\n"}
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] != "paused" and "blocker" not in out
    assert (loop._goal(gid).checkpoint or {}).get("paused_for") != "blocker"
    tasks = _tasks(gid)
    assert tasks["implement"][0] == "done" and not tasks["implement"][1].startswith(loop.BLOCKER_SKIP)
    assert goal_loop["ran"] == ["implement", "review"] and goal_loop["evaluated"] == [1]


def test_an_empty_diff_without_a_blocker_line_does_not_pause(goal_loop):
    goal_loop["outputs"]["implement"] = "Nothing needed a change in this repository."
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] != "paused" and "blocker" not in out
    assert (loop._goal(gid).checkpoint or {}).get("paused_for") != "blocker"
    assert _tasks(gid)["implement"][0] == "done"
    assert goal_loop["ran"] == ["implement", "review"] and goal_loop["evaluated"] == [1]


def test_a_read_only_task_with_a_blocker_line_does_not_pause(goal_loop, monkeypatch):
    plan = [TaskContract(key="review", title="review", role="qa", description="Review it.")]
    monkeypatch.setattr(loop, "_plan", lambda goal, it, fb, notes: ([p.model_copy() for p in plan], None))
    goal_loop["outputs"]["review"] = BLOCKER
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    assert loop.run_goal(gid)["status"] != "paused"
    assert _tasks(gid)["review"][0] == "done" and goal_loop["evaluated"] == [1]


def test_a_benchmark_goal_fails_instead_of_pausing(goal_loop):
    gid = loop.create_goal("Add a feature", quality_commands=[], origin="eval")
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert out["status"] == "failed" and g.status == "failed" and out["blocker"] == "missing credentials"
    assert g.result == "Stopped: blocker: missing credentials."
    assert (g.checkpoint or {}).get("paused_for") != "blocker"
    assert goal_loop["ran"] == ["implement"] and goal_loop["evaluated"] == [] and g.iteration == 1
