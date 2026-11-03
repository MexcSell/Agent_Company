"""Quality Gate: contracts, QA verdicts, tests/lint/type checks and approval.

A goal iteration passes only when every task finished and met its contract, no
reviewer returned `VERDICT: FAIL`, and every configured command (tests, lint,
type checks) exits 0 in the goal's worktree. Passing work then waits for human
approval unless REQUIRE_APPROVAL=0.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company import contracts, worktrees
from agent_company.config import settings
from agent_company.db import Goal, Task, session

REVIEW_ROLES = {"qa", "security", "network_security"}


def evaluate(goal_id: int, iteration: int) -> dict[str, Any]:
    with session() as s:
        goal = s.get(Goal, goal_id)
        assert goal is not None
        tasks = s.scalars(select(Task).where(Task.goal_id == goal_id, Task.iteration == iteration)
                          .order_by(Task.id)).all()
        commands = list(goal.quality_commands or []) or list(settings().quality_commands)
        worktree, base = Path(goal.worktree), goal.base_branch
        rows = [(t.key, t.title, t.role, t.status, t.output, dict(t.gate or {})) for t in tasks]

    checks: list[dict[str, Any]] = []
    feedback: list[str] = []
    for key, title, role, status, output, gate in rows:
        failed = [c for c in gate.get("acceptance", []) if not c["passed"]]
        ok = status == "done" and not failed
        detail = "; ".join(c["detail"] for c in failed) or ("" if status == "done" else output[-300:])
        checks.append({"check": f"task {key}", "passed": ok, "detail": detail or status})
        if not ok:
            feedback.append(f"Task '{title}' ({role}) {status}: {detail}")
        if role in REVIEW_ROLES and status == "done":
            m = re.search(r"VERDICT:\s*(PASS|FAIL)", output, re.I)
            if m and m.group(1).upper() == "FAIL":
                checks.append({"check": f"review {key}", "passed": False, "detail": "reviewer: FAIL"})
                feedback.append(f"Reviewer '{title}' found problems:\n{output[-1500:]}")

    sha = worktrees.commit_all(worktree, f"Goal {goal_id} iteration {iteration}")
    for cmd in commands:
        ok, detail = contracts.run_command(cmd, worktree, settings().command_timeout)
        checks.append({"check": f"command {cmd}", "passed": ok, "detail": detail[-1500:]})
        if not ok:
            feedback.append(f"Command failed: {detail[-1500:]}")

    passed = all(c["passed"] for c in checks) and bool(rows)
    return {
        "passed": passed,
        "checks": checks,
        "feedback": "\n\n".join(feedback),
        "commit": sha,
        "changed_files": worktrees.changed_files(worktree, base) if base else [],
        "needs_approval": passed and settings().require_approval,
    }
