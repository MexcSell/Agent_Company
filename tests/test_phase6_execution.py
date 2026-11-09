"""Verification phase 6: a project's assignment, its task records and their state machine, offline. The live run on
this server (TEST-PROJECT-003, the simple calculator) is docs/verification/phase6_live.py.
Report: docs/verification/phase-6-execution.md."""
import json
import threading

import pytest
from sqlalchemy import select
from test_phase4_communication import DEV_REPLY, TASK, blocks, plan, pm_dev  # noqa: F401 - pm_dev is a fixture

from agent_company import loop, projects, workflow
from agent_company.db import Event, Task, session

REQUIREMENTS = ("Project: Simple Calculator\n\nRequirements:\n- Addition\n- Subtraction\n- Multiplication\n"
                "- Division\n- Division-by-zero handling")


def _tasks(gid):
    with session() as s:
        rows = s.scalars(select(Task).where(Task.goal_id == gid).order_by(Task.id)).all()
        for t in rows:
            s.expunge(t)
        return rows


def test_the_projects_registered_requirements_reach_the_planner(pm_dev):  # noqa: F811
    """V23: the project's description (where TEST-PROJECT-003's requirements are) never reached any agent."""
    pm, _ = pm_dev
    p = projects.create("TEST-PROJECT-003", description=REQUIREMENTS)
    gid = loop.create_goal("Develop a simple calculator application.", "Develop a simple calculator application.",
                           repo_path=p["path"], project_id=p["id"], max_iterations=1)
    loop.run_goal(gid)
    prompt = pm.calls[0]["messages"][-1]["content"]
    assert "Description (as registered): Project: Simple Calculator" in prompt and "Division-by-zero handling" in prompt


def test_a_task_record_has_its_id_project_agent_description_priority_estimate_and_times(pm_dev):  # noqa: F811
    pm, _ = pm_dev
    p = projects.create("TEST-PROJECT-003", description=REQUIREMENTS)
    pm.reply = plan({**TASK, "priority": 2, "estimated_minutes": 15})
    gid = loop.create_goal("calculator", "x", repo_path=p["path"], project_id=p["id"], max_iterations=1)
    loop.run_goal(gid)
    [t] = _tasks(gid)
    assert t.id and loop._goal(t.goal_id).project_id == p["id"]
    assert t.agent_id and t.planned_by and t.contract["description"] == TASK["description"]
    assert t.priority == 2 and t.contract["estimated_minutes"] == 15
    assert t.created_at and t.stage_log[0]["to"] == "planned", "created pending"


def test_the_task_lifecycle_of_a_passing_goal(pm_dev):  # noqa: F811
    gid = loop.create_goal("factorial", "x", max_iterations=1)
    loop.run_goal(gid)
    [t] = _tasks(gid)
    assert [e["to"] for e in t.stage_log] == ["planned", "assigned", "in_progress", "review", "approved"]
    assert loop._goal(gid).status == "approved" or t.stage == "approved"


def test_generated_code_alone_does_not_complete_a_task(pm_dev):  # noqa: F811
    _, dev = pm_dev
    dev.reply = blocks({"factorial.py": "def factorial(n):\n    return n\n", "test_factorial.py":
                        "from factorial import factorial\n\ndef test_five():\n    assert factorial(5) == 120\n"}) \
        + "Execution result: all tests pass"
    gid = loop.create_goal("factorial", "x", max_iterations=1)
    loop.run_goal(gid)
    [t] = _tasks(gid)
    assert [e["to"] for e in t.stage_log][-2:] == ["review", "failed"] and t.status == "failed"


def test_a_failed_task_is_retried_in_the_next_iteration_as_reopened(pm_dev, monkeypatch):  # noqa: F811
    _, dev = pm_dev
    replies = iter([blocks({"factorial.py": "def factorial(n):\n    return n\n"}) + "Execution result: done",
                    DEV_REPLY])
    real = loop._execute

    def execute(agent, req, purpose, **kw):
        if agent.role == "developer":
            dev.reply = next(replies)
        return real(agent, req, purpose, **kw)

    monkeypatch.setattr(loop, "_execute", execute)
    gid = loop.create_goal("factorial", "x", max_iterations=2)
    loop.run_goal(gid)
    first, second = _tasks(gid)
    assert first.stage == "failed" and second.stage_log[0] == {**second.stage_log[0], "to": "reopened"}
    assert [e["to"] for e in second.stage_log][1:] == ["assigned", "in_progress", "review", "approved"]


@pytest.mark.parametrize("cur,to", [("cancelled", "review"), ("completed", "failed"), ("failed", "approved"),
                                    ("approved", "review"), ("skipped", "approved")])
def test_an_illegal_task_move_is_refused_and_logged(cur, to, monkeypatch):
    monkeypatch.setattr(workflow, "STRICT_TASK_STAGES", False)
    gid = loop.create_goal("moves")
    with session() as s:
        t = Task(goal_id=gid, iteration=1, key="k", title="t", role="developer", stage=cur)
        s.add(t)
        s.flush()
        assert workflow.set_stage(t, to, "a late worker") is False
        assert t.stage == cur
    with session() as s:
        assert s.scalars(select(Event).where(Event.goal_id == gid, Event.message.like(f"%{cur} → {to}%"))).first()


def test_a_task_of_a_cancelled_goal_stays_cancelled_when_its_worker_finishes(pm_dev, monkeypatch):  # noqa: F811
    """V24: goal #111's task went cancelled → review → failed when its worker came back after the cancel."""
    monkeypatch.setattr(workflow, "STRICT_TASK_STAGES", False)
    _, dev = pm_dev
    gid = loop.create_goal("factorial", "x", max_iterations=1)
    started, release = threading.Event(), threading.Event()
    real = loop._execute

    def execute(agent, req, purpose, **kw):
        if agent.role == "developer":
            started.set()
            release.wait(10)
        return real(agent, req, purpose, **kw)

    monkeypatch.setattr(loop, "_execute", execute)
    th = threading.Thread(target=loop.run_goal, args=(gid,))
    th.start()
    assert started.wait(10)
    loop.cancel(gid, actor="test")
    release.set()
    th.join(30)
    [t] = _tasks(gid)
    stages = [e["to"] for e in t.stage_log]
    assert t.stage == "cancelled" and stages[-1] == "cancelled", stages
    assert json.dumps(t.gate) != "{}" or t.output, "the worker's result is still recorded"
