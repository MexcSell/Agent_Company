"""Administrator settings stored in the database, overriding `.env` values."""

from __future__ import annotations

import json
from pathlib import Path
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
    "tls_domains": (list, "Domain names for the dashboard (e.g. agent-company.internal): added to the HTTPS "
                          "certificate and the nginx server_name; clients need DNS or a hosts-file entry"),
    "enable_claude_code": (bool, "Let agents (and the General Manager) run the Claude Code CLI on the server's Claude login"),
    "enable_codex": (bool, "Let agents run the Codex CLI on the server's ChatGPT login"),
    "enable_copilot": (bool, "Let agents run the GitHub Copilot CLI on the server's GitHub login"),
    "study_interval": (int, "Seconds between visits of the learning rotation, which rewrites one agent's definition (0 = manual)"),
    "retry_max_attempts": (int, "Attempts per model when a run fails with a transient error (429, 5xx, timeout)"),
    "retry_base_seconds": (float, "First retry delay in seconds; it doubles on each retry"),
    "retry_max_seconds": (float, "Longest delay between two retries, in seconds"),
    "min_improvement": (int, "Percentage points a trial playbook must beat the baseline by to be promoted"),
    "max_goal_depth": (int, "Generations of goals that proposals may spawn below a person's order"),
    "budget_exhausted_action": (str, "When a goal's budget runs out: 'pause' (wait for an administrator) or 'stop' (fail it)"),
    "cost_overrun_factor": (float, "A goal that spends more than this many times its planner's cost estimate pauses "
                                   "for an administrator (1-20)"),
    "escalate_after": (int, "Consecutive iterations the same check may fail before it is escalated to the planner"),
    "repo_map_chars": (int, "Characters of the repository map (definitions ranked by use) in the planner's prompt "
                            "and each task's package; 0 = no map"),
    "stuck_repeat_limit": (int, "A Claude Code or Codex run ends when it makes the same tool call this many times in a "
                                "row (0 = never)"),
    "stuck_error_limit": (int, "A Claude Code or Codex run ends when the same tool call fails with the same error this "
                               "many times (0 = never)"),
    "candidate_smoke": (bool, "A candidate playbook must pass the smoke suite before it is tried on users' goals"),
    "benchmark_schedule": (str, "Scheduled benchmark review: 'off', 'daily', 'weekly' or 'monthly' runs of the "
                                "benchmark suite with real models, compared with the previous run; tasks that no "
                                "longer pass are notified (needs a budget)"),
    "benchmark_suite": (str, "Suite of the scheduled benchmark review (an evals/ suite name, e.g. smoke)"),
    "benchmark_budget_usd": (float, "Model spend in USD one scheduled benchmark review may use; 0 = it never runs"),
    "approval_blocker_hours": (int, "Hours a goal may wait for approval before it becomes a project blocker (0 = never)"),
    "inactive_days": (int, "Days without work after which an agent is listed for retirement review"),
    "explore_rate": (float, "Share of non-critical tasks given to a new agent that has too little evidence yet (0-1)"),
    "recruit_requires_approval": (str, "Recruitments that wait for an administrator: 'never', 'permanent_only' (a new "
                                       "agent) or 'always' (also a clone made for a role nobody holds)"),
    "qualify_recruits": (bool, "A new recruit of a writing role first passes an eval suite as a candidate before it is "
                               "used (costs model calls; the offline simulator always fails it)"),
    "improvement_min_score": (float, "Percent a code-improvement candidate's weighted benchmark score must beat the baseline by"),
    "improvement_max_regression": (int, "Percent any single benchmark metric of a candidate may get worse before it is rejected"),
    "improvement_auto_rollback": (bool, "Revert a deployed code improvement automatically when the safe restart refuses it"),
    "a2a_enabled": (bool, "Publish the A2A agent card and take tasks from A2A clients issued on Security -> A2A clients"),
    "shift_seconds": (int, "Length of a research shift on the rotation in seconds (0 = every rotation turn is a shift)"),
    "blocked_commands": (list, "Regular expressions (one per line) the command policy refuses on top of its built-in "
                               "rules; they cannot loosen them"),
    "retrieval_w_relevance": (float, "Knowledge ranking: weight of keyword relevance to the task (FTS5 + term coverage)"),
    "retrieval_w_technology": (float, "Knowledge ranking: weight of the item's technologies the task and project use"),
    "retrieval_w_verified": (float, "Knowledge ranking: weight of verification (evidence class, reviewed by a person)"),
    "retrieval_w_author": (float, "Knowledge ranking: weight of who learned it (the agent itself, related agents)"),
    "retrieval_w_recency": (float, "Knowledge ranking: weight of recency"),
    "retrieval_w_contradiction": (float, "Knowledge ranking: rank lost by an item with an unresolved contradiction"),
    "retrieval_strategies": (list, "Knowledge ranking procedure per query class, one '<class>=<strategy>' per line "
                                   "(classes exact, document, recent, complex, concept; strategies balanced, literal, "
                                   "fresh, broad); learned by 'learn retrieval', kept only while recall@5 holds"),
    "sandbox": (str, "Quality commands and benchmarks run agent-written code: 'auto' in the L3 sandbox (no network, "
                     "only the worktree writable) when bubblewrap works, 'required' never outside it, 'off' on the host"),
    "sandbox_memory_mb": (int, "Memory limit of a sandboxed command in MB (needs systemd-run)"),
    "sandbox_tasks": (int, "Processes and threads a sandboxed command may run at once (needs systemd-run)"),
    "sandbox_cpus": (float, "CPU cores a sandboxed command may use (needs systemd-run)"),
    "sandbox_paths": (list, "Extra read-only paths (toolchains) visible in the sandbox, one per line"),
    "sandbox_env": (list, "Extra environment variable names passed to checked commands, one per line"),
    "dependency_audit": (str, "Changed dependency manifests are audited with pip-audit / npm audit when installed: "
                              "'block' fails the gate on a known vulnerability in an added or changed dependency, "
                              "'warn' only lists it, 'off' runs no audit"),
    "research_tool_rounds": (int, "Rounds in which a research request's model plans tool calls (search the "
                                  "collected documents, arXiv papers, fetch a page) that the research lead runs before "
                                  "it writes the report (0 = off)"),
    "max_clones": (int, "Clones an agent may have at once when all agents of its role are busy or degraded; beyond "
                        "that the work waits (an administrator's own Clone is not counted against it)"),
    "health_recovery_minutes": (int, "A degraded agent is not staffed; after this many minutes its model is probed "
                                     "with the model test, and a passing probe makes it healthy again (the wait "
                                     "doubles after each failed probe, at most 6 h; 0 = never, an administrator "
                                     "resets it)"),
    "mcp_revalidate_hours": (int, "Hours between re-tests of every enabled MCP server: a tool whose description or "
                                  "input schema changed is no longer allowed until an administrator ticks it again "
                                  "(0 = only when Test is pressed)"),
    "required_test_stages": (list, "Gate stages every goal must run checks in, one per line: performance (load and "
                                   "stress), compatibility, accessibility, integration, …; the checks are the "
                                   "project's quality commands tagged with the stage, e.g. '[accessibility] npx "
                                   "pa11y-ci'. A project may add stages"),
    "contradiction_judge": (str, "Knowledge contradictions: the instructor's verdict is recorded for the person who "
                                 "resolves the pair ('advise'), also closes a pair it finds compatible as 'both hold' "
                                 "('decide'; it never rejects an item), or no model is asked ('off')"),
}
RETRIEVAL_KEYS = {k for k in SCHEMA if k.startswith("retrieval_w_")}
LIMITS: dict[str, tuple[float, float]] = {
    "max_iterations": (1, 10), "max_parallel": (1, 16), "command_timeout": (10, 7200),
    "rotation_interval": (0, 86400), "max_proposal_goals": (0, 20), "goal_budget_usd": (0, 10_000),
    "goal_time_budget": (0, 7 * 86400), "study_interval": (0, 7 * 86400),
    "goal_token_budget": (0, 1_000_000_000), "goal_agent_limit": (0, 16),
    "retry_max_attempts": (1, 10), "retry_base_seconds": (0, 600), "retry_max_seconds": (0, 3600),
    "min_improvement": (0, 100), "max_goal_depth": (1, 10), "escalate_after": (1, 10), "cost_overrun_factor": (1, 20),
    "repo_map_chars": (0, 50_000), "stuck_repeat_limit": (0, 100), "stuck_error_limit": (0, 100),
    "approval_blocker_hours": (0, 720), "inactive_days": (1, 3650), "explore_rate": (0, 1),
    "improvement_min_score": (0, 100), "improvement_max_regression": (0, 100),
    "shift_seconds": (0, 7 * 86400), "health_recovery_minutes": (0, 1440), "max_clones": (0, 50), "research_tool_rounds": (0, 5),
    "mcp_revalidate_hours": (0, 720), "benchmark_budget_usd": (0, 1000),
    "sandbox_memory_mb": (128, 262_144), "sandbox_tasks": (16, 65_536), "sandbox_cpus": (0.1, 256),
    **{k: (0, 10) for k in RETRIEVAL_KEYS},
}
CHOICES: dict[str, set[str]] = {"proposal_policy": {"user", "superiors"}, "executor": {"auto", "offline"},
                                "transport": {"https", "http"}, "budget_exhausted_action": {"pause", "stop"},
                                "recruit_requires_approval": {"never", "permanent_only", "always"},
                                "sandbox": {"off", "auto", "required"}, "dependency_audit": {"off", "warn", "block"},
                                "contradiction_judge": {"off", "advise", "decide"},
                                "benchmark_schedule": {"off", "daily", "weekly", "monthly"}}


