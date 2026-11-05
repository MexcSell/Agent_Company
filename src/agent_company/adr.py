"""Architecture Decision Records (docs/requirements/ASEAO08-engineering.md S08-R-02, ASEAO08 §7.2, AT-005).

A significant technology or architecture choice of a goal's plan (a `technical` or `critical` decision record,
decisions.py) is written to `docs/adr/NNNN-title.md` in the goal's worktree before the plan checkpoint, so it is
committed and merged with the goal's branch and versioned by git. NNNN is the decision record's id: numbers are
unique across goals working on one repository at once, and the file and its record name each other.

An ADR has context, options, criteria, the decision, its reasons and evidence, consequences (assumptions,
limitations, confidence) and a status: Accepted (approved), Proposed (waiting for a person), Rejected or
Superseded. A later decision on the same topic of the same project (or goal) supersedes the earlier ADR: the new
file names the old one and the old file's status says by which. A re-plan repeating the same recommendation
reuses the existing ADR instead of writing a copy. The files are documentation: failing to write one never stops
a goal. The gate's scope check accepts exactly the ADR files recorded on the goal's decisions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company import worktrees
from agent_company.db import Decision, Goal, log_event, session

FOLDER = "docs/adr"
KINDS = {"technical", "critical"}
_STATUS = re.compile(r"(?m)^Status: .*$")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50].strip("-") or "decision"


def path_for(d: Decision) -> str:
    return f"{FOLDER}/{d.id:04d}-{slug(d.topic)}.md"


def status_of(d: Decision, superseded_by: str = "") -> str:
    if superseded_by:
        return f"Superseded by {superseded_by}"
    return {"approved": "Accepted", "rejected": "Rejected", "cancelled": "Rejected",
            "expired": "Rejected"}.get(d.status, "Proposed (waits for a person)")


def _norm(topic: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", topic.lower()).split())


def _items(xs: list[Any]) -> str:
    out = []
    for x in xs or []:
        if isinstance(x, dict):
            ref = x.get("ref") or x.get("id") or x.get("note") or ""
            out.append(f"- {ref}" + (f" ({x['class']})" if x.get("class") else ""))
        elif str(x).strip():
            out.append(f"- {x}")
    return "\n".join(out) or "- (none recorded)"


def render(d: Decision, goal: Goal | None, supersedes: str = "") -> str:
    name = Path(path_for(d)).stem.split("-")[0]
    lines = [f"# ADR {name}: {d.topic}", "",
             f"Status: {status_of(d)}",
             f"Date: {d.created_at.date().isoformat()}",
             f"Decision record: #{d.id}" + (f" · goal #{goal.id} ({goal.title[:120]})" if goal else ""),
             f"Category: {d.category}"]
    if supersedes:
        lines.append(f"Supersedes: [{Path(supersedes).stem}]({Path(supersedes).name})")
    lines += ["", "## Context", "", d.question or d.topic, "", "## Options", "", _items(d.alternatives),
              "", "## Criteria", "", _items(d.criteria), "", "## Decision", "", d.recommendation or "(none)",
              "", "## Reasons", "", d.reasoning or "(none recorded)", "", "Evidence:", "", _items(d.evidence),
              "", "## Consequences", "", "Assumptions:", "", _items(d.assumptions), "", "Limitations:", "",
              _items(d.limitations), "", f"Confidence: {d.confidence:.2f}"
              + (f" ({d.confidence_note})" if d.confidence_note else ""), ""]
    return "\n".join(lines)


def _earlier(s: Any, d: Decision) -> Decision | None:
    """The latest earlier decision with an ADR on the same topic: of the same project, or of the same goal."""
    q = select(Decision).where(Decision.id < d.id, Decision.adr_path != "").order_by(Decision.id.desc())
    q = q.where(Decision.project_id == d.project_id) if d.project_id is not None else q.where(
        Decision.goal_id == d.goal_id)
    topic = _norm(d.topic)
    return next((x for x in s.scalars(q) if _norm(x.topic) == topic), None)


def write(goal_id: int, decision_ids: list[int]) -> list[str]:
    """Write the ADRs of these decisions into the goal's worktree; returns the paths written (relative)."""
    written: list[str] = []
    with session() as s:
        goal = s.get(Goal, goal_id)
        if goal is None or not goal.worktree:
            return []
        wt = worktrees.ensure_sandboxed(goal.worktree)
        for did in decision_ids:
            d = s.get(Decision, did)
            if d is None or d.category not in KINDS or d.adr_path:
                continue
            old = _earlier(s, d)
            old_file = wt / old.adr_path if old else None
            if old is not None and not old_file.is_file():  # not in this repository (another one, or deleted)
                old, old_file = None, None
            if old is not None and _norm(old.recommendation) == _norm(d.recommendation) and old.status == d.status:
                d.adr_path = old.adr_path  # the same choice again (a re-plan): no copy
                continue
            rel = path_for(d)
            f = wt / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(render(d, goal, old.adr_path if old else ""), encoding="utf-8")
            if old_file is not None:
                text = old_file.read_text(encoding="utf-8")
                old_file.write_text(_STATUS.sub(f"Status: {status_of(old, Path(rel).stem)}", text, count=1),
                                    encoding="utf-8")
            d.adr_path = rel
            written.append(rel)
            log_event("decision", f"ADR {rel} for decision #{d.id} '{d.topic}'"
                      + (f" (supersedes {old.adr_path})" if old else ""), goal_id=goal_id, s=s,
                      decision_id=d.id, adr=rel)
    return written


def on_decided(decision_id: int) -> None:
    """The decision's status changed (a person decided, or a review disputed it): the ADR's status follows. A goal
    waiting for approval gets the change committed (only the ADR file); a goal still at work commits it with its
    next checkpoint; a finished goal's branch is left alone."""
    from agent_company.gitsync import identity_for

    with session() as s:
        d = s.get(Decision, decision_id)
        g = s.get(Goal, d.goal_id) if d is not None and d.goal_id else None
        if d is None or not d.adr_path or g is None or not g.worktree:
            return
        if g.status not in {"planning", "running", "paused", "awaiting_approval"}:
            return
        status, rel, wt, author, waiting = status_of(d), d.adr_path, g.worktree, g.created_by, \
            g.status == "awaiting_approval"
    try:
        f = worktrees.ensure_sandboxed(wt) / rel
        if not f.is_file():
            return
        text = f.read_text(encoding="utf-8")
        new = _STATUS.sub(f"Status: {status}", text, count=1)
        if new == text:
            return
        f.write_text(new, encoding="utf-8")
        if waiting:
            worktrees.git(wt, "add", "--", rel)
            worktrees.git(wt, "commit", "-q", "-m", f"ADR {Path(rel).stem}: {status}", "--", rel,
                          env=worktrees.identity_env(identity_for(author)))
    except (OSError, worktrees.WorktreeError) as e:
        log_event("error", f"ADR {rel}: status not updated: {e}", goal_id=d.goal_id if d else None)


def paths(goal_id: int) -> list[str]:
    """The ADR files the goal's decisions recorded and the earlier ADRs they superseded (whose status line they
    changed): the gate's scope check accepts these."""
    out: set[str] = set()
    with session() as s:
        for d in s.scalars(select(Decision).where(Decision.goal_id == goal_id, Decision.adr_path != "")):
            out.add(d.adr_path)
            if d.adr_path == path_for(d):  # written for this decision, not reused
                old = _earlier(s, d)
                if old is not None:
                    out.add(old.adr_path)
    return sorted(out)
