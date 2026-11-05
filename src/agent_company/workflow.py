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

A task keeps two fields (ASEAO05 S05-R-08). `Task.status` is what the scheduler executes on
(queued|running|done|failed|skipped). `Task.stage` is its lifecycle, logged in `Task.stage_log`:

  created ─▶ planned ─▶ assigned ─▶ in_progress ─▶ review ─▶ approved ─▶ completed (the goal is approved)
               │ reopened (an earlier attempt failed or was stopped)   ╲▶ failed
               ▼
            blocked (quota, budget, a recruitment or the goal paused) · skipped (a dependency failed)
            cancelled (the goal was cancelled or rejected)

Tasks written before stages existed read as the stage of their status (`STATUS_STAGE`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.db import Goal, GoalTransition, Task, now, session

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


TASK_STAGES = ("created", "planned", "assigned", "in_progress", "review", "approved", "completed",
               "blocked", "failed", "cancelled", "reopened", "skipped")
STATUS_STAGE = {"queued": "planned", "running": "in_progress", "done": "approved", "failed": "failed",
                "skipped": "skipped"}
TASK_CLOSED = ("completed", "failed", "cancelled", "skipped")  # a goal's later state does not change these


class IllegalTransition(ValueError):
    pass


def set_stage(t: Task, stage: str, reason: str = "") -> bool:
    """Move a task (loaded in an open session) to a lifecycle stage; every change is kept in `stage_log`."""
    if stage not in TASK_STAGES:
        raise ValueError(f"unknown task stage {stage!r}")
    if t.stage == stage:
        return False
    t.stage_log = [*(t.stage_log or []), {"from": t.stage or "created", "to": stage, "reason": reason[:200],
                                          "at": now().isoformat()}][-50:]
    t.stage = stage
    return True


def task_stage(t: Task) -> str:
    if (t.stage or "created") == "created" and not t.stage_log:  # written before stages were tracked
        return STATUS_STAGE.get(t.status, "created")
    return t.stage


def _tasks_follow(s: Session, g: Goal, to: str) -> None:
    """The goal's state moves its latest iteration's tasks along: approved → completed, cancelled/rejected →
    cancelled, paused/failed → blocked while they wait."""
    for t in s.scalars(select(Task).where(Task.goal_id == g.id, Task.iteration == g.iteration)):
        stage = task_stage(t)
        if to == "approved" and stage == "approved":
            set_stage(t, "completed", "goal approved")
        elif to in ("cancelled", "rejected") and stage not in TASK_CLOSED:
            set_stage(t, "cancelled", f"goal {to}")
        elif to in ("paused", "failed") and t.status == "queued" and stage in ("planned", "assigned", "reopened"):
            set_stage(t, "blocked", f"goal {to}")


def set_status(s: Session, g: Goal, to: str, reason: str = "", actor: str = "system") -> bool:
    """Move a goal (loaded in `s`) to `to`. Returns False when it already is there; raises on an illegal move."""
    cur = g.status
    if cur == to:
        return False
    if to not in TRANSITIONS.get(cur, set()):
        raise IllegalTransition(f"goal #{g.id}: {cur} → {to} is not a workflow transition")
    g.status = to
    s.add(GoalTransition(goal_id=g.id, from_status=cur, to_status=to, reason=reason[:500], actor=actor[:128]))
    if to in ("approved", "cancelled", "rejected", "paused", "failed"):
        _tasks_follow(s, g, to)
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
