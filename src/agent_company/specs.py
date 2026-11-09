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
`save_answer()` writes a finished task's answer to its .md/.txt file_exists path and to the documents of the goal's own
spec folder its command conditions name; a `=== FILE: <path> ===` … `=== END FILE ===` block gives one path its own
content, and a block for another own-folder document (.md/.txt/.json/.yaml) is placed too. In the goal's own spec
folder also over a stale file (read-only) or where the run made none (writing), never a loop file, another goal's
folder or .git. `task_note()` tells the agent so.
`configure()` lets a CLI General Manager (Claude Code / Codex) update the project's `.claude/` configuration
in the worktree; anything it changes outside `.claude/` and `.mcp.json` is put back.
"""

from __future__ import annotations

import json
import posixpath
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
SPEC_PATH = re.compile(r"\.claude/specs/[^\s'\"`;|&<>()]+")  # a spec file a command condition names
BLOCK_FILES = ANSWER_FILES + (".json", ".yaml", ".yml")  # an unnamed FILE block may also be one of these
FILE_OPEN = re.compile(r"=== FILE: (.+?) ===")
FILE_END = "=== END FILE ==="


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


def _normalise(goal: Any, path: str) -> str:
    """A file_exists path in the worktree-relative form the loop writes and checks: without the absolute worktree
    prefix, a leading `./` or a trailing `/` (characters only; containment is decided after resolving)."""
    prefix = str(goal.worktree).rstrip("/") + "/" if goal.worktree else ""
    if prefix and path.startswith(prefix):
        path = path[len(prefix):]
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def _out_of_scope(goal: Any, path: str, allowed: list[str]) -> bool:
    """A file_exists path no task may create: outside the allowed paths, not exempt from the scope gate and not
    already in the worktree, so the condition can never pass."""
    from agent_company import adr, checks

    if path.startswith(CONFIG_PATHS) or path.startswith(adr.FOLDER + "/"):  # the scope gate's own exemptions
        return False
    if any(checks.in_scope(path, pat) for pat in allowed):
        return False
    return not (goal.worktree and (Path(goal.worktree) / path).exists())


def check_plan(goal: Any, plan: list[Any], *, drop_out_of_scope: bool = False) -> list[str]:
    """Reject a plan whose acceptance conditions name a `.claude/specs/<X>` folder other than this goal's, or a file
    the loop writes itself in any spec folder (K54), or a file_exists path outside the goal's allowed paths: such a
    plan can never pass. Paths are not rewritten; the only change is the harmless normalisation of a spec file_exists
    path (`_normalise`), returned as `<key>: <old> -> <new>`. Every problem of the plan is listed in one ValueError,
    one per line. With `drop_out_of_scope` (the fallback plan) out-of-scope file_exists conditions are removed and
    reported instead; the K54 problems still raise."""
    own = slug(goal.title, goal.id)
    rel = relative_folder(goal)
    allowed = list(getattr(goal, "allowed_paths", None) or [])
    report, problems, fixes = [], [], []
    outside: list[tuple[Any, Any, str]] = []

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
                path = _normalise(goal, cond.path)
                scan(c.key, path, whole=True)
                if path != cond.path and SPEC_DIR.match(path):
                    report.append(f"{c.key}: {cond.path} -> {path}")
                    fixes.append((cond, path))
                if allowed and _out_of_scope(goal, path, allowed):
                    outside.append((c, cond, path))
            elif cond.type == "command" and cond.run:
                scan(c.key, cond.run)
    if outside and not drop_out_of_scope:
        problems.append("file_exists acceptance outside the goal's allowed paths can never pass: "
                        + "; ".join(f"task {c.key}: {path}" for c, _, path in outside)
                        + f". Allowed paths: {', '.join(allowed)}. Use a path inside them, or a contains, regex or "
                        "command condition instead.")
    if problems:
        raise ValueError("\n".join(problems))
    for cond, path in fixes:
        cond.path = path
    for c, cond, path in outside:
        c.acceptance = [a for a in c.acceptance if a is not cond]
        report.append(f"{c.key}: dropped file_exists {path} (outside the allowed paths)")
    return report


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _blocks(text: str) -> tuple[list[tuple[str, str]], str, list[str]]:
    """The answer's `=== FILE: <path> ===` … `=== END FILE ===` blocks in order (markers are whole lines after
    trimming; the content is exactly the lines between them), the text outside them, and the paths of open markers
    without an end, whose lines stay in that text. An end marker outside a block is text."""
    lines = text.splitlines()
    pairs: list[tuple[str, str]] = []
    rest: list[str] = []
    i = 0
    while i < len(lines):
        m = FILE_OPEN.fullmatch(lines[i].strip())
        end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == FILE_END), None) if m else None
        if m is None or end is None:
            rest.append(lines[i])
            i += 1
            continue
        pairs.append((m.group(1).strip(), "\n".join(lines[i + 1:end])))
        i = end + 1
    unterminated = [m.group(1).strip() for line in rest if (m := FILE_OPEN.fullmatch(line.strip()))]
    return pairs, "\n".join(rest), unterminated


def documents(text: str) -> dict[str, str]:
    """The answer's FILE blocks, path → content; the first block of a path wins, an unterminated one is ignored."""
    found: dict[str, str] = {}
    for path, body in _blocks(text)[0]:
        found.setdefault(path, body)
    return found


