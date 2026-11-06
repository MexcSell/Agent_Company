"""Cost and Usage Monitor: per-run tokens and cost, daily/weekly windows, limits."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from agent_company.db import Agent, ModelEntry, Run, Task, now, session


def day_start(t: datetime | None = None) -> datetime:
    t = t or now()
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(t: datetime | None = None) -> datetime:
    d = day_start(t)
    return d - timedelta(days=d.weekday())  # Monday 00:00 UTC


def cost_of(entry: ModelEntry | None, input_tokens: int, output_tokens: int) -> float:
    if entry is None:
        return 0.0
    return (input_tokens * entry.input_cost + output_tokens * entry.output_cost) / 1_000_000


def record(*, model_name: str, kind: str, input_tokens: int = 0, output_tokens: int = 0,
           cost: float | None = None, duration: float = 0.0, ok: bool = True, error: str = "",
           task_id: int | None = None, agent_id: int | None = None, purpose: str = "task",
           goal_id: int | None = None, res: Any = None) -> None:
    """One run. `res` (an executors.Result) adds what the tool gateway granted it."""
    with session() as s:
        entry = s.scalars(select(ModelEntry).where(ModelEntry.name == model_name)).first()
        if cost is None:
            cost = cost_of(entry, input_tokens, output_tokens)
        s.add(Run(model_name=model_name, kind=kind, input_tokens=input_tokens,
                  output_tokens=output_tokens, cost=cost, duration=duration, ok=ok,
                  error=error[:2000], task_id=task_id, agent_id=agent_id, purpose=purpose, goal_id=goal_id,
                  isolation=getattr(res, "isolation", "") or "", tools=list(getattr(res, "tools", None) or [])[:100],
                  permissions=list(getattr(res, "permissions", None) or []),
                  dropped=list(getattr(res, "dropped", None) or []),
                  denied_tools=list(getattr(res, "denied_tools", None) or [])[:100],
                  tool_calls=list(getattr(res, "tool_calls", None) or [])))
    from agent_company import router

    if not ok and router.is_limit_error(error):  # a plan or quota limit: the router skips the model until its reset
        router.mark_limited(model_name, error)
    if agent_id is not None:  # activity and health (SPEC-02 S02-R-02)
        from agent_company import registry

        registry.after_run(agent_id)


def goal_spend(goal_id: int) -> float:
    with session() as s:
        return float(s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(Run.goal_id == goal_id)) or 0.0)


def goal_tokens(goal_id: int) -> int:
    with session() as s:
        return int(s.scalar(select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0))
                            .where(Run.goal_id == goal_id)) or 0)


def task_spend(task_id: int) -> float:
    with session() as s:
        return float(s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(Run.task_id == task_id)) or 0.0)


def tokens_since(model_name: str, since: datetime) -> int:
    with session() as s:
        total = s.scalar(select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0))
                         .where(Run.model_name == model_name, Run.started_at >= since))
        return int(total or 0)


def over_limit(entry: ModelEntry) -> str | None:
    if entry.daily_token_limit and tokens_since(entry.name, day_start()) >= entry.daily_token_limit:
        return "daily token limit reached"
    if entry.weekly_token_limit and tokens_since(entry.name, week_start()) >= entry.weekly_token_limit:
        return "weekly token limit reached"
    return None


def _window(s, model_name: str, since: datetime) -> dict[str, Any]:  # noqa: ANN001
    row = s.execute(select(func.count(), func.coalesce(func.sum(Run.input_tokens), 0),
                           func.coalesce(func.sum(Run.output_tokens), 0),
                           func.coalesce(func.sum(Run.cost), 0.0))
                    .where(Run.model_name == model_name, Run.started_at >= since)).one()
    return {"runs": row[0], "input_tokens": int(row[1]), "output_tokens": int(row[2]),
            "tokens": int(row[1]) + int(row[2]), "cost": round(float(row[3]), 6)}


def summary() -> dict[str, Any]:
    t = now()
    d0, w0 = day_start(t), week_start(t)
    next_day, next_week = d0 + timedelta(days=1), w0 + timedelta(days=7)
    with session() as s:
        models = []
        for m in s.scalars(select(ModelEntry).order_by(ModelEntry.id)).all():
            models.append({
                "name": m.name, "kind": m.kind,
                "daily": _window(s, m.name, d0) | {"limit": m.daily_token_limit},
                "weekly": _window(s, m.name, w0) | {"limit": m.weekly_token_limit},
            })
        total = s.execute(select(func.count(), func.coalesce(func.sum(Run.cost), 0.0),
                                 func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0))).one()
        running = s.scalar(select(func.count()).select_from(Task).where(Task.status == "running"))
        busy = s.scalar(select(func.count()).select_from(Agent).where(Agent.status == "busy"))
    return {
        "models": models,
        "totals": {"runs": total[0], "cost": round(float(total[1]), 6), "tokens": int(total[2])},
        "active_sessions": {"running_tasks": running, "busy_agents": busy},
        "resets": {
            "daily_at": next_day.isoformat(), "daily_in_seconds": int((next_day - t).total_seconds()),
            "weekly_at": next_week.isoformat(),
            "weekly_in_seconds": int((next_week - t).total_seconds()),
        },
    }
