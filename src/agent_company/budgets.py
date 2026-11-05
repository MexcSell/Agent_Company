"""Goal budgets and their allocation to child goals (docs/requirements/SPEC-04-orchestration.md S04-R-09).

Dimensions: model cost (USD), model tokens, agents working at once, wall-clock time (`goal_time_budget`, per run of
the loop, see `loop._budget_note`). A goal's own value wins; 0 falls back to the setting (`goal_budget_usd`,
`goal_token_budget`, `goal_agent_limit`); 0 there means no limit.

A goal's budget covers the goals its proposals spawn: its remainder is its budget minus its own use minus, per child
goal, the child's budget while the child can still work (the larger of budget and use) or only its use once it has
ended. So a child's unused budget returns to the parent when it ends, without bookkeeping. A child is capped to the
parent's remainder when it is created, and not created when nothing is left. Raising a budget is an administrator's
action (audited by the API); a child's raise stays within its parent's remainder.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from agent_company import usage
from agent_company.db import Goal, log_event, session
from agent_company.workflow import ACTIVE

DIMENSIONS = {"cost_usd": "budget_usd", "tokens": "budget_tokens"}  # the ones a parent shares with its children


def limits(g: Goal) -> dict[str, float]:
    """The goal's effective budget: cost_usd, tokens, agents (0 = no limit). Without its own value, the setting made
    stricter by the project's own rule (policies.effective, S01-R-05)."""
    from sqlalchemy.orm import object_session

    from agent_company.policies import effective

    s = object_session(g)

    def rule(key: str) -> Any:
        return effective(key, g.project_id, s) or 0

    return {"cost_usd": float(g.budget_usd or rule("goal_budget_usd")), "tokens": int(g.budget_tokens or rule("goal_token_budget")),
            "agents": int(g.max_active_agents or rule("goal_agent_limit"))}


def _used(goal_id: int, dim: str) -> float:
    return usage.goal_spend(goal_id) if dim == "cost_usd" else usage.goal_tokens(goal_id)


def committed(goal_id: int, dim: str) -> float:
    """What the goal and its child goals use or hold of `dim`: its own use plus, per child, the child's budget while
    it can still work (or its use, if larger) and its use once it has ended (recursively)."""
    with session() as s:
        kids = [(k.id, k.status, limits(k)[dim]) for k in s.scalars(select(Goal).where(Goal.parent_goal_id == goal_id))]
    total = _used(goal_id, dim)
    for kid, status, budget in kids:
        held = committed(kid, dim)
        total += max(budget, held) if status in ACTIVE and budget > 0 else held
    return total


def remaining(goal_id: int, dim: str = "cost_usd") -> float | None:
    """What is left of the goal's budget in `dim` for itself and new child goals; None without a limit."""
    with session() as s:
        g = s.get(Goal, goal_id)
        cap = limits(g)[dim] if g is not None else 0
    if not cap:
        return None
    return max(0.0, cap - committed(goal_id, dim))


def for_child(parent_id: int, requested: dict[str, float] | None = None) -> dict[str, float]:
    """The budget columns of a new child goal: what it asked for (else the setting, else the parent's remainder),
    capped to the parent's remainder. Raises ValueError when the parent has nothing left."""
    from agent_company.policies import effective

    requested = requested or {}
    with session() as s:
        parent = s.get(Goal, parent_id)
        project_id = parent.project_id if parent else None
    out: dict[str, float] = {}
    for dim, column in DIMENSIONS.items():
        left = remaining(parent_id, dim)
        want = float(requested.get(column) or 0)
        if left is None:
            out[column] = want
            continue
        if left <= 0:
            raise ValueError(f"the parent goal's {dim.replace('_usd', '')} budget is used")
        default = effective("goal_budget_usd" if dim == "cost_usd" else "goal_token_budget", project_id)
        ask = want or default or left
        if ask > left:
            log_event("budget", f"child goal's {dim} budget capped to the parent's remainder ({ask:g} → {left:g})",
                      goal_id=parent_id, dimension=dim, requested=ask, granted=left)
        out[column] = min(ask, left)
    out["budget_tokens"] = int(out["budget_tokens"])
    return out


def set_budget(goal_id: int, actor: str, budget_usd: float | None = None, budget_tokens: int | None = None,
               max_active_agents: int | None = None) -> dict[str, Any]:
    """Change a goal's budget (administrators; the API audits it). A child goal's cost and tokens stay within what
    its parent has left plus what the child already holds."""
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(goal_id)
        parent, status = g.parent_goal_id, g.status
        before = {"budget_usd": g.budget_usd, "budget_tokens": g.budget_tokens,
                  "max_active_agents": g.max_active_agents, **limits(g)}
    changes: dict[str, Any] = {}
    for column, value in (("budget_usd", budget_usd), ("budget_tokens", budget_tokens),
                          ("max_active_agents", max_active_agents)):
        if value is None:
            continue
        if float(value) < 0:
            raise ValueError(f"{column} cannot be negative")
        changes[column] = float(value) if column == "budget_usd" else int(value)
    for dim, column in DIMENSIONS.items():
        if parent is None or column not in changes:
            continue
        left = remaining(parent, dim)
        if left is None:
            continue
        held = committed(goal_id, dim)  # what the child already counts for in the parent's remainder
        room = left + (max(before[dim], held) if status in ACTIVE and before[dim] else held)
        if not changes[column] or changes[column] > room:
            raise ValueError(f"the parent goal #{parent} has {room:g} {dim} left for this goal")
    with session() as s:
        g = s.get(Goal, goal_id)
        for k, v in changes.items():
            setattr(g, k, v)
        after = {"budget_usd": g.budget_usd, "budget_tokens": g.budget_tokens, "max_active_agents": g.max_active_agents}
        log_event("budget", f"budget changed by {actor}: {after}", goal_id=goal_id, before=before, after=after, s=s)
    return summary(goal_id)


def summary(goal_id: int) -> dict[str, Any]:
    """The goal's budget, use and remainder per dimension (for the goal view and the API)."""
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(goal_id)
        lim = limits(g)
        own = {"budget_usd": g.budget_usd, "budget_tokens": g.budget_tokens, "max_active_agents": g.max_active_agents}
    return {"own": own, "limits": lim,
            "used": {"cost_usd": round(usage.goal_spend(goal_id), 6), "tokens": usage.goal_tokens(goal_id)},
            "remaining": {dim: remaining(goal_id, dim) for dim in DIMENSIONS}}
