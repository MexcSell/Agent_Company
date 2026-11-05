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
from agent_company import contracts, strategies, worktrees
from agent_company.config import settings
from agent_company.db import Goal, Task, session

REVIEW_ROLES = {"qa", "security", "network_security"}

# The review contract (ASEAO08 §13.2-13.3, docs/requirements/ASEAO08-engineering.md S08-R-03).
REVIEW_CHECKLIST = """How to review (the gate checks the format):
1. Start with a section headed "Logic verification". For each changed function trace the data flow for the
   normal case, one edge case and one error case, and say whether the change fixes the root cause of the task
   or only hides the symptom. Code that looks simple but is not shown to work must not be approved.
2. Then go through the checklist: contract satisfied, interfaces compatible, error handling, edge cases,
   tests sufficient, dependencies justified, security, performance, docs updated.
3. Write each problem as one line: FINDING: <file>:<line> | <what breaks, for whom> | <concrete fix>
   One finding per distinct issue; list further places of the same issue in the location (a.py:3, b.py:9).
4. End with OUTCOME: APPROVED | APPROVED_WITH_COMMENTS | REVISION_REQUIRED | REJECTED | ESCALATED
   and then VERDICT: PASS (approved, with or without comments) or VERDICT: FAIL (anything else)."""
PASSING_OUTCOMES = {"APPROVED", "APPROVED_WITH_COMMENTS"}
_OUTCOME = re.compile(r"OUTCOME:\s*\**\s*(APPROVED_WITH_COMMENTS|APPROVED|REVISION_REQUIRED|REJECTED|ESCALATED)", re.I)
_LOGIC = re.compile(r"(?im)^[\s#*>_-]*logic verification\b")
_FINDING = re.compile(r"(?im)^[\s*-]*FINDING:\s*(.+)$")


def merge_findings(output: str) -> list[str]:
    """FINDING lines with the same risk and fix become one finding listing every location."""
    def norm(text: str) -> str:
        return " ".join(text.lower().split())

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in _FINDING.findall(output):
        parts = [p.strip() for p in raw.split("|")]
        where, risk, fix = ("", parts[0], "") if len(parts) == 1 else (parts[0], parts[1], " | ".join(parts[2:]))
        item = merged.setdefault((norm(risk), norm(fix)), {"locs": [], "risk": risk, "fix": fix})
        for loc in (x.strip() for x in where.split(",")):
            if loc and loc not in item["locs"]:
                item["locs"].append(loc)
    return [" | ".join(x for x in (", ".join(i["locs"]), i["risk"], i["fix"]) if x) for i in merged.values()]


def review_verdict(output: str) -> tuple[bool, str]:
    """(passed, why) for a reviewer's output. PASS needs the logic-verification section and, when an
    OUTCOME is given, an approving one; a bare `VERDICT: PASS` is not enough."""
    verdicts = re.findall(r"VERDICT:\s*\**\s*(PASS|FAIL)", output, re.I)
    if verdicts and verdicts[-1].upper() == "FAIL":
        return False, "reviewer: FAIL"
    outcome = _OUTCOME.findall(output)
    if outcome and outcome[-1].upper() not in PASSING_OUTCOMES:
        return False, f"reviewer outcome {outcome[-1].upper()}"
    if not _LOGIC.search(output):
        return False, "review has no logic-verification section (data flow of normal, edge and error cases)"
    return True, outcome[-1].upper() if outcome else "PASS"


# Named gate stages (ASEAO09 §13, S09-R-01). Commands may carry a stage tag: "[integration] pytest tests/it";
# untagged commands are unit tests. `regression` compares commands with the baseline; `governance` reflects
# decisions that wait for a person (it never fails the gate, it holds the merge).
STAGES = ["requirements", "contract", "static", "security", "unit", "integration", "regression", "review", "governance"]
COMMAND_STAGES = {"unit", "integration", "regression", "security", "static"}
_STAGE_TAG = re.compile(r"^\s*\[([a-z_]+)\]\s*", re.I)


def split_stage(cmd: str) -> tuple[str, str]:
    """('integration', 'pytest tests/it') for '[integration] pytest tests/it'; untagged → unit."""
    m = _STAGE_TAG.match(cmd)
    if m and m.group(1).lower() in COMMAND_STAGES:
        return m.group(1).lower(), cmd[m.end():]
    return "unit", cmd


def _stage_of(check: str) -> str:
    if check.startswith("task "):
        return "contract"
    if check.startswith("review "):
        return "review"
    if check == "secret scan":
        return "security"
    if check in {"syntax", "scope", "boundaries"}:
        return "static"
    return "unit"


def _require_approval(goal_id: int) -> bool:
    """`require_approval`, switched on for a project whose own rules ask for it (policies.effective, S01-R-05)."""
    from agent_company.policies import effective

    with session() as s:
        g = s.get(Goal, goal_id)
        return bool(effective("require_approval", g.project_id if g else None, s))


