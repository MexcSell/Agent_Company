"""Risk-based test strategy per goal (ASEAO09 §6, S09-R-02, AT-001).

Each iteration the planner gets a short strategy derived from what the goal touches, and the final one (with the
plan) is stored on the goal (`Goal.test_strategy`):

- the goal's classification (S14-R-05): confidential raises the risk to medium, restricted or secret to high;
- the changed areas: the goal's allowed paths, each task's `in_scope` globs and the files changed so far. A path
  in a sensitive area (authentication, sessions, tokens, secrets, crypto, permissions) makes the goal high-risk,
  and the writing tasks touching it get `risk_level: high`, so the existing security review follows them;
- earlier defects in those files: files that earlier goals on the same repository changed in an iteration the gate
  failed (`Goal.history[].changed_files`). They raise the risk to medium and are named to the planner;
- a task the planner itself marked `risk_level: high`.

A high-risk goal must have checks in the `integration` and `security` stages: the gate adds a `stage <name>` row per
mandatory stage that fails when no check of that stage ran. The planner can satisfy `integration` with a command
acceptance condition carrying `"stage": "integration"`; the gate runs it again itself (quality.evaluate). The
`security` stage holds the secret scan until a dependency audit exists (S08-R-05).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company import contracts, worktrees
from agent_company.db import Goal, now, session

SENSITIVE = re.compile(r"(?i)(auth|login|logout|session|token|passw|secret|crypt|permission|acl|rbac|oauth|jwt|"
                       r"saml|sso|security|credential|\.env\b|private[_-]?key|\bkeys?\b)")
HIGH_STAGES = ["integration", "security"]
RISKS = ["low", "medium", "high"]
CAP = 20  # paths and files listed per field


def _matches(path: str, area: list[str]) -> bool:
    return any(path == a or fnmatch.fnmatch(path, a) or path.startswith(a.rstrip("*").rstrip("/") + "/")
               for a in area if a)


def _changed(g: Goal) -> list[str]:
    if not g.worktree or not g.base_branch or not Path(g.worktree).is_dir():
        return []
    try:
        return worktrees.changed_files(g.worktree, g.base_branch)
    except Exception:  # noqa: BLE001 - a missing base or worktree: nothing changed that we can see
        return []


def _defect_history(s: Any, g: Goal, area: list[str]) -> dict[str, list[int]]:
    """file -> earlier goals on the same repository that changed it in an iteration the gate failed."""
    if not area or not g.repo_path:
        return {}
    out: dict[str, list[int]] = {}
    for other in s.scalars(select(Goal).where(Goal.repo_path == g.repo_path, Goal.id != g.id,
                                              Goal.origin != "eval").order_by(Goal.id.desc()).limit(200)):
        for h in other.history or []:
            if not h.get("failed_checks"):
                continue
            for f in h.get("changed_files") or []:
                if _matches(f, area) and other.id not in out.setdefault(f, []):
                    out[f].append(other.id)
    return dict(sorted(out.items())[:CAP])


def derive(goal_id: int, plan: list[contracts.TaskContract] | None = None, iteration: int = 1) -> dict[str, Any]:
    from agent_company.learning import CLASSIFICATIONS, goal_level

    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(goal_id)
        level = goal_level(s, g)
        changed = _changed(g) if iteration > 1 else []
        area = sorted({*(g.allowed_paths or []), *(p for c in plan or [] for p in c.in_scope), *changed})
        history = _defect_history(s, g, area)
    sensitive = [p for p in area if SENSITIVE.search(p)][:CAP]
    high_tasks = [c.key for c in plan or [] if c.risk_level == "high"]
    reasons: list[str] = []
    risk = 0
    rank = CLASSIFICATIONS.index(level) if level in CLASSIFICATIONS else 1
    if rank >= CLASSIFICATIONS.index("restricted"):
        risk = 2
        reasons.append(f"classified {level}")
    elif rank == CLASSIFICATIONS.index("confidential"):
        risk = 1
        reasons.append("classified confidential")
    if sensitive:
        risk = 2
        reasons.append("sensitive area: " + ", ".join(sensitive[:5]))
    if high_tasks:
        risk = 2
        reasons.append("high-risk task(s): " + ", ".join(high_tasks[:5]))
    if history:
        risk = max(risk, 1)
        reasons.append(f"{len(history)} file(s) with earlier failures: " + ", ".join(list(history)[:5]))
    high = risk == 2
    return {"risk": RISKS[risk], "reasons": reasons or ["no sensitive area, classification or defect history"],
            "mandatory_stages": HIGH_STAGES if high else [], "reviewers": ["qa"] + (["security"] if high else []),
            "sensitive_paths": sensitive, "defect_history": history, "area": area[:CAP],
            "iteration": iteration, "at": now().isoformat()}


def escalate(goal_id: int, plan: list[contracts.TaskContract], iteration: int = 1) -> list[contracts.TaskContract]:
    """A writing task touching a sensitive area becomes high-risk, so a security review follows it. A task without
    its own `in_scope` touches the goal's allowed paths and the files changed so far."""
    with session() as s:
        g = s.get(Goal, goal_id)
        goal_area = [*(g.allowed_paths or []), *(_changed(g) if iteration > 1 else [])] if g else []
    out = []
    for c in plan:
        area = c.in_scope or goal_area
        if c.writes and c.risk_level != "high" and any(SENSITIVE.search(p) for p in area):
            c = c.model_copy(update={"risk_level": "high"})
        out.append(c)
    return out


def store(goal_id: int, strategy: dict[str, Any]) -> None:
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is not None:
            g.test_strategy = strategy


def mandatory(goal: Goal) -> list[str]:
    return list((goal.test_strategy or {}).get("mandatory_stages") or [])


def planner_note(strategy: dict[str, Any]) -> str:
    lines = [f"\nTest strategy: risk {strategy['risk']} ({'; '.join(strategy['reasons'])})."]
    if strategy["mandatory_stages"]:
        lines.append("Mandatory gate stages: " + ", ".join(strategy["mandatory_stages"]) + ". Give a task an "
                     'acceptance condition {"type": "command", "run": "<integration test command>", '
                     '"stage": "integration"} that exercises the changed parts together; the gate runs it itself. '
                     "Writing tasks in sensitive areas get a security review.")
    if strategy["defect_history"]:
        lines.append("These files failed checks in earlier goals; test them explicitly: "
                     + ", ".join(strategy["defect_history"]))
    return "\n".join(lines) + "\n"