def _command_paths(run: str) -> list[str]:
    """The `.claude/specs/` files a command condition names, without globs, variables or brace expansions."""
    return [m.group(0) for m in SPEC_PATH.finditer(run) if not any(ch in m.group(0) for ch in "*?[${")]


def _named_paths(goal: Any, contract: Any) -> list[tuple[str, bool]]:
    """Each path the acceptance names, normalised, with whether a command condition named it."""
    found = []
    for cond in contract.acceptance:
        if cond.type == "file_exists" and cond.path:
            found.append((_normalise(goal, cond.path), False))
        elif cond.type == "command" and cond.run:
            found += [(_normalise(goal, p), True) for p in _command_paths(cond.run)]
    return found


def _inside(root: Path, goal: Any, path: str) -> tuple[Path, str] | None:
    """The resolved target and its worktree-relative path, or None outside the worktree or in `.git`."""
    target = (root / _normalise(goal, path)).resolve()
    if root not in target.parents or ".git" in target.relative_to(root).parts:
        return None
    return target, target.relative_to(root).as_posix()


def save_answer(goal: Any, contract: Any, text: str, *, writes: bool, changed: list[str] | None) -> list[str]:
    """A finished task's acceptance names a document to exist: its answer becomes that document, so the check
    measures the work, not the file permission (agents cannot write `.claude/`). The documents are its .md/.txt
    file_exists paths and the ones in the goal's own spec folder a command condition names. A FILE block for one of
    them is its content, the text outside the blocks the others'; without blocks the whole answer is each. A block
    for another document (BLOCK_FILES) in the own spec folder is placed too; any other block is ignored and logged.
    In the goal's own spec folder a read-only task's answer replaces a stale file from an earlier iteration (K336)
    and a writing task's answer fills a path its run did not change (`changed` None: unknown, so none); loop files
    are never written. Another goal's spec folder and `.git` are never written, nor anything outside the worktree.
    Elsewhere only a read-only task writes, and only a new file (named ones only).
    Returns the worktree-relative paths written; a path that cannot be written is logged and skipped."""
    if not text.strip() or not goal.worktree:
        return []
    root = Path(goal.worktree).resolve()
    own = relative_folder(goal) + "/"
    pairs, rest, unterminated = _blocks(text)
    ignored = [(path, f"no {FILE_END} line") for path in unterminated]
    blocks: dict[str, str] = {}  # worktree-relative path → content, the first block of a path
    spelled: dict[str, str] = {}  # worktree-relative path → the block's path as written, for the log
    for path, body in pairs:
        where = _inside(root, goal, path)
        if where is None:
            ignored.append((path, "outside the goal's spec folder"))
        elif where[1] in blocks:
            ignored.append((path, "a second block for the same file"))
        else:
            blocks[where[1]], spelled[where[1]] = body, path
    answer = rest if pairs else text
    targets: list[tuple[Path, str]] = []
    for path, by_command in _named_paths(goal, contract):
        where = _inside(root, goal, path) if path.lower().endswith(ANSWER_FILES) else None
        if where and not (by_command and not where[1].startswith(own)):  # a command names only own documents
            targets.append(where)
    named = {rel for _, rel in targets}
    for rel in blocks:
        if rel in named:
            continue
        if not rel.startswith(own):
            if not (writes and changed and rel in changed):  # else a writing executor applied it: not the loop's
                ignored.append((spelled[rel], "outside the goal's spec folder"))
        elif not rel.lower().endswith(BLOCK_FILES):
            ignored.append((spelled[rel], "not a document"))
        else:
            targets.append((root / rel, rel))
    saved: list[str] = []
    seen: set[str] = set()
    for target, rel in targets:
        if rel in seen:
            continue
        seen.add(rel)
        body = blocks.get(rel, answer)
        why = ""
        if rel.startswith(own):
            if target.name in LOOP_FILES:
                why = "loop file"
            elif writes and (changed is None or rel in changed):  # a folder: _write logs
                why = "changed by the run"
        elif rel.split("/")[:2] == [".claude", "specs"] or writes or target.exists():
            why = "outside the goal's spec folder"
        if not why and not body.strip():
            why = "empty"
        if why:
            if rel in blocks:
                ignored.append((spelled[rel], why))
            continue
        try:
            _write(target, body)
        except OSError as e:
            log_event("error", f"saving the answer of task {contract.key} as {rel}: {e}", goal_id=goal.id)
            continue
        saved.append(rel)
    for path, why in ignored:
        log_event("task", f"block {path} of task {contract.key} ignored: {why}", goal_id=goal.id)
    return saved


