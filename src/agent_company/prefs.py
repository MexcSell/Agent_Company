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
    "goal_token_budget": (int, "Maximum model tokens per goal (0 = no limit)"),
    "goal_agent_limit": (int, "Agents working on one goal at once (0 = no limit)"),
    "allow_preexisting_failures": (bool, "Checks that already failed before the agents started do not block the gate"),
    "auto_push": (bool, "Push approved work to the project's GitHub remote"),
    "quality_commands": (list, "Default quality commands for goals that set none"),
    "executor": (str, "'auto' (Model Router) or 'offline' (simulator)"),
    "transport": (str, "How browsers connect: 'https' (encrypted, default) or 'http' (sealed cookie only)"),
    "enable_claude_code": (bool, "Let agents (and the General Manager) run the Claude Code CLI on the server's Claude login"),
    "enable_codex": (bool, "Let agents run the Codex CLI on the server's ChatGPT login"),
    "study_interval": (int, "Seconds between visits of the learning rotation, which rewrites one agent's definition (0 = manual)"),
    "retry_max_attempts": (int, "Attempts per model when a run fails with a transient error (429, 5xx, timeout)"),
    "retry_base_seconds": (float, "First retry delay in seconds; it doubles on each retry"),
    "retry_max_seconds": (float, "Longest delay between two retries, in seconds"),
    "min_improvement": (int, "Percentage points a trial playbook must beat the baseline by to be promoted"),
    "max_goal_depth": (int, "Generations of goals that proposals may spawn below a person's order"),
    "budget_exhausted_action": (str, "When a goal's budget runs out: 'pause' (wait for an administrator) or 'stop' (fail it)"),
    "escalate_after": (int, "Consecutive iterations the same check may fail before it is escalated to the planner"),
    "candidate_smoke": (bool, "A candidate playbook must pass the smoke suite before it is tried on users' goals"),
    "approval_blocker_hours": (int, "Hours a goal may wait for approval before it becomes a project blocker (0 = never)"),
    "inactive_days": (int, "Days without work after which an agent is listed for retirement review"),
    "explore_rate": (float, "Share of non-critical tasks given to a new agent that has too little evidence yet (0-1)"),
    "improvement_min_score": (float, "Percent a code-improvement candidate's weighted benchmark score must beat the baseline by"),
    "improvement_max_regression": (int, "Percent any single benchmark metric of a candidate may get worse before it is rejected"),
    "improvement_auto_rollback": (bool, "Revert a deployed code improvement automatically when the safe restart refuses it"),
    "a2a_enabled": (bool, "Publish the A2A agent card and take tasks from A2A clients issued on Security -> A2A clients"),
    "shift_seconds": (int, "Length of a research shift on the rotation in seconds (0 = every rotation turn is a shift)"),
}
LIMITS: dict[str, tuple[float, float]] = {
    "max_iterations": (1, 10), "max_parallel": (1, 16), "command_timeout": (10, 7200),
    "rotation_interval": (0, 86400), "max_proposal_goals": (0, 20), "goal_budget_usd": (0, 10_000),
    "goal_time_budget": (0, 7 * 86400), "study_interval": (0, 7 * 86400),
    "goal_token_budget": (0, 1_000_000_000), "goal_agent_limit": (0, 16),
    "retry_max_attempts": (1, 10), "retry_base_seconds": (0, 600), "retry_max_seconds": (0, 3600),
    "min_improvement": (0, 100), "max_goal_depth": (1, 10), "escalate_after": (1, 10),
    "approval_blocker_hours": (0, 720), "inactive_days": (1, 3650), "explore_rate": (0, 1),
    "improvement_min_score": (0, 100), "improvement_max_regression": (0, 100),
    "shift_seconds": (0, 7 * 86400),
}
CHOICES: dict[str, set[str]] = {"proposal_policy": {"user", "superiors"}, "executor": {"auto", "offline"},
                                "transport": {"https", "http"}, "budget_exhausted_action": {"pause", "stop"}}


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
    if key == "transport":  # reconfigure nginx first; a failure leaves the setting unchanged
        from agent_company import transport

        transport.apply(v)
    with session() as s:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value={"v": v}))
        else:
            row.value = {"v": v}
    load()
    return v


def clear(key: str) -> None:
    if key == "transport":  # back to the .env value
        from agent_company import transport

        transport.apply(config.load_settings().transport)
    with session() as s:
        row = s.get(Setting, key)
        if row is not None:
            s.delete(row)
    load()


# --- dashboard layout: JSON values kept apart from the .env settings ----------------------------

UI_KEYS = {"nav_layout"}


def get_ui(key: str) -> Any:
    if key not in UI_KEYS:
        raise ValueError(f"unknown layout key {key!r}")
    with session() as s:
        row = s.get(Setting, f"ui:{key}")
        return (row.value or {}).get("v") if row else None


def set_ui(key: str, value: Any) -> Any:
    if key not in UI_KEYS:
        raise ValueError(f"unknown layout key {key!r}")
    if key == "nav_layout":
        if not isinstance(value, dict) or not all(isinstance(value.get(k, []), list) for k in ("top", "bottom")):
            raise ValueError("nav_layout is {top: [view ids], bottom: [view ids]}")
        value = {k: [str(x)[:32] for x in value.get(k, []) if not (k == "hidden" and x == "settings")][:40]
                 for k in ("top", "bottom", "hidden")}
    with session() as s:
        row = s.get(Setting, f"ui:{key}")
        if row is None:
            s.add(Setting(key=f"ui:{key}", value={"v": value}))
        else:
            row.value = {"v": value}
    return value
