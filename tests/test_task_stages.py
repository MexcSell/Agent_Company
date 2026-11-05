"""ASEAO05 S05-R-08: the task lifecycle stage beside its execution status; blockers from task output."""

from sqlalchemy import select

from agent_company import contracts, loop, management, projects, workflow
from agent_company.db import Goal, Risk, Task, session
from agent_company.scheduler import set_status as set_task_status


def _contract(key):
    return contracts.validate({"key": key, "title": key, "role": "developer", "description": "work"})


def test_a_run_moves_tasks_through_the_chain_and_an_approved_goal_completes_them():
    p = projects.create("Stages")
    gid = loop.create_goal("Write a summary", project_id=p["id"])
    loop.run_goal(gid)
    with session() as s:
        g = s.get(Goal, gid)
        tasks = s.scalars(select(Task).where(Task.goal_id == gid, Task.iteration == g.iteration)).all()
        assert tasks
        for t in tasks:
            chain = [x["to"] for x in t.stage_log]
            assert chain[:5] == ["planned", "assigned", "in_progress", "review", "approved"], chain
        status = g.status
    if status == "awaiting_approval":
        loop.approve(gid, "admin")
    with session() as s:
        assert {workflow.task_stage(t) for t in s.scalars(select(Task).where(
            Task.goal_id == gid, Task.iteration == s.get(Goal, gid).iteration))} == {"completed"}


def test_goal_states_move_waiting_tasks_and_a_resume_reopens_them():
    gid = loop.create_goal("Paused work")
    a, b = loop._persist(gid, 1, [_contract("a"), _contract("b")])
    set_task_status(a, "running")
    with session() as s:
        s.get(Goal, gid).iteration = 1
        workflow.set_status(s, s.get(Goal, gid), "running", "test")
        workflow.set_status(s, s.get(Goal, gid), "paused", "test")
        assert s.get(Task, b).stage == "blocked" and s.get(Task, a).stage == "in_progress"
    loop._requeue_unfinished(gid, 1)
    with session() as s:
        assert (s.get(Task, a).stage, s.get(Task, b).stage) == ("reopened", "planned")
        workflow.set_status(s, s.get(Goal, gid), "queued", "resumed")
        workflow.set_status(s, s.get(Goal, gid), "cancelled", "test")
        assert {s.get(Task, a).stage, s.get(Task, b).stage} == {"cancelled"}

    # a failed or stopped task planned again in the next iteration is reopened
    set_task_status(a, "failed", output="ERROR: broke")
    again, fresh = loop._persist(gid, 2, [_contract("a"), _contract("c")])
    with session() as s:
        assert (s.get(Task, again).stage, s.get(Task, fresh).stage) == ("reopened", "planned")


def test_skips_map_to_their_stage_and_old_rows_read_as_their_status():
    gid = loop.create_goal("Skips")
    a, b, c = loop._persist(gid, 1, [_contract("a"), _contract("b"), _contract("c")])
    set_task_status(a, "skipped", output="skipped: a dependency failed")
    set_task_status(b, "skipped", output="skipped: goal cancelled")
    set_task_status(c, "skipped", stage="blocked", output=f"{loop.QUOTA_SKIP}quota")
    with session() as s:
        assert [s.get(Task, x).stage for x in (a, b, c)] == ["skipped", "cancelled", "blocked"]
        old = Task(goal_id=gid, iteration=1, key="legacy", title="legacy", role="qa", status="done")
        s.add(old)
        s.flush()
        assert workflow.task_stage(old) == "approved"


def test_blocker_lines_become_project_blockers_while_the_goal_is_open():
    assert contracts.blockers("ok\n- BLOCKER: the staging DB is down\nBLOCKER: no API key\n") == [
        "the staging DB is down", "no API key"]
    p = projects.create("Blocked")
    gid = loop.create_goal("Needs the DB", project_id=p["id"])
    (tid,) = loop._persist(gid, 1, [_contract("a")])
    with session() as s:
        g = s.get(Goal, gid)
        g.iteration = 1
        workflow.set_status(s, g, "running", "test")
    set_task_status(tid, "done", gate={"blockers": ["the staging DB is down"]})
    management.sync(p["id"])
    with session() as s:
        r = s.scalars(select(Risk).where(Risk.project_id == p["id"], Risk.cause == f"task:{tid}:blocker:0")).one()
        assert r.status == "open" and "staging DB" in r.title
        workflow.set_status(s, s.get(Goal, gid), "cancelled", "test")
    management.sync(p["id"])
    with session() as s:
        assert s.scalars(select(Risk).where(Risk.cause == f"task:{tid}:blocker:0")).one().status == "resolved"
