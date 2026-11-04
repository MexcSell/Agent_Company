"""Spec-driven workflow: every goal leaves its artifacts in `.claude/specs/<slug>/` of its worktree.

Plan → Build → Review → Fix (docs/1.claude-code-multi-agent.jpg). The loop writes, per iteration:

    requirements.md  the goal, its details, quality commands and scope
    tasks.md         the GM's plan: file-disjoint task contracts, roles, dependencies, acceptance
    spec.md          the specification task's output (architect), when there is one
    design.md        design notes from the architect's other tasks
    review.md        the reviewers' (QA) outputs and verdicts
    sa-review.md     security/analysis reviews, when such roles took part
    decisions.md     one entry per iteration: plan summary, gate result, findings

They are committed with the goal's branch, so the approved merge carries them into the project.
`configure()` lets a CLI General Manager (Claude Code / Codex) update the project's `.claude/` configuration
in the worktree; anything it changes outside `.claude/` and `.mcp.json` is put back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company.db import Goal, Task, session

CONFIG_PATHS = (".claude/", ".mcp.json")


def slug(title: str, goal_id: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"{goal_id:04d}-{s or 'goal'}"


def folder(goal: Goal) -> Path:
    return Path(goal.worktree) / ".claude" / "specs" / slug(goal.title, goal.id)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_plan(goal: Goal, iteration: int, plan: list[Any], summary: str = "") -> None:
    d = folder(goal)
    _write(d / "requirements.md", f"# Requirements: {goal.title}\n\n{goal.description or goal.title}\n\n"
           + ("## Quality commands\n" + "\n".join(f"- `{c}`" for c in goal.quality_commands) + "\n\n" if goal.quality_commands else "")
           + ("## Scope\n" + "\n".join(f"- `{p}`" for p in goal.allowed_paths) + "\n" if goal.allowed_paths else ""))
    lines = [f"# Tasks (iteration {iteration})", "", summary, ""]
    for i, c in enumerate(plan, 1):
        acc = "; ".join(json.dumps(a.model_dump(exclude_none=True)) for a in c.acceptance)
        lines += [f"## {i}. {c.title}", f"- key: `{c.key}` · role: {c.role} · writes: {'yes' if c.writes else 'no'}"
                  + (f" · after: {', '.join(c.depends_on)}" if c.depends_on else ""), "", c.description, "",
                  f"Acceptance: {acc or '(none)'}", ""]
    _write(d / "tasks.md", "\n".join(lines))


def write_outputs(goal: Goal, iteration: int, _: Any = None) -> None:
    """After the tasks ran, before the gate commits: spec/design/review files from the outputs."""
    d = folder(goal)
    with session() as s:
        tasks = s.scalars(select(Task).where(Task.goal_id == goal.id, Task.iteration == iteration).order_by(Task.id)).all()
        rows = [(t.key, t.title, t.role, t.status, (t.output or "").strip()) for t in tasks]

    def section(items: list[tuple[str, str, str, str, str]]) -> str:
        return "\n\n".join(f"## {title} ({role}, {status})\n\n{out[-8000:] or '(no output)'}" for _, title, role, status, out in items)

    spec = [r for r in rows if r[2] == "architect" and r[0] in {"spec", "specification"}]
    design = [r for r in rows if r[2] == "architect" and r not in spec]
    review = [r for r in rows if r[2] in {"qa", "reviewer"}]
    sa = [r for r in rows if r[2] in {"security", "network_security", "analyst", "comprehensive_analyst"}]
    if spec:
        _write(d / "spec.md", f"# Specification: {goal.title}\n\n{section(spec)}")
    if design:
        _write(d / "design.md", f"# Design: {goal.title}\n\n{section(design)}")
    if review:
        _write(d / "review.md", f"# Review (iteration {iteration})\n\n{section(review)}")
    if sa:
        _write(d / "sa-review.md", f"# Security and analysis review (iteration {iteration})\n\n{section(sa)}")


def write_decision(goal: Goal, iteration: int, report: dict[str, Any]) -> None:
    """After the gate: the iteration's decision in decisions.md, committed on its own."""
    from agent_company import worktrees

    d = folder(goal)
    with session() as s:
        rows = [(t.key, t.role, t.status) for t in s.scalars(
            select(Task).where(Task.goal_id == goal.id, Task.iteration == iteration).order_by(Task.id))]
    dec = d / "decisions.md"
    old = dec.read_text(encoding="utf-8") if dec.exists() else f"# Decisions: {goal.title}\n"
    failed = [c for c in report.get("checks", []) if not c.get("passed")]
    entry = (f"\n## Iteration {iteration}: {'PASS' if report.get('passed') else 'FAIL'}\n"
             + "\n".join(f"- {key} ({role}): {status}" for key, role, status in rows)
             + ("\n\nFindings:\n" + "\n".join(f"- {c.get('check')}: {str(c.get('detail', ''))[:300]}" for c in failed) if failed else "")
             + ("\n\nNext: fix the findings (loop back to Build)." if not report.get("passed") else "\n\nNext: approval and merge."))
    _write(dec, old.rstrip() + "\n" + entry)
    from agent_company.gitsync import identity_for

    worktrees.commit_all(goal.worktree, f"Record the decision of iteration {iteration} ({'pass' if report.get('passed') else 'fail'})",
                         author=identity_for(goal.created_by))


def configure_prompt(goal: Goal) -> str:
    return (f"Goal: {goal.title}\n{goal.description}\n\nBefore the team starts, update this repository's Claude Code "
            "configuration for the goal, only inside .claude/ and .mcp.json: the teammates it needs "
            "(.claude/agents/<name>.md with name, description, tools, model), rules, skills and commands, and the "
            f"spec in {folder(goal).relative_to(Path(goal.worktree))}/spec.md. Keep what is already there unless it "
            "is wrong. Do not change any other file. End with a two-line summary of what you changed.")


def revert_outside_config(worktree: Path) -> list[str]:
    """Undo changes a configure step made outside the configuration paths. Returns what was put back."""
    from agent_company import worktrees

    changed = worktrees.git(worktree, "diff", "--name-only", "HEAD").splitlines()
    new = worktrees.git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
    put_back = []
    for path in changed + new:
        if not path or path.startswith(CONFIG_PATHS) or path.startswith(".attachments"):
            continue
        if path in new:
            (Path(worktree) / path).unlink(missing_ok=True)
        else:
            worktrees.git(worktree, "checkout", "HEAD", "--", path, check=False)
        put_back.append(path)
    return put_back
