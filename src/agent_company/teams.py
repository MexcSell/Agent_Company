"""A goal's team and its changes (docs/requirements/SPEC-04-orchestration.md S04-R-11, FR-25; §8.4 dynamic team
resizing).

joined      the first time staffing gives an agent a task of the goal (`loop._make_worker`).
added       the General Manager (in its plan, `"team": {"add": [...]}`, or `gm team`) or an administrator adds a role
            mid-goal: staffing picks or recruits an agent for it now, under the usual rules (reuse before recruiting,
            the recruitment approval policy). A recruitment that waits for a person is recorded as `requested`.
released    an agent leaves the goal's team: staffing does not give it another task of this goal; another agent of
            the role is used, cloned or recruited instead, and a role whose every agent was released fails the task
            with that reason, so the planner re-plans.
Rows are never changed or deleted, so the history of earlier assignments stays; `members` folds it into the current
team. The next plan of the goal sees the team (`planner_note`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.db import Agent, Goal, GoalMember, log_event, session

ACTIONS = ("joined", "added", "released", "requested")


def _iteration(s: Session, goal_id: int) -> int:
    g = s.get(Goal, goal_id)
    return g.iteration if g is not None else 0


def released(s: Session, goal_id: int | None) -> set[int]:
    """Agents released from the goal and not added back since."""
    if goal_id is None:
        return set()
    out: set[int] = set()
    for m in s.scalars(select(GoalMember).where(GoalMember.goal_id == goal_id, GoalMember.agent_id.is_not(None))
                       .order_by(GoalMember.id)):
        if m.action == "released":
            out.add(m.agent_id)
        elif m.action in {"joined", "added"}:
            out.discard(m.agent_id)
    return out


def joined(goal_id: int, agent_id: int, role: str, task_id: int | None = None) -> bool:
    """Record an agent's first task of the goal (once)."""
    with session() as s:
        seen = s.scalars(select(GoalMember.id).where(GoalMember.goal_id == goal_id,
                                                     GoalMember.agent_id == agent_id)).first()
        if seen is not None:
            return False
        s.add(GoalMember(goal_id=goal_id, agent_id=agent_id, role=role, action="joined", task_id=task_id,
                         iteration=_iteration(s, goal_id), reason="staffed for a task"))
    return True


def add(goal_id: int, role: str = "", skills: list[str] | None = None, reason: str = "", actor: str = "system",
        agent_id: int | None = None) -> dict:
    """Add a role to the goal's team now, or take a released agent back (`agent_id`). Returns the change."""
    from agent_company import recruitment, registry

    with session() as s:
        if s.get(Goal, goal_id) is None:
            raise KeyError(goal_id)
        if agent_id is not None:
            a = s.get(Agent, agent_id)
            if a is None:
                raise KeyError(f"agent {agent_id}")
            if agent_id not in released(s, goal_id):
                raise ValueError(f"{a.name} was not released from goal #{goal_id}")
            back = a.role
    if agent_id is not None:
        return _record(goal_id, agent_id, back, "added", reason or "taken back", actor)
    role, _ = registry.resolve_role(role)
    if not role:
        raise ValueError("name a role")
    try:
        agent = registry.staff(role, list(skills or []), goal_id)
    except recruitment.RecruitmentPending as e:
        return _record(goal_id, None, role, "requested", f"{reason} ({e})".strip(), actor)
    except (recruitment.RecruitmentRefused, registry.QuotaExceeded, RuntimeError) as e:
        raise ValueError(f"cannot add a {role}: {e}") from e
    registry.release(agent.id, goal_id)
    return _record(goal_id, agent.id, role, "added", reason, actor)


def release(goal_id: int, agent_id: int, reason: str = "", actor: str = "system") -> dict[str, Any]:
    """Release an agent from the goal's team; a task it is running finishes."""
    with session() as s:
        if s.get(Goal, goal_id) is None:
            raise KeyError(goal_id)
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(f"agent {agent_id}")
        if agent_id in released(s, goal_id):
            raise ValueError(f"{a.name} is already released from goal #{goal_id}")
        if a.role == "general_manager":  # it plans the goal; another planner is a model or agent switch on its card
            raise ValueError("the General Manager plans the goal and cannot be released from it")
        role = a.role
    return _record(goal_id, agent_id, role, "released", reason, actor)


def _record(goal_id: int, agent_id: int | None, role: str, action: str, reason: str, actor: str) -> dict[str, Any]:
    with session() as s:
        name = s.get(Agent, agent_id).name if agent_id else ""
        m = GoalMember(goal_id=goal_id, agent_id=agent_id, role=role, action=action, reason=reason.strip()[:500],
                       actor=actor, iteration=_iteration(s, goal_id))
        s.add(m)
        s.flush()
        log_event("team", f"{actor}: {action} {name or role} on goal #{goal_id}" + (f": {reason}" if reason else ""),
                  goal_id=goal_id, agent_id=agent_id, s=s, action=action, role=role)
        return _dict(m, {agent_id: name} if agent_id else {})


def _dict(m: GoalMember, names: dict[int, str]) -> dict[str, Any]:
    return {"id": m.id, "agent_id": m.agent_id, "agent": names.get(m.agent_id) if m.agent_id else None,
            "role": m.role, "action": m.action, "reason": m.reason, "actor": m.actor, "iteration": m.iteration,
            "task_id": m.task_id, "at": m.created_at.isoformat() if m.created_at else None}


def history(goal_id: int) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(select(GoalMember).where(GoalMember.goal_id == goal_id).order_by(GoalMember.id)).all()
        ids = {m.agent_id for m in rows if m.agent_id}
        names = dict(s.execute(select(Agent.id, Agent.name).where(Agent.id.in_(ids))).all()) if ids else {}
        return [_dict(m, names) for m in rows]


def members(goal_id: int) -> list[dict[str, Any]]:
    """The current team: each agent's latest change, released ones included (marked)."""
    latest: dict[Any, dict[str, Any]] = {}
    for m in history(goal_id):
        key = m["agent_id"] or f"role:{m['role']}"
        latest[key] = m
    return list(latest.values())


def planner_note(goal_id: int) -> str:
    """The team for the planner's prompt, and how to resize it."""
    team = members(goal_id)
    lines = [f"- {m['agent'] or '(waiting for recruitment)'} (id {m['agent_id'] or '-'}, {m['role']}): {m['action']}"
             + (f" — {m['reason']}" if m["reason"] and m["action"] != "joined" else "") for m in team]
    return (("\nTeam of this goal:\n" + "\n".join(lines) + "\n") if lines else "") + (
        '\nTo resize the team add "team": {"add": [{"role": "...", "skills": ["..."], "reason": "..."}], '
        '"release": [{"agent_id": 0, "reason": "..."}]} (released agents get no further task of this goal).\n')


def apply_plan(goal_id: int, team: Any, actor: str) -> list[dict[str, Any]]:
    """The planner's `team` field: additions and releases. A change that cannot be made is logged, not raised."""
    out: list[dict[str, Any]] = []
    if not isinstance(team, dict):
        return out
    for item in (team.get("release") or [])[:10]:
        try:
            out.append(release(goal_id, int(item["agent_id"]), str(item.get("reason", "")), actor))
        except (KeyError, TypeError, ValueError) as e:
            log_event("team", f"release refused: {e}", goal_id=goal_id)
    for item in (team.get("add") or [])[:5]:
        try:
            skills = [str(x) for x in item.get("skills") or []][:10]
            out.append(add(goal_id, str(item["role"]), skills, str(item.get("reason", "")), actor))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            log_event("team", f"addition refused: {e}", goal_id=goal_id)
    return out