def _coerce(key: str, value: Any) -> Any:
    typ, _ = SCHEMA[key]
    if typ is bool:
        if isinstance(value, str):
            value = value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if key == "tls_domains":  # commas as well as newlines; strict names, as they go into the nginx config
        from agent_company import transport

        return transport.parse_domains(value)
    if typ is list:
        if isinstance(value, str):
            value = [x.strip() for x in value.split("\n") if x.strip()]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{key} must be a list of strings")
        if key == "sandbox_paths":
            for path in value:
                if not path.startswith("/") or path.rstrip("/") in {"", "/home", "/root", "/etc", "/var"}:
                    raise ValueError(f"sandbox_paths: {path!r} must be an absolute path below a toolchain directory")
                data = config.settings().data_dir
                if data.is_relative_to(path) or Path(path).is_relative_to(data):
                    raise ValueError(f"sandbox_paths: {path!r} would show the data directory to agent code")
        if key == "sandbox_env":
            from agent_company.sandbox import SECRETISH

            if bad := [n for n in value if SECRETISH.search(n) or not n.replace("_", "").isalnum()]:
                raise ValueError(f"sandbox_env: {', '.join(bad)} look like credentials or are not variable names")
        if key == "required_test_stages":
            from agent_company.quality import COMMAND_STAGES

            value = list(dict.fromkeys(x.strip().lower() for x in value if x.strip()))
            if bad := [x for x in value if x not in COMMAND_STAGES]:
                raise ValueError(f"required_test_stages: {', '.join(bad)} are not command stages "
                                 f"({', '.join(sorted(COMMAND_STAGES))})")
        if key == "retrieval_strategies":
            from agent_company import learning

            learning.parse_strategies(value)
        if key == "blocked_commands":
            import re

            for pattern in value:
                try:
                    re.compile(pattern)
                except re.error as e:
                    raise ValueError(f"blocked_commands: {pattern!r} is not a regular expression ({e})") from e
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
    if key == "benchmark_suite":
        from agent_company import evaluation

        value = value.strip()
        try:
            if "/" in value or not value:
                raise ValueError("a suite name, not a path")
            evaluation.load_suite(value)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"benchmark_suite: {value!r} is not a usable suite ({e})") from e
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