def stages(results: list[dict[str, Any]], goal_id: int) -> list[dict[str, Any]]:
    """One row per stage: pass, fail or skipped (no check of that stage ran), with its checks."""
    from agent_company import decisions

    rows = []
    for st in STAGES:
        if st == "regression":
            mine = [c for c in results if c.get("baseline_passed")]
        else:
            mine = [c for c in results if c.get("stage") == st]
        if st == "governance":
            waiting = decisions.blocking(goal_id)
            rows.append({"stage": st, "status": "waiting" if waiting else "pass", "checks": len(waiting),
                         "failed": [f"decision #{d['id']} {d['topic']}" for d in waiting]})
            continue
        failed = [c["check"] for c in mine if not c["passed"]]
        rows.append({"stage": st, "status": "skipped" if not mine else ("fail" if failed else "pass"),
                     "checks": len(mine), "failed": failed})
    return rows


def baseline(goal_id: int) -> dict[str, Any]:
    """Run the quality commands on the untouched code, before any agent changes it."""
    with session() as s:
        goal = s.get(Goal, goal_id)
        assert goal is not None
        commands = list(goal.quality_commands or []) or list(settings().quality_commands)
        if strategies.skips_checks(goal):  # S04-R-11: a template whose goals change no files runs no commands
            commands = []
        worktree, done = Path(goal.worktree), dict(goal.baseline or {})
    out = dict(done)
    for cmd in commands:
        if cmd not in out:
            ok, detail = contracts.run_command(split_stage(cmd)[1], worktree, settings().command_timeout)
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
        if strategies.skips_checks(goal):
            commands = []
        worktree, base = Path(goal.worktree), goal.base_branch
        allowed, protect, baseline = list(goal.allowed_paths or []), bool(goal.protect_tests), dict(goal.baseline or {})
        permits = list(goal.permits or [])
        ordered_by = goal.created_by
        rows = [(t.key, t.title, t.role, t.status, t.output, dict(t.gate or {})) for t in tasks]
        from agent_company.recruitment import on_probation

        # SPEC-03 S03-R-04: a probationer's change needs a passing review by an agent not on probation
        probation = [t.key for t in tasks if t.writes and t.status == "done" and on_probation(s, t.agent_id)]
        reviewers = {t.key for t in tasks if t.role in REVIEW_ROLES and t.status == "done"
                     and not on_probation(s, t.agent_id)}

    results: list[dict[str, Any]] = []
    feedback: list[str] = []
    for key, title, role, status, output, gate in rows:
        failed = [c for c in gate.get("acceptance", []) if not c["passed"]]
        ok = status == "done" and not failed
        detail = "; ".join(c["detail"] for c in failed) or ("" if status == "done" else output[-300:])
        results.append({"check": f"task {key}", "passed": ok, "detail": detail or status, "stage": "contract"})
        if not ok:
            feedback.append(f"Task '{title}' ({role}) {status}: {detail}")
        if role in REVIEW_ROLES and status == "done":
            passed_review, why = review_verdict(output)
            if not passed_review:
                findings = merge_findings(output)
                results.append({"check": f"review {key}", "passed": False, "detail": why,
                                "findings": findings, "stage": "review"})
                feedback.append(f"Reviewer '{title}' ({why}):\n"
                                + ("\n".join(f"- {f}" for f in findings) if findings else output[-1500:]))
            else:
                results.append({"check": f"review {key}", "passed": True, "detail": why, "stage": "review"})

    if probation:
        passing = [c for c in results if c["check"] in {f"review {k}" for k in reviewers} and c["passed"]]
        results.append({"check": "probation review", "passed": bool(passing), "stage": "review",
                        "detail": (f"{', '.join(probation)} by an agent on probation; reviewed by "
                                   f"{', '.join(c['check'][7:] for c in passing)}") if passing else
                                  f"{', '.join(probation)} by an agent on probation has no passing review"})
        if not passing:
            feedback.append(f"Tasks {', '.join(probation)} were done by an agent on probation and need a passing "
                            "QA review.")

    from agent_company.gitsync import identity_for

    sha = worktrees.commit_all(worktree, f"Goal {goal_id} iteration {iteration}", author=identity_for(ordered_by))
    if base:
        for c in checks_mod.run_static(worktree, base, allowed, protect, permits):
            results.append(c | {"stage": _stage_of(c["check"])})
            if not c["passed"]:
                feedback.append(f"{c['check'].capitalize()} check failed:\n{c['detail']}")
    for cmd in commands:
        stage, run = split_stage(cmd)
        ok, detail = contracts.run_command(run, worktree, settings().command_timeout)
        before = baseline.get(cmd)
        preexisting = not ok and before is not None and not before.get("passed")
        row = {"check": f"command {run}", "passed": ok, "detail": detail[-1500:], "stage": stage,
               "baseline_passed": bool(before and before.get("passed"))}
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
        "stages": stages(results, goal_id),
        "checks": results,
        "feedback": "\n\n".join(feedback),
        "commit": sha,
        "head": worktrees.git(worktree, "rev-parse", "HEAD"),
        "changed_files": worktrees.changed_files(worktree, base) if base else [],
        "needs_approval": passed and _require_approval(goal_id),
    }
