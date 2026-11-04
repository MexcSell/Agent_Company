"""Quality Gate: contracts, QA verdicts, tests/lint/type checks and approval.

A goal iteration passes only when every task finished and met its contract, no
reviewer returned `VERDICT: FAIL`, the change adds no credentials, every changed
file parses, the change stays in the goal's allowed paths (and leaves existing
tests alone when they are protected), and every configured command (tests, lint,
type checks) exits 0 in the goal's worktree. A command that already failed on the
untouched code is marked pre-existing (and does not block with
ALLOW_PREEXISTING_FAILURES). Passing work then waits for human approval unless
REQUIRE_APPROVAL=0.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company import checks as checks_mod
from agent_company import contracts, worktrees
from agent_company.config import settings
from agent_company.db import Goal, Task, session

REVIEW_ROLES = {"qa", "security", "network_security"}


def baseline(goal_id: int) -> dict[str, Any]:
    """Run the quality commands on the untouched code, before any agent changes it."""
    with session() as s:
        goal = s.get(Goal, goal_id)
        assert goal is not None
        commands = list(goal.quality_commands or []) or list(settings().quality_commands)
        worktree, done = Path(goal.worktree), dict(goal.baseline or {})
    out = dict(done)
    for cmd in commands:
        if cmd not in out:
            ok, detail = contracts.run_command(cmd, worktree, settings().command_timeout)
            out[cmd] = {"passed": ok, "detail": detail[-800:]}
    with session() as s:
        s.get(Goal, goal_id).baseline = out
    return out


def evaluate(goal_id: int, iteration: int) -> dict[str, Any]:
    with session() as s:
        goal = s.get(Goal, goal_id)
        assert goal is not None
        tasks = s.scalars(select(Task).where(Task.goal_id == goal_id, Task.iteration == iteration)
                          .order_by(Task.id)).all()
        commands = list(goal.quality_commands or []) or list(settings().quality_commands)
        worktree, base = Path(goal.worktree), goal.base_branch
        allowed, protect, baseline = list(goal.allowed_paths or []), bool(goal.protect_tests), dict(goal.baseline or {})
        ordered_by = goal.created_by
        rows = [(t.key, t.title, t.role, t.status, t.output, dict(t.gate or {})) for t in tasks]

    results: list[dict[str, Any]] = []
    feedback: list[str] = []
    for key, title, role, status, output, gate in rows:
        failed = [c for c in gate.get("acceptance", []) if not c["passed"]]
        ok = status == "done" and not failed
        detail = "; ".join(c["detail"] for c in failed) or ("" if status == "done" else output[-300:])
        results.append({"check": f"task {key}", "passed": ok, "detail": detail or status})
        if not ok:
            feedback.append(f"Task '{title}' ({role}) {status}: {detail}")
        if role in REVIEW_ROLES and status == "done":
            m = re.search(r"VERDICT:\s*(PASS|FAIL)", output, re.I)
            if m and m.group(1).upper() == "FAIL":
                results.append({"check": f"review {key}", "passed": False, "detail": "reviewer: FAIL"})
                feedback.append(f"Reviewer '{title}' found problems:\n{output[-1500:]}")

    from agent_company.gitsync import identity_for

    sha = worktrees.commit_all(worktree, f"Goal {goal_id} iteration {iteration}", author=identity_for(ordered_by))
    if base:
        for c in checks_mod.run_static(worktree, base, allowed, protect):
            results.append(c)
            if not c["passed"]:
                feedback.append(f"{c['check'].capitalize()} check failed:\n{c['detail']}")
    for cmd in commands:
        ok, detail = contracts.run_command(cmd, worktree, settings().command_timeout)
        before = baseline.get(cmd)
        preexisting = not ok and before is not None and not before.get("passed")
        row = {"check": f"command {cmd}", "passed": ok, "detail": detail[-1500:]}
        if preexisting:
            row["preexisting"] = True
            row["detail"] = "already failing before the agents started\n" + row["detail"]
            if settings().allow_preexisting_failures:
                row["passed"] = True
        results.append(row)
        if not row["passed"]:
            feedback.append(("Command failed (it already failed on the untouched code, so the goal may be to "
                             "fix it): " if preexisting else "Command failed: ") + detail[-1500:])

    passed = all(c["passed"] for c in results) and bool(rows)
    score = round(sum(c["passed"] for c in results) / len(results), 3) if results else 0.0
    return {
        "passed": passed,
        "score": score,
        "checks": results,
        "feedback": "\n\n".join(feedback),
        "commit": sha,
        "head": worktrees.git(worktree, "rev-parse", "HEAD"),
        "changed_files": worktrees.changed_files(worktree, base) if base else [],
        "needs_approval": passed and settings().require_approval,
    }
