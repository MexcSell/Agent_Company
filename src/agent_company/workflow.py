"""Goal workflow state machine (docs/requirements/SPEC-04-orchestration.md, S04-R-04).

Every change of `Goal.status` goes through `set_status`, which rejects transitions the workflow does not
allow and writes one `goal_transitions` row per change, so a goal's history can be replayed.

  queued ─▶ planning ─▶ running ─▶ awaiting_approval ─▶ approved
     │          ▲           │  ╲            │      ╲
     │          └─revision──┘   ╲           ▼       ▶ rejected
     │                 │         ▶ approved (no approval needed)
     │                 ▼
     │              paused ─▶ queued (resume)
     ▼
  recovering (after a restart) ─▶ planning | running
  failed ─▶ recovering (retry)          any active state ─▶ cancelled | failed

`approved`, `rejected` and `cancelled` are final.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.db import Goal, GoalTransition, session

ACTIVE = ("queued", "planning", "running", "recovering", "paused")  # not final: can still be cancelled
WORKING = ("planning", "running", "recovering")  # a worker is executing it now
FINAL = ("approved", "rejected", "cancelled")

TRANSITIONS: dict[str, set[str]] = {
    "queued": {"planning", "running", "recovering", "paused", "cancelled", "failed"},
    "recovering": {"planning", "running", "queued", "paused", "cancelled", "failed"},
    "planning": {"running", "recovering", "paused", "cancelled", "failed"},
    "running": {"planning", "awaiting_approval", "approved", "recovering", "paused", "cancelled", "failed"},
    "paused": {"queued", "cancelled"},
    "awaiting_approval": {"approved", "rejected", "cancelled"},
    "failed": {"recovering"},
    "approved": set(),
    "rejected": set(),
    "cancelled": set(),
}


class IllegalTransition(ValueError):
    pass


def set_status(s: Session, g: Goal, to: str, reason: str = "", actor: str = "system") -> bool:
    """Move a goal (loaded in `s`) to `to`. Returns False when it already is there; raises on an illegal move."""
    cur = g.status
    if cur == to:
        return False
    if to not in TRANSITIONS.get(cur, set()):
        raise IllegalTransition(f"goal #{g.id}: {cur} → {to} is not a workflow transition")
    g.status = to
    s.add(GoalTransition(goal_id=g.id, from_status=cur, to_status=to, reason=reason[:500], actor=actor[:128]))
    return True


def transition(goal_id: int, to: str, reason: str = "", actor: str = "system") -> bool:
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(f"goal {goal_id}")
        return set_status(s, g, to, reason, actor)


def history(goal_id: int) -> list[dict[str, Any]]:
    with session() as s:
        return [{"from": t.from_status, "to": t.to_status, "reason": t.reason, "actor": t.actor,
                 "at": t.created_at.isoformat()}
                for t in s.scalars(select(GoalTransition).where(GoalTransition.goal_id == goal_id)
                                   .order_by(GoalTransition.id))]
