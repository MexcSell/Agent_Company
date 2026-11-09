"""A planner (the General Manager) runs no task, so its experience comes from its goal's verified outcome; every XP
change keeps the level in step."""

from sqlalchemy import select

from agent_company import learning, workflow
from agent_company.db import Agent, Goal, Task, session


def _gm() -> int:
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first().id


def _goal(planned_by: int | None, iteration: int = 1) -> int:
    with session() as s:
        g = Goal(title="g", description="d", status="running", iteration=iteration)
        s.add(g)
        s.flush()
        s.add(Task(goal_id=g.id, iteration=iteration, key="t1", title="t", role="developer", planned_by=planned_by))
        s.add(Task(goal_id=g.id, iteration=iteration, key="t2", title="t", role="qa", planned_by=planned_by))
        s.add(Task(goal_id=g.id, iteration=iteration - 1, key="t0", title="old plan", role="qa", planned_by=None))
        return g.id


def _xp(aid: int) -> tuple[int, int, int]:
    with session() as s:
        a = s.get(Agent, aid)
        return a.xp, a.level, (a.skills or {}).get("planning", 0)


def test_the_planner_gains_experience_from_its_goals_outcome():
    gm = _gm()
    xp0, _, planning0 = _xp(gm)
    for _ in range(5):
        workflow.transition(_goal(gm), "awaiting_approval", "gate passed")
    xp, level, planning = _xp(gm)
    assert xp == xp0 + 5 * learning.XP_SUCCESS, "once per goal, not once per task"
    assert level == 1 + xp // learning.XP_PER_LEVEL and level >= 2
    assert planning == min(10, planning0 + 5)
    workflow.transition(_goal(gm), "failed", "gate failed")
    assert _xp(gm)[0] == xp + learning.XP_FAILURE and _xp(gm)[2] == planning


def test_a_goal_merged_at_once_counts_and_an_approval_after_waiting_counts_once():
    gm = _gm()
    xp0 = _xp(gm)[0]
    workflow.transition(_goal(gm), "approved", "passed the gate and merged")  # owner policy / no approval needed
    assert _xp(gm)[0] == xp0 + learning.XP_SUCCESS
    gid = _goal(gm)
    workflow.transition(gid, "awaiting_approval", "gate passed")
    workflow.transition(gid, "approved", "a person approved")
    assert _xp(gm)[0] == xp0 + 2 * learning.XP_SUCCESS


def test_a_goal_without_a_recorded_planner_and_other_moves_credit_nobody():
    gm = _gm()
    xp0 = _xp(gm)[0]
    workflow.transition(_goal(None), "awaiting_approval", "loop's own plan")
    workflow.transition(_goal(gm), "paused", "paused")
    assert _xp(gm)[0] == xp0


def test_every_xp_change_keeps_the_level_in_step():
    with session() as s:
        a = s.get(Agent, _gm())
        learning.add_xp(a, 3 * learning.XP_PER_LEVEL + 1)
        assert a.level == 4