def task_note(goal: Any, contract: Any) -> str:
    """For the task prompt: the acceptance names a file in the goal's own spec folder, which the loop writes from
    the answer (agent sessions are refused `.claude/`); how a FILE block gives one file its own content. Depends only
    on `contract.writes` (the run kind is chosen later). "" when no own-folder path is named."""
    d = relative_folder(goal)
    if not any(posixpath.normpath(p).startswith(d + "/") for p, _ in _named_paths(goal, contract)):
        return ""
    lead = (f"Claude Code sessions are refused edits under `.claude/`, so do not spend turns trying; the loop writes "
            f"files in `{d}/` from your answer. If your tool did write such a file, the loop keeps yours."
            if contract.writes else
            f"This session has no write tool for `{d}/`; the loop writes those files from your answer.")
    return (f"{lead} To give a file its own content, put it in a block: a line `=== FILE: <worktree-relative path> "
            f"===`, the content, then a line `{FILE_END}`. Without blocks, your whole answer becomes each named "
            f"document; with blocks, the text outside them. {', '.join(LOOP_FILES)} are never written.\n\n")


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
    """After the tasks ran, before the gate commits: spec/design/review files from the outputs. An architect task is
    the spec when its key is `spec`/`specification` or its acceptance or saved answer names this goal's spec.md, and
    then never also design. A spec.md or design.md that save_answer wrote (`gate["saved_answer"]`) or a run changed
    itself (`gate["files"]`) for a task of this iteration is that task's, kept."""
    d = folder(goal)
    with session() as s:
        tasks = s.scalars(select(Task).where(Task.goal_id == goal.id, Task.iteration == iteration).order_by(Task.id)).all()
        rows = [(t.key, t.title, t.role, t.status, (t.output or "").strip()) for t in tasks]
        kept = {p for t in tasks for field in ("saved_answer", "files") for p in (t.gate or {}).get(field) or []}
        named = [{*((t.gate or {}).get("saved_answer") or []),  # the documents each task's acceptance names
                  *(_normalise(goal, c.get("path") or "") for c in (t.contract or {}).get("acceptance") or []
                    if c.get("type") == "file_exists")} for t in tasks]
    own = relative_folder(goal)

    def section(items: list[tuple[str, str, str, str, str]]) -> str:  # whole: a spec cut at its start loses its frame
        return "\n\n".join(f"## {title} ({role}, {status})\n\n{out or '(no output)'}" for _, title, role, status, out in items)

    spec = [r for r, n in zip(rows, named)
            if r[2] == "architect" and (r[0] in {"spec", "specification"} or f"{own}/spec.md" in n)]
    design = [r for r in rows if r[2] == "architect" and r not in spec]
    review = [r for r in rows if r[2] in {"qa", "reviewer"}]
    sa = [r for r in rows if r[2] in {"security", "network_security", "analyst", "comprehensive_analyst"}]
    if spec and f"{own}/spec.md" not in kept:
        _write(d / "spec.md", f"# Specification: {goal.title}\n\n{section(spec)}")
    if design and f"{own}/design.md" not in kept:
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
