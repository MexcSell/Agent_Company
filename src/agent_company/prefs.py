"""Administrator settings stored in the database, overriding `.env` values."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from agent_company import config
from agent_company.db import Setting, session

# key -> (type, description, constraint)
SCHEMA: dict[str, tuple[type, str]] = {
    "max_iterations": (int, "Rounds of the goal loop before a goal fails (1-10)"),
    "max_parallel": (int, "Tasks running at once across all goals (1-16)"),
    "require_approval": (bool, "Work that passes the gate waits for an administrator before merging"),
    "command_timeout": (int, "Seconds a quality command or agent run may take"),
    "rotation_interval": (int, "Seconds between rotation visits of the external-data room (0 = manual); restart to apply"),
    "proposal_policy": (str, "Who decides agent proposals: 'user' (administrator) or 'superiors' (the GM)"),
    "max_proposal_goals": (int, "Goals a single goal may spawn through approved proposals"),
    "goal_budget_usd": (float, "Maximum model spend per goal in USD (0 = no limit)"),
    "goal_time_budget": (int, "Maximum wall-clock seconds per goal (0 = no limit)"),
    "allow_preexisting_failures": (bool, "Checks that already failed before the agents started do not block the gate"),
    "auto_push": (bool, "Push approved work to the project's GitHub remote"),
    "quality_commands": (list, "Default quality commands for goals that set none"),
    "executor": (str, "'auto' (Model Router) or 'offline' (simulator)"),
}
LIMITS: dict[str, tuple[float, float]] = {
    "max_iterations": (1, 10), "max_parallel": (1, 16), "command_timeout": (10, 7200),
    "rotation_interval": (0, 86400), "max_proposal_goals": (0, 20), "goal_budget_usd": (0, 10_000),
    "goal_time_budget": (0, 7 * 86400),
}
CHOICES: dict[str, set[str]] = {"proposal_policy": {"user", "superiors"}, "executor": {"auto", "offline"}}


def _coerce(key: str, value: Any) -> Any:
    typ, _ = SCHEMA[key]
    if typ is bool:
        if isinstance(value, str):
            value = value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if typ is list:
        if isinstance(value, str):
            value = [x.strip() for x in value.split("\n") if x.strip()]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{key} must be a list of strings")
        return value
    try:
        value = typ(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key} must be {typ.__name__}") from e
    if key in LIMITS:
        lo, hi = LIMITS[key]
        if not lo <= value <= hi:
            raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of {sorted(CHOICES[key])}")
    return value


def load() -> None:
    """Read overrides from the database into the running settings."""
    with session() as s:
        values = {r.key: r.value.get("v") for r in s.scalars(select(Setting)) if r.key in SCHEMA}
    config.set_overrides(values)
    _reset_runtime()


def _reset_runtime() -> None:
    from agent_company import scheduler

    scheduler._global_slots = None  # pick up a new max_parallel


def describe() -> list[dict[str, Any]]:
    with session() as s:
        stored = {r.key for r in s.scalars(select(Setting))}
    cur = config.settings()
    return [{"key": k, "type": t.__name__, "description": d, "value": getattr(cur, k),
             "overridden": k in stored, "choices": sorted(CHOICES.get(k, []))} for k, (t, d) in SCHEMA.items()]


def set_value(key: str, value: Any) -> Any:
    if key not in SCHEMA:
        raise ValueError(f"unknown setting {key!r}")
    v = _coerce(key, value)
    with session() as s:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value={"v": v}))
        else:
            row.value = {"v": v}
    load()
    return v


def clear(key: str) -> None:
    with session() as s:
        row = s.get(Setting, key)
        if row is not None:
            s.delete(row)
    load()
