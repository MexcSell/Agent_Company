"""Escalation levels and who receives them (docs/requirements/SPEC-04-orchestration.md S04-R-07, S04-R-08).

Level   recipient                                   reached by
1       the assigned agent (it retries)             retries inside `loop._execute`
2       the goal's GM (re-plan)                     a check failing `escalate_after` iterations in a row,
                                                    a request repeated without an answer, technical decisions
3       the manager of the agent's room             the same failure for twice as long, organizational decisions
4       the executive GM of the protected platform  three times as long, financial decisions, decisions about the
        project                                     platform's own code
5       a person (an administrator notification)    four times as long, critical decisions and every decision that
                                                    needs a person

The level follows the decision category and its impact, not only the attempt count. Every escalation is an
`events` row of kind `escalation` with `level`, `recipient` and `recipient_agent_id`. Levels 2-4 are delivered
as a `request` message on the goal's blackboard, so the recipient reads it in its next prompt on that goal
(`blackboard.inbox`); a room manager is the enabled agent of the room that reports outside it, and an agent that
heads its own room escalates to its superior.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.db import Agent, Goal, Project, log_event, session

LEVELS = {1: "assigned agent", 2: "goal GM", 3: "room manager", 4: "executive GM", 5: "human"}
LEVEL_BY_CATEGORY = {"operational": 1, "technical": 2, "organizational": 3, "financial": 4, "critical": 5}


def level_for(category: str, protected: bool = False, human: bool = False) -> int:
    """The level a decision of this category goes to: a person for critical ones and ones that need a person;
    technical and organizational ones about the protected platform project go to its executive GM."""
    if human or category == "critical":
        return 5
    level = LEVEL_BY_CATEGORY.get(category, 2)
    return 4 if protected and level in (2, 3) else level


def _company_gm(s: Session) -> Agent | None:
    q = select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))
    return s.scalars(q.order_by(Agent.enabled.desc(), Agent.id)).first()


def _project(s: Session, goal: Goal | None) -> Project | None:
    return s.get(Project, goal.project_id) if goal is not None and goal.project_id else None


def _room_manager(s: Session, agent: Agent) -> Agent | None:
    """The head of the agent's room: the enabled agent there that reports outside it. One that heads its own
    room escalates to its superior."""
    room = {a.id: a for a in s.scalars(select(Agent).where(Agent.room_key == agent.room_key,
                                                            Agent.parent_id.is_(None)))}
    me = s.get(Agent, agent.parent_id) if agent.parent_id else agent  # a clone answers to its original's manager
    heads = sorted((a for a in room.values() if a.enabled and a.superior_id not in room), key=lambda a: a.id)
    head = heads[0] if heads else None
    if head is None or head.id == me.id:
        return s.get(Agent, me.superior_id) if me.superior_id else None
    return head


def recipient(s: Session, level: int, goal_id: int | None = None, agent_id: int | None = None) -> Agent | None:
    """The agent that receives an escalation of this level (None for level 5, or when nobody fits)."""
    goal = s.get(Goal, goal_id) if goal_id else None
    agent = s.get(Agent, agent_id) if agent_id else None
    if level == 1:
        return agent
    if level == 2:
        p = _project(s, goal)
        gm = s.get(Agent, p.manager_id) if p is not None and p.manager_id else None
        return gm or _company_gm(s)
    if level == 3:
        return (_room_manager(s, agent) if agent is not None else None) or recipient(s, 2, goal_id)
    if level == 4:
        p = s.scalars(select(Project).where(Project.protected)).first()
        gm = s.get(Agent, p.manager_id) if p is not None and p.manager_id else None
        return gm or _company_gm(s)
    return None


def escalate(level: int, reason: str, goal_id: int | None = None, agent_id: int | None = None,
             deliver: bool = True, **data: Any) -> dict[str, Any]:
    """Escalate to `level` (1-5): an `escalation` event naming the recipient; levels 2-4 also leave a request on
    the goal's blackboard for the recipient (`deliver=False` when the caller tells it another way, e.g. the
    planner's feedback); level 5 notifies the administrators. Not to be called inside an open session."""
    from agent_company import blackboard
    from agent_company.governance import notify

    level = max(1, min(5, int(level)))
    with session() as s:
        to = recipient(s, level, goal_id, agent_id)
        to_id, to_role, to_name = (to.id, to.role, to.name) if to is not None else (None, "", "")
        goal = s.get(Goal, goal_id) if goal_id else None
        project_id = goal.project_id if goal is not None else None
    label = LEVELS[level] + (f" {to_name}" if to_name else "")
    log_event("escalation", f"{reason[:400]} (level {level}: {label})", goal_id=goal_id, agent_id=agent_id,
              level=level, recipient=LEVELS[level], recipient_agent_id=to_id, **data)
    if level == 5:
        notify(f"Escalated to a person: {reason}"[:256], reason[:4000], level="warning", source="escalation",
               goal_id=goal_id, project_id=project_id, admin_only=True)
    elif deliver and goal_id and to_id is not None and level >= 2:
        n = len([e for e in blackboard.read(goal_id, ["messages"]) if e["key"].startswith("escalation:")])
        blackboard.put(goal_id, "messages", f"escalation:{n + 1}", f"Escalated to you ({LEVELS[level]}): {reason}"[:1000],
                       author_agent_id=agent_id,
                       meta={"from": "escalation", "to": to_role, "kind": "request", "level": level,
                             "recipient_agent_id": to_id, "delivered": []})
    return {"level": level, "recipient": LEVELS[level], "recipient_agent_id": to_id}
