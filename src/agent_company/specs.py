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
`check_plan()` rejects (never rewrites) a plan whose acceptance names another spec folder or a loop-written file;
the GM is told why and plans again (K54).
`save_answer()` writes a finished task's answer to its .md/.txt file_exists path: in the goal's own spec folder also
over a stale file (read-only) or where the run made none (writing), never a loop file, another goal's folder or .git.
`configure()` lets a CLI General Manager (Claude Code / Codex) update the project's `.claude/` configuration
in the worktree; anything it changes outside `.claude/` and `.mcp.json` is put back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company.db import Goal, Task, log_event, session

ANSWER_FILES = (".md", ".markdown", ".txt", ".rst")  # documents a task's answer may become
CONFIG_PATHS = (".claude/", ".mcp.json")
LOOP_FILES = ("requirements.md", "tasks.md", "review.md", "sa-review.md", "decisions.md")  # the loop writes them
# A `.claude/specs/<X>` folder, with or without a trailing slash; check_plan rejects any <X> but the goal's own (K54).
SPEC_DIR = re.compile(r"\.claude/specs/([^/\s'\"`;|&<>()]+)")
LOOP_FILE = re.compile(rf"\.claude/specs/[^/\s'\"`;|&<>()]+/({'|'.join(map(re.escape, LOOP_FILES))})(?![\w.-])")


def slug(title: str, goal_id: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"{goal_id:04d}-{s or 'goal'}"


def folder(goal: Goal) -> Path:
    return Path(goal.worktree) / ".claude" / "specs" / slug(goal.title, goal.id)


def relative_folder(goal: Any) -> str:
    return f".claude/specs/{slug(goal.title, goal.id)}"


def planner_note(goal: Any) -> str:
    d = relative_folder(goal)
    return (f"This goal's spec folder is `{d}/`. Any spec or design document a task produces goes there (a read-only "
            f"task's .md/.txt file_exists path in it is filled with that task's answer); use no other `.claude/specs/` "
            f"folder. {', '.join(LOOP_FILES)} are written by the loop itself and must not appear in acceptance "
            "conditions.\n")


def check_plan(goal: Any, plan: list[Any]) -> list[str]:
    """Reject a plan whose acceptance conditions name a `.claude/specs/<X>` folder other than this goal's, or a file
    the loop writes itself in any spec folder (K54): such a plan can never pass. Paths are not rewritten; the only
    change is the harmless normalisation of a spec file_exists path (a leading `./`, the absolute worktree prefix),
    returned as `<key>: <old> -> <new>`. Every problem of the plan is listed in one ValueError, one per line."""
    own = slug(goal.title, goal.id)
    rel = relative_folder(goal)
    prefix = str(goal.worktree).rstrip("/") + "/" if goal.worktree else ""
    report, problems, fixes = [], [], []

    def scan(key: str, text: str, whole: bool = False) -> None:  # a file_exists path is named whole
        for m in SPEC_DIR.finditer(text):
            if m.group(1) != own:
                problems.append(f"task {key}: {text if whole else m.group(0)} is outside this goal's spec folder "
                                f"{rel}/; put spec documents there or in the source tree")
                if whole:
                    break
        for m in LOOP_FILE.finditer(text):
            problems.append(f"task {key}: {text if whole else m.group(0)} is written by the loop itself, not by a "
                            "task (the gate writes decisions.md)")

    for c in plan:
        for cond in c.acceptance:
            if cond.type == "file_exists" and cond.path:
                path = cond.path
                if prefix and path.startswith(prefix):
                    path = path[len(prefix):]
                while path.startswith("./"):
                    path = path[2:]
                scan(c.key, path, whole=True)
                if path != cond.path and SPEC_DIR.match(path):
                    report.append(f"{c.key}: {cond.path} -> {path}")
                    fixes.append((cond, path))
            elif cond.type == "command" and cond.run:
                scan(c.key, cond.run)
    if problems:
        raise ValueError("\n".join(problems))
    for cond, path in fixes:
        cond.path = path
    return report


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def save_answer(goal: Any, contract: Any, text: str, *, writes: bool, changed: list[str]) -> list[str]:
    """A finished task's acceptance names a document to exist: its whole answer becomes that document, so the check
    measures the work, not the file permission (agents cannot write `.claude/`). In the goal's own spec folder a
    read-only task's answer replaces a stale file from an earlier iteration (K336) and a writing task's answer fills
    a path its run did not change; loop files are never written. Another goal's spec folder and `.git` are never
    written, nor anything outside the worktree. Elsewhere only a read-only task writes, and only a new file.
    Returns the worktree-relative paths written; a path that cannot be written is logged and skipped."""
    if not text.strip() or not goal.worktree:
        return []
    root = Path(goal.worktree).resolve()
    prefix = str(goal.worktree).rstrip("/") + "/"
    own = relative_folder(goal) + "/"
    saved: list[str] = []
    for cond in contract.acceptance:
        path = cond.path or ""
        if cond.type != "file_exists" or not path.lower().endswith(ANSWER_FILES):
            continue
        if path.startswith(prefix):
            path = path[len(prefix):]
        while path.startswith("./"):
            path = path[2:]
        target = (root / path).resolve()
        if root not in target.parents:
            continue
        rel = target.relative_to(root).as_posix()
        if ".git" in target.relative_to(root).parts or rel in saved:
            continue
        if rel.startswith(own):
            if target.name in LOOP_FILES or (writes and rel in changed):  # a folder there fails in _write, logged
                continue
        elif rel.split("/")[:2] == [".claude", "specs"] or writes or target.exists():
            continue
        try:
            _write(target, text)
        except OSError as e:
            log_event("error", f"saving the answer of task {contract.key} as {rel}: {e}", goal_id=goal.id)
            continue
        saved.append(rel)
    return saved


def write_plan(goal: Goal, iteration: int, plan: list[Any], summary: str = "") -> None:
    d = folder(goal)
    _write(d / "requirements.md", f"# Requirements: {goal.title}\n\n{goal.description or goal.title}\n\n"
           + ("## Quality commands\n" + "\n".join(f"- `{c}`" for c in goal.quality_commands) + "\n\n"
              if goal.quality_commands and not (goal.trial or {}).get("hide_checks") else "")
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

    def section(items: list[tuple[str, str, str, str, str]]) -> str:  # whole: a spec cut at its start loses its frame
        return "\n\n".join(f"## {title} ({role}, {status})\n\n{out or '(no output)'}" for _, title, role, status, out in items)

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
