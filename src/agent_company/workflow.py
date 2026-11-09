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
`set_stage` allows only the moves of `TASK_TRANSITIONS`; `completed` and `cancelled` are final.
"""

from __future__ import annotations

import os
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
# the task lifecycle's moves (verification phase 6, V24): forward along LIFECYCLE (steps may be skipped: a task set
# done directly), to a waiting or ending stage, reopened for a retry. `completed` and `cancelled` are final: a worker
# still running when its goal was cancelled records its result, but the task stays cancelled
LIFECYCLE = ("created", "planned", "assigned", "in_progress", "review", "approved", "completed")
_WAIT = {"blocked", "skipped", "cancelled", "failed", "reopened"}
TASK_TRANSITIONS: dict[str, set[str]] = {
    **{st: set(LIFECYCLE[i + 1:]) | _WAIT for i, st in enumerate(LIFECYCLE[:-2])},
    "reopened": set(LIFECYCLE[1:]) | _WAIT - {"reopened"},
    "approved": {"completed", "reopened", "cancelled"},
    "blocked": set(LIFECYCLE[1:]) | _WAIT - {"blocked"},
    "skipped": {"planned", "reopened", "cancelled"},
    "failed": {"reopened", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
# a test run may make an illegal move raise (AGENT_COMPANY_STRICT_STAGES=1); the server only refuses and logs it
STRICT_TASK_STAGES = os.environ.get("AGENT_COMPANY_STRICT_STAGES") == "1"


class IllegalTransition(ValueError):
    pass


def set_stage(t: Task, stage: str, reason: str = "") -> bool:
    """Move a task (loaded in an open session) to a lifecycle stage; every change is kept in `stage_log`."""
    if stage not in TASK_STAGES:
        raise ValueError(f"unknown task stage {stage!r}")
    if t.stage == stage:
        return False
    cur = t.stage or "created"
    if stage not in TASK_TRANSITIONS.get(cur, set()):
        if STRICT_TASK_STAGES:
            raise IllegalTransition(f"task #{t.id}: {cur} → {stage} ({reason})")
        from sqlalchemy.orm import object_session

        from agent_company.db import log_event

        log_event("task", f"task {t.key} stays {cur}: {cur} → {stage} is not a task transition"
                  + (f" ({reason[:200]})" if reason else ""), goal_id=t.goal_id, s=object_session(t))
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
    # mission.label_counts: a resumed or reopened goal (to in ACTIVE), or one whose tasks follow a final move
    # below, is counted again on the next read
    g.label_counts = {}
    s.add(GoalTransition(goal_id=g.id, from_status=cur, to_status=to, reason=reason[:500], actor=actor[:128]))
    if to in ("approved", "cancelled", "rejected", "paused", "failed"):
        _tasks_follow(s, g, to)
    if g.kind and to in ("awaiting_approval", "approved", "rejected", "failed"):
        from agent_company import orchestration

        try:  # S04-R-13: the template's canary and self-evaluation; never in the way of the transition
            orchestration.after_outcome(s, g, to)
        except Exception as e:  # noqa: BLE001
            from agent_company.db import log_event

            log_event("error", f"orchestration self-evaluation after goal #{g.id}: {type(e).__name__}: {e}", s=s)
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