def set_value(key: str, value: Any, actor: str = "system") -> Any:
    """Store an override. A governance setting (policies.GOVERNANCE_KEYS) records a policy version (S01-R-05)."""
    from agent_company import policies

    if key not in SCHEMA:
        raise ValueError(f"unknown setting {key!r}")
    v = _coerce(key, value)
    if key in RETRIEVAL_KEYS:
        _check_retrieval(key, v, actor)
    if key == "retrieval_strategies":
        _check_strategies(v, actor)
    if key == "transport":  # reconfigure nginx first; a failure leaves the setting unchanged
        from agent_company import transport

        transport.apply(v)
    if key in policies.GOVERNANCE_KEYS:
        policies.baseline()
    previous = _stored(key) if key == "tls_domains" else None
    with session() as s:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value={"v": v}))
        else:
            row.value = {"v": v}
    load()
    if key == "tls_domains":
        _apply_domains(key, previous)
    if key in policies.GOVERNANCE_KEYS:
        policies.record("company", actor, f"{key} = {v}")
    return v


def _stored(key: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(Setting, key)
        return dict(row.value) if row is not None else None


def _apply_domains(key: str, previous: dict[str, Any] | None) -> None:
    """Reissue the certificate and rewrite the nginx site for new domain names (REQ-009). Runs after the value is
    stored, so `transport.hosts()` sees it; if nginx refuses, the previous value is restored and the error raised."""
    from agent_company import transport

    try:
        transport.apply(config.settings().transport, force_reload=True)
    except ValueError:
        with session() as s:
            row = s.get(Setting, key)
            if previous is None:
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(Setting(key=key, value=previous))
            else:
                row.value = previous
        load()
        raise


def _check_retrieval(key: str, value: float, actor: str) -> None:
    """A ranking weight changes only when recall@5 of the retrieval eval holds (SPEC-11 S11-R-08)."""
    from agent_company import learning
    from agent_company.db import log_event

    name = key.removeprefix("retrieval_w_")
    before, after = learning.evaluate_retrieval()["value"], learning.evaluate_retrieval(**{name: value})["value"]
    if after < before:
        raise ValueError(f"{key} = {value:g} lowers recall@5 of evals/retrieval.json from {before:.2f} to {after:.2f}")
    log_event("retrieval", f"{key} = {value:g} by {actor}: recall@5 {before:.2f} -> {after:.2f}",
              key=key, before=before, after=after)


def stored_weights() -> dict[str, float | None]:
    """The stored `retrieval_w_<name>` overrides by name; None where the .env value or the default holds."""
    with session() as s:
        rows = {r.key: r.value.get("v") for r in s.scalars(select(Setting).where(Setting.key.in_(RETRIEVAL_KEYS)))}
    return {k.removeprefix("retrieval_w_"): rows.get(k) for k in sorted(RETRIEVAL_KEYS)}


def set_weights(values: dict[str, float | None], actor: str, check: bool = True) -> dict[str, float]:
    """Several ranking weights at once (a retrieval experiment, S13-R-05): with `check`, recall@5 of the retrieval
    eval must hold for the change as a whole, not for each weight alone. None clears the override."""
    from agent_company import learning
    from agent_company.db import log_event

    keys = {f"retrieval_w_{n}": v for n, v in values.items()}
    if unknown := sorted(set(keys) - RETRIEVAL_KEYS):
        raise ValueError(f"unknown ranking weights: {', '.join(unknown)}")
    coerced = {k: None if v is None else _coerce(k, v) for k, v in keys.items()}
    before = learning.evaluate_retrieval()["value"]
    defaults = config.load_settings()
    after_w = {k.removeprefix("retrieval_w_"): getattr(defaults, k) if v is None else v for k, v in coerced.items()}
    after = learning.evaluate_retrieval(**after_w)["value"]
    if check and after < before:
        raise ValueError(f"these weights lower recall@5 of evals/retrieval.json from {before:.2f} to {after:.2f}")
    with session() as s:
        for k, v in coerced.items():
            row = s.get(Setting, k)
            if v is None:
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(Setting(key=k, value={"v": v}))
            else:
                row.value = {"v": v}
    load()
    log_event("retrieval", f"ranking weights set by {actor}: recall@5 {before:.2f} -> {after:.2f}",
              weights=after_w, before=before, after=after)
    return learning.weights()


def _check_strategies(lines: list[str], actor: str) -> None:
    """The ranking procedures per query class change only when recall@5 of the retrieval eval holds (SE-R-21)."""
    from agent_company import learning
    from agent_company.db import log_event

    new = learning.parse_strategies(lines)
    before = learning.evaluate_retrieval()["value"]
    after = learning.evaluate_retrieval(strategies=new)["value"]
    if after < before:
        raise ValueError(f"retrieval_strategies {', '.join(lines)} lowers recall@5 of evals/retrieval.json from "
                         f"{before:.2f} to {after:.2f}")
    log_event("retrieval", f"retrieval_strategies = {', '.join(lines) or '(balanced)'} by {actor}: recall@5 "
              f"{before:.2f} -> {after:.2f}", key="retrieval_strategies", before=before, after=after)


def clear(key: str, actor: str = "system") -> None:
    from agent_company import policies

    if key == "transport":  # back to the .env value
        from agent_company import transport

        transport.apply(config.load_settings().transport)
    if key in policies.GOVERNANCE_KEYS:
        policies.baseline()
    previous = _stored(key) if key == "tls_domains" else None
    with session() as s:
        row = s.get(Setting, key)
        if row is not None:
            s.delete(row)
    load()
    if key == "tls_domains":
        _apply_domains(key, previous)
    if key in policies.GOVERNANCE_KEYS:
        policies.record("company", actor, f"{key} back to the .env value")


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
