"""Project management: milestones, risks and blockers, insights and upcoming action items
(docs/requirements/ASEAO05-projects.md S05-R-01..R-05).

Milestones name deliverables, the goals that must be approved and acceptance criteria: commands, re-run in a
throwaway worktree of the project's current HEAD when the milestone is checked or completed, and manual
criteria a person ticks. A milestone is not completed while a mandatory criterion is unmet.

Blockers and generated action items are derived, not hooked into every code path: `sync()` computes what
should be open from the current state (goals paused for budget, goals waiting for approval longer than
`approval_blocker_hours`, milestones whose required goal failed; pending proposals, unresolved defects, open
blockers, failed milestone criteria), opens what is missing and closes what no longer holds. It runs after
every goal and whenever a project's status is read, so it heals itself after a restart. The `NEXT:` lines of a
merged goal's final tasks become follow-up items once; people close those.

Agents report insights in a fenced JSON block: ```json {"insights": [{"title", "description", "evidence",
"expected_benefit", "estimated_cost", "confidence"}]} ```. A person moves them along
recorded → analyzed → reviewed → accepted | rejected → implemented → evaluated; an accepted insight becomes a
knowledge item (screened like lessons, withheld for secret projects) and may become a proposal.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_company import worktrees
from agent_company.config import settings
from agent_company.db import (ActionItem, Agent, Defect, Goal, GoalTransition, Insight, Lesson, Milestone, Project,
                              ProjectHandover, Proposal, Risk, Run, Task, log_event, now, session)
from agent_company.performance import OPEN as OPEN_DEFECT
from agent_company.performance import PRIORITY as DEFECT_PRIORITY

RISK_CATEGORIES = ["technical", "security", "budget", "schedule", "staffing", "external_dependency", "quality",
                   "infrastructure", "requirements"]
SEVERITIES = ["low", "medium", "high", "critical"]
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
RISK_STATUSES = ["open", "mitigated", "resolved", "closed"]
INSIGHT_CHAIN = ["recorded", "analyzed", "reviewed", "accepted", "implemented", "evaluated"]
ITEM_STATUSES = ["open", "in_progress", "done", "dropped"]
INSIGHTS_PER_TASK = 3
FAR = datetime(9999, 1, 1, tzinfo=UTC)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands timestamps back without a zone; they were written in UTC."""
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt else None


def _date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError as e:
        raise ValueError(f"not a date: {value!r} (use YYYY-MM-DD)") from e


def _strs(value: Any, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        value = [x.strip() for x in value.split("\n")]
    return [str(x).strip()[:500] for x in (value or []) if str(x).strip()][:limit]


def _ints(value: Any, limit: int = 50) -> list[int]:
    out = []
    for x in value or []:
        try:
            out.append(int(x))
        except (TypeError, ValueError) as e:
            raise ValueError(f"not an id: {x!r}") from e
    return out[:limit]


def _project(s: Session, project_id: int) -> Project:
    p = s.get(Project, project_id)
    if p is None:
        raise KeyError(f"project {project_id}")
    return p


# --- milestones (S05-R-01) ----------------------------------------------------------------

def _criteria(items: Any) -> list[dict[str, Any]]:
    from agent_company.governance import check_command

    out = []
    for it in items or []:
        if isinstance(it, str):
            it = {"kind": "manual", "text": it}
        if not isinstance(it, dict) or not str(it.get("text", "")).strip():
            raise ValueError("each criterion needs a text (a command or a description)")
        kind = it.get("kind", "manual")
        if kind not in ("command", "manual"):
            raise ValueError("a criterion is a 'command' or 'manual'")
        text = str(it["text"]).strip()[:1000]
        if kind == "command" and (why := check_command(text)):
            raise ValueError(f"criterion command refused: {why}")
        out.append({"kind": kind, "text": text, "mandatory": bool(it.get("mandatory", True)),
                    "met": bool(it.get("met", False)) if kind == "manual" else False,
                    "detail": str(it.get("detail", ""))[:2000], "checked_at": it.get("checked_at")})
    return out[:30]


def _goal_states(s: Session, ids: list[int]) -> dict[int, str]:
    return dict(s.execute(select(Goal.id, Goal.status).where(Goal.id.in_(ids or [-1]))).all())


def milestone_dict(m: Milestone, goal_states: dict[int, str] | None = None) -> dict[str, Any]:
    states = goal_states or {}
    goals = [{"id": g, "status": states.get(g, "missing")} for g in m.goal_ids or []]
    crit = m.criteria or []
    parts = len(goals) + len(crit)
    done = sum(g["status"] == "approved" for g in goals) + sum(bool(c.get("met")) for c in crit)
    progress = 1.0 if m.status == "completed" else (done / parts if parts else 0.0)
    target = _aware(m.target_date)
    return {"id": m.id, "project_id": m.project_id, "name": m.name, "description": m.description,
            "target_date": _iso(target), "deliverables": m.deliverables or [], "goals": goals, "criteria": crit,
            "owner_agent_id": m.owner_agent_id, "weight": m.weight, "status": m.status, "progress": round(progress, 3),
            "overdue": m.status == "open" and target is not None and target < now(),
            "completed_at": _iso(m.completed_at), "created_at": _iso(m.created_at)}


def _milestone_out(s: Session, m: Milestone) -> dict[str, Any]:
    return milestone_dict(m, _goal_states(s, m.goal_ids or []))


MILESTONE_FIELDS = {"name", "description", "target_date", "deliverables", "goal_ids", "criteria", "owner_agent_id",
                    "weight", "status"}


def _apply_milestone(s: Session, m: Milestone, changes: dict[str, Any]) -> None:
    bad = set(changes) - MILESTONE_FIELDS
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    if "name" in changes:
        if not str(changes["name"] or "").strip():
            raise ValueError("a milestone needs a name")
        m.name = str(changes["name"]).strip()[:256]
    if "description" in changes:
        m.description = str(changes["description"] or "")[:4000]
    if "target_date" in changes:
        m.target_date, m.overdue_notified = _date(changes["target_date"]), False
    if "deliverables" in changes:
        m.deliverables = _strs(changes["deliverables"])
    if "goal_ids" in changes:
        ids = _ints(changes["goal_ids"])
        found = set(s.scalars(select(Goal.id).where(Goal.id.in_(ids or [-1]), Goal.project_id == m.project_id)))
        if missing := [g for g in ids if g not in found]:
            raise ValueError(f"not goals of this project: {', '.join(map(str, missing))}")
        m.goal_ids = ids
    if "criteria" in changes:
        m.criteria = _criteria(changes["criteria"])
    if "owner_agent_id" in changes:
        m.owner_agent_id = int(changes["owner_agent_id"]) if changes["owner_agent_id"] else None
    if "weight" in changes:
        m.weight = max(0.0, min(100.0, float(changes["weight"] or 0)))
    if "status" in changes:
        if changes["status"] not in ("open", "cancelled"):
            raise ValueError("a milestone is reopened or cancelled here; complete it with /complete")
        m.status, m.completed_at = changes["status"], None


def create_milestone(project_id: int, name: str, **fields: Any) -> dict[str, Any]:
    with session() as s:
        _project(s, project_id)
        m = Milestone(project_id=project_id, name="-")
        _apply_milestone(s, m, {"name": name, **fields})
        s.add(m)
        s.flush()
        log_event("milestone", f"milestone '{m.name}' added to project #{project_id}", s=s, milestone_id=m.id)
        return _milestone_out(s, m)


def update_milestone(milestone_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    with session() as s:
        m = s.get(Milestone, milestone_id)
        if m is None:
            raise KeyError(f"milestone {milestone_id}")
        _apply_milestone(s, m, changes)
        return _milestone_out(s, m)


def set_criterion(milestone_id: int, index: int, met: bool, actor: str) -> dict[str, Any]:
    """A person ticks (or unticks) a manual criterion; command criteria are only ever set by running them."""
    with session() as s:
        m = s.get(Milestone, milestone_id)
        if m is None:
            raise KeyError(f"milestone {milestone_id}")
        crit = [dict(c) for c in m.criteria or []]
        if not 0 <= index < len(crit):
            raise KeyError(f"criterion {index}")
        if crit[index]["kind"] != "manual":
            raise ValueError("command criteria are met by running them (Check)")
        crit[index] |= {"met": bool(met), "detail": f"{'met' if met else 'unmet'} according to {actor}"[:2000],
                        "checked_at": now().isoformat()}
        m.criteria = crit
        return _milestone_out(s, m)


def _check_dir(milestone_id: int) -> Path:
    return settings().worktrees_dir / f"milestone-{milestone_id}-check"


def _run_commands(repo: str, milestone_id: int, commands: list[str],
                  project_id: int | None = None) -> list[tuple[bool, str]]:
    """Run the commands in a detached worktree of the project's HEAD (the sandbox), then remove it."""
    from agent_company import sandbox
    from agent_company.contracts import run_command

    if not repo or not (Path(repo) / ".git").exists():
        return [(False, "the project has no git repository to check")] * len(commands)
    path = _check_dir(milestone_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # left over from a run that was cut off
        worktrees.git(repo, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    worktrees.git(repo, "worktree", "prune", check=False)
    try:
        worktrees.git(repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")
        mode = sandbox.mode_for(project_id=project_id)
        return [run_command(c, path, settings().command_timeout, mode=mode) for c in commands]
    except worktrees.WorktreeError as e:
        return [(False, f"could not check out the project: {e}")] * len(commands)
    finally:
        worktrees.git(repo, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)


def check_milestone(milestone_id: int) -> dict[str, Any]:
    """Re-run the milestone's command criteria and record the results."""
    with session() as s:
        m = s.get(Milestone, milestone_id)
        if m is None:
            raise KeyError(f"milestone {milestone_id}")
        repo, project_id = _project(s, m.project_id).path, m.project_id
        commands = [(i, c["text"]) for i, c in enumerate(m.criteria or []) if c["kind"] == "command"]
    results = _run_commands(repo, milestone_id, [c for _, c in commands], project_id) if commands else []
    with session() as s:
        m = s.get(Milestone, milestone_id)
        crit = [dict(c) for c in m.criteria or []]
        for (i, _), (ok, detail) in zip(commands, results, strict=True):
            if i < len(crit):
                crit[i] |= {"met": ok, "detail": detail[-2000:], "checked_at": now().isoformat()}
        m.criteria = crit
        out = _milestone_out(s, m)
    sync(out["project_id"])  # a failed criterion becomes an action item, a met one closes it
    return out


def unmet(m: dict[str, Any]) -> list[str]:
    reasons = [f"goal #{g['id']} is {g['status']}, not approved" for g in m["goals"] if g["status"] != "approved"]
    reasons += [f"criterion not met: {c['text']}" for c in m["criteria"] if c["mandatory"] and not c["met"]]
    return reasons


def complete_milestone(milestone_id: int, actor: str) -> dict[str, Any]:
    """Complete a milestone after re-running its commands; refused while a mandatory criterion is unmet (AT-012)."""
    out = check_milestone(milestone_id)
    if out["status"] != "open":
        raise ValueError(f"the milestone is {out['status']}")
    if reasons := unmet(out):
        raise ValueError("cannot complete the milestone: " + "; ".join(reasons))
    with session() as s:
        m = s.get(Milestone, milestone_id)
        m.status, m.completed_at = "completed", now()
        log_event("milestone", f"milestone '{m.name}' completed by {actor}", s=s, milestone_id=m.id)
        out = _milestone_out(s, m)
    sync(out["project_id"])
    return out


def list_milestones(project_id: int) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(select(Milestone).where(Milestone.project_id == project_id)
                         .order_by(Milestone.status != "open", Milestone.target_date.is_(None), Milestone.target_date,
                                   Milestone.id)).all()
        states = _goal_states(s, [g for m in rows for g in m.goal_ids or []])
        return [milestone_dict(m, states) for m in rows]


# --- risks and blockers (S05-R-02) --------------------------------------------------------

def risk_dict(r: Risk) -> dict[str, Any]:
    return {"id": r.id, "project_id": r.project_id, "goal_id": r.goal_id, "kind": r.kind, "title": r.title,
            "description": r.description, "category": r.category, "severity": r.severity,
            "probability": r.probability, "impact": r.impact, "mitigation": r.mitigation,
            "owner_agent_id": r.owner_agent_id, "status": r.status, "cause": r.cause, "automatic": bool(r.cause),
            "resolved_at": _iso(r.resolved_at), "created_at": _iso(r.created_at)}


RISK_FIELDS = {"kind", "title", "description", "category", "severity", "probability", "impact", "mitigation",
               "owner_agent_id", "status", "goal_id"}


def _apply_risk(r: Risk, changes: dict[str, Any]) -> None:
    bad = set(changes) - RISK_FIELDS
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    if "kind" in changes and changes["kind"] not in ("risk", "blocker"):
        raise ValueError("kind is 'risk' or 'blocker'")
    if "category" in changes and changes["category"] not in RISK_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(RISK_CATEGORIES)}")
    if "severity" in changes and changes["severity"] not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    if "status" in changes and changes["status"] not in RISK_STATUSES:
        raise ValueError(f"status must be one of {', '.join(RISK_STATUSES)}")
    if "title" in changes and not str(changes["title"] or "").strip():
        raise ValueError("a risk needs a title")
    for k, v in changes.items():
        if k == "probability":
            v = None if v in (None, "") else max(0.0, min(1.0, float(v)))
        elif k in ("owner_agent_id", "goal_id"):
            v = int(v) if v else None
        elif isinstance(v, str):
            v = v.strip()[:256 if k == "title" else 4000]
        setattr(r, k, v)
    if r.kind == "blocker":
        r.probability = None  # a blocker has happened; probability is for risks
    if "status" in changes:
        r.resolved_at = now() if r.status in ("resolved", "closed") else None


def create_risk(project_id: int, title: str, **fields: Any) -> dict[str, Any]:
    with session() as s:
        _project(s, project_id)
        r = Risk(project_id=project_id, title="-")
        _apply_risk(r, {"title": title, **fields})
        s.add(r)
        s.flush()
        log_event("risk", f"{r.kind} '{r.title}' recorded for project #{project_id}", goal_id=r.goal_id, s=s,
                  risk_id=r.id)
        return risk_dict(r)


def update_risk(risk_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    with session() as s:
        r = s.get(Risk, risk_id)
        if r is None:
            raise KeyError(f"risk {risk_id}")
        _apply_risk(r, changes)
        return risk_dict(r)


def list_risks(project_id: int, include_closed: bool = False) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Risk).where(Risk.project_id == project_id)
        if not include_closed:
            q = q.where(Risk.status.in_(("open", "mitigated")))
        rows = s.scalars(q.order_by(Risk.id.desc()).limit(200)).all()
        out = [risk_dict(r) for r in rows]
    return sorted(out, key=lambda r: (r["kind"] != "blocker", r["status"] != "open", -SEVERITY_RANK[r["severity"]],
                                      -r["id"]))


def _waiting_since(s: Session, g: Goal) -> datetime:
    at = s.scalar(select(func.max(GoalTransition.created_at)).where(
        GoalTransition.goal_id == g.id, GoalTransition.to_status == "awaiting_approval"))
    return _aware(at or g.updated_at)


def _wanted_blockers(s: Session, project_id: int) -> dict[str, dict[str, Any]]:
    """cause -> the blocker that should be open now."""
    want: dict[str, dict[str, Any]] = {}
    for g in s.scalars(select(Goal).where(Goal.project_id == project_id, Goal.origin != "eval",
                                          Goal.status.in_(("paused", "awaiting_approval")))):
        why = (g.checkpoint or {}).get("paused_for")
        if g.status == "paused" and why in ("budget", "cost_overrun"):
            want[f"goal:{g.id}:budget"] = {
                "goal_id": g.id, "category": "budget", "severity": "high",
                "title": f"Goal #{g.id} paused: {'it cost more than planned' if why == 'cost_overrun' else 'budget used up'}",
                "description": f"{g.title}\n{(g.checkpoint or {}).get('paused_why', '')}".strip(),
                "mitigation": "Raise the budget and resume the goal, or cancel it."}
        if g.status == "paused" and why == "recruitment":  # SPEC-03 S03-R-05
            want[f"goal:{g.id}:recruitment"] = {
                "goal_id": g.id, "category": "staffing", "severity": "medium",
                "title": f"Goal #{g.id} waits for a recruitment (approval or qualification run)",
                "description": f"{g.title}\n{(g.checkpoint or {}).get('paused_why', '')}".strip(),
                "mitigation": "Approve or reject the recruitment, or wait for its candidate's qualification run "
                              "(Evolution → Recruitment)."}
        hours = settings().approval_blocker_hours
        if g.status == "awaiting_approval" and hours and _waiting_since(s, g) < now() - timedelta(hours=hours):
            want[f"goal:{g.id}:approval"] = {
                "goal_id": g.id, "category": "schedule", "severity": "medium",
                "title": f"Goal #{g.id} has waited for approval more than {hours} h", "description": g.title,
                "mitigation": "Approve or reject the merge."}
    # BLOCKER: lines of the open goals' latest iteration tasks (S05-R-08), open until the goal ends or re-plans
    for t, g in s.execute(select(Task, Goal).join(Goal, Task.goal_id == Goal.id).where(
            Goal.project_id == project_id, Goal.origin != "eval", Task.iteration == Goal.iteration,
            Goal.status.not_in(("approved", "rejected", "cancelled", "failed")))).all():
        for n, text in enumerate((t.gate or {}).get("blockers") or []):
            want[f"task:{t.id}:blocker:{n}"] = {
                "goal_id": g.id, "category": "technical", "severity": "medium",
                "title": f"Goal #{g.id}, task '{t.key}': {text}"[:256],
                "description": f"Reported by the task's agent in its output.\n{g.title}",
                "mitigation": "Remove the cause, or re-plan the goal around it."}
    for m in s.scalars(select(Milestone).where(Milestone.project_id == project_id, Milestone.status == "open")):
        for gid, status in _goal_states(s, m.goal_ids or []).items():
            if status in ("failed", "cancelled", "rejected"):
                want[f"milestone:{m.id}:goal:{gid}"] = {
                    "goal_id": gid, "category": "requirements", "severity": "high",
                    "title": f"Milestone '{m.name}' needs goal #{gid}, which is {status}"[:256],
                    "description": "The milestone cannot be completed until the goal is replaced or removed from it.",
                    "mitigation": "Order the work again and add the new goal to the milestone."}
    return want


def _wanted_items(s: Session, project_id: int, manager_id: int | None) -> dict[str, dict[str, Any]]:
    """source -> the action item that should be open now."""
    want: dict[str, dict[str, Any]] = {}
    for p in s.scalars(select(Proposal).where(Proposal.project_id == project_id, Proposal.status == "pending")):
        want[f"proposal:{p.id}"] = {"title": f"Decide proposal: {p.title}", "description": p.description,
                                    "priority": 2, "goal_id": p.goal_id, "responsible_agent_id": manager_id,
                                    "expected_output": "approved (a new goal) or rejected"}
    defects = s.execute(select(Defect, Goal.title).join(Goal, Goal.id == Defect.goal_id).where(
        Goal.project_id == project_id, Defect.status.in_(OPEN_DEFECT),
        # a failed goal's leftovers come back as a new order, not as items that never close
        Goal.status.not_in(("cancelled", "rejected", "failed")), Goal.origin != "eval")).all()
    for d, title in defects:
        want[f"defect:{d.id}"] = {"title": f"Fix {d.severity} defect: {d.check}"[:256],
                                  "description": f"Goal #{d.goal_id} {title}\n{d.detail[:1000]}",
                                  "priority": d.priority or DEFECT_PRIORITY.get(d.severity, 3), "goal_id": d.goal_id,
                                  "expected_output": "the check passes"}
    for r in s.scalars(select(Risk).where(Risk.project_id == project_id, Risk.kind == "blocker", Risk.status == "open")):
        want[f"risk:{r.id}"] = {"title": f"Resolve blocker: {r.title}"[:256], "description": r.mitigation,
                                "priority": 1 if SEVERITY_RANK[r.severity] >= 3 else 2, "goal_id": r.goal_id,
                                "responsible_agent_id": r.owner_agent_id, "expected_output": "the blocker is resolved"}
    for m in s.scalars(select(Milestone).where(Milestone.project_id == project_id, Milestone.status == "open")):
        for i, c in enumerate(m.criteria or []):
            if c.get("checked_at") and not c.get("met") and c.get("mandatory", True):
                want[f"criterion:{m.id}:{i}"] = {"title": f"Meet '{m.name}' criterion: {c['text']}"[:256],
                                                 "description": (c.get("detail") or "")[:1000], "priority": 2,
                                                 "milestone_id": m.id, "due_date": _aware(m.target_date),
                                                 "responsible_agent_id": m.owner_agent_id,
                                                 "expected_output": "the criterion is met"}
    return want


FOLLOW_UP_DAYS = 30


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _follow_ups(s: Session, project_id: int, manager_id: int | None) -> int:
    """`NEXT:` lines of a merged goal's final tasks become action items once (G-R-01): follow-up work after the
    merge. Unlike the derived items they are not closed by `sync`; a person closes them, and a closed one is not
    made again. An open item with the same text is not repeated."""
    made = {src for (src,) in s.execute(select(ActionItem.source).where(ActionItem.project_id == project_id,
                                                                         ActionItem.source.like("next:%")))}
    seen = {_norm(a.title.removeprefix("Follow up: ")) for a in s.scalars(select(ActionItem).where(
        ActionItem.project_id == project_id, ActionItem.status.in_(("open", "in_progress"))))}
    opened = 0
    rows = s.execute(select(Task, Goal).join(Goal, Task.goal_id == Goal.id).where(
        Goal.project_id == project_id, Goal.origin != "eval", Goal.status == "approved",
        Task.iteration == Goal.iteration, Goal.updated_at >= now() - timedelta(days=FOLLOW_UP_DAYS))
        .order_by(Task.id)).all()
    for t, g in rows:
        for n, text in enumerate((t.gate or {}).get("next_actions") or []):
            source, key = f"next:{t.id}:{n}", _norm(str(text))
            if source in made or not key:
                continue
            made.add(source)
            if key in seen:
                continue
            seen.add(key)
            s.add(ActionItem(project_id=project_id, source=source, title=f"Follow up: {text}"[:256],
                             description=f"Named by task '{t.key}' of goal #{g.id} ({g.title}) after it merged.",
                             priority=3, goal_id=g.id, responsible_agent_id=manager_id,
                             expected_output="done, or ordered as a goal"))
            opened += 1
    return opened


def _notify_overdue(s: Session, p: Project) -> None:
    from agent_company.governance import notify

    t = now()
    for m in s.scalars(select(Milestone).where(Milestone.project_id == p.id, Milestone.status == "open",
                                               Milestone.overdue_notified.is_(False), Milestone.target_date.is_not(None))):
        if _aware(m.target_date) < t:
            m.overdue_notified = True
            notify(f"Milestone overdue: {m.name}"[:256], f"Project {p.name}; target {_iso(m.target_date)[:10]}",
                   level="warning", source="milestone", project_id=p.id, s=s)
    for a in s.scalars(select(ActionItem).where(ActionItem.project_id == p.id,
                                                ActionItem.status.in_(("open", "in_progress")),
                                                ActionItem.overdue_notified.is_(False), ActionItem.due_date.is_not(None))):
        if _aware(a.due_date) < t:
            a.overdue_notified = True
            notify(f"Action item overdue: {a.title}"[:256], f"Project {p.name}; due {_iso(a.due_date)[:10]}",
                   level="warning", source="action_item", project_id=p.id, s=s)


def sync(project_id: int | None) -> dict[str, int]:
    """Open the blockers and action items the project's state calls for and close those that no longer hold."""
    counts = {"opened": 0, "closed": 0}
    if not project_id:
        return counts
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            return counts
        want = _wanted_blockers(s, project_id)
        have = {r.cause: r for r in s.scalars(select(Risk).where(Risk.project_id == project_id, Risk.cause != "",
                                                                 Risk.status.in_(("open", "mitigated"))))}
        for cause, r in have.items():
            if cause not in want:
                r.status, r.resolved_at = "resolved", now()
                counts["closed"] += 1
                log_event("risk", f"blocker resolved: {r.title}", goal_id=r.goal_id, s=s, risk_id=r.id)
        for cause, fields in want.items():
            if cause not in have:
                s.add(Risk(project_id=project_id, kind="blocker", cause=cause, owner_agent_id=p.manager_id, **fields))
                counts["opened"] += 1
                log_event("risk", f"blocker opened: {fields['title']}", goal_id=fields.get("goal_id"), s=s)
        s.flush()
        want_items = _wanted_items(s, project_id, p.manager_id)
        items = {a.source: a for a in s.scalars(select(ActionItem).where(
            ActionItem.project_id == project_id, ActionItem.source != "",
            ActionItem.status.in_(("open", "in_progress"))))}
        for source, a in items.items():
            if source not in want_items and not source.startswith("next:"):
                a.status = "done"
                counts["closed"] += 1
        for source, fields in want_items.items():
            if source not in items:
                s.add(ActionItem(project_id=project_id, source=source, **fields))
                counts["opened"] += 1
        counts["opened"] += _follow_ups(s, project_id, p.manager_id)
        _notify_overdue(s, p)
    return counts


# --- upcoming action items (S05-R-04) -----------------------------------------------------

def item_dict(a: ActionItem, names: dict[int, str] | None = None) -> dict[str, Any]:
    return {"id": a.id, "project_id": a.project_id, "title": a.title, "description": a.description,
            "responsible_agent_id": a.responsible_agent_id,
            "responsible": (names or {}).get(a.responsible_agent_id) if a.responsible_agent_id else None,
            "priority": a.priority, "due_date": _iso(a.due_date), "depends_on": a.depends_on or [],
            "expected_output": a.expected_output, "status": a.status, "goal_id": a.goal_id,
            "milestone_id": a.milestone_id, "source": a.source, "automatic": bool(a.source),
            "overdue": a.status in ("open", "in_progress") and a.due_date is not None and _aware(a.due_date) < now(),
            "created_at": _iso(a.created_at)}


ITEM_FIELDS = {"title", "description", "responsible_agent_id", "priority", "due_date", "depends_on",
               "expected_output", "status", "goal_id", "milestone_id"}


def _apply_item(a: ActionItem, changes: dict[str, Any]) -> None:
    bad = set(changes) - ITEM_FIELDS
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    if "status" in changes and changes["status"] not in ITEM_STATUSES:
        raise ValueError(f"status must be one of {', '.join(ITEM_STATUSES)}")
    if "title" in changes and not str(changes["title"] or "").strip():
        raise ValueError("an action item needs a title")
    for k, v in changes.items():
        if k == "priority":
            v = max(1, min(5, int(v)))
        elif k == "due_date":
            v = _date(v)
            a.overdue_notified = False
        elif k == "depends_on":
            v = [x for x in _ints(v) if x != a.id]
        elif k in ("responsible_agent_id", "goal_id", "milestone_id"):
            v = int(v) if v else None
        elif isinstance(v, str):
            v = v.strip()[:256 if k == "title" else 4000]
        setattr(a, k, v)


def create_item(project_id: int, title: str, **fields: Any) -> dict[str, Any]:
    with session() as s:
        _project(s, project_id)
        a = ActionItem(project_id=project_id, title="-")
        _apply_item(a, {"title": title, **fields})
        s.add(a)
        s.flush()
        return item_dict(a, dict(s.execute(select(Agent.id, Agent.name)).all()))


def update_item(item_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    with session() as s:
        a = s.get(ActionItem, item_id)
        if a is None:
            raise KeyError(f"action item {item_id}")
        _apply_item(a, changes)
        return item_dict(a, dict(s.execute(select(Agent.id, Agent.name)).all()))


def ranked_items(project_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """Open action items in the GM's order: ones whose dependencies are done first, then priority, due date and
    risk (the severity of the blocker they resolve, or of the goal's worst open blocker)."""
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        rows = s.scalars(select(ActionItem).where(ActionItem.project_id == project_id,
                                                  ActionItem.status.in_(("open", "in_progress")))).all()
        risks = s.scalars(select(Risk).where(Risk.project_id == project_id, Risk.status == "open")).all()
    open_ids = {a.id for a in rows}
    by_id = {r.id: SEVERITY_RANK[r.severity] for r in risks}
    by_goal: dict[int, int] = {}
    for r in risks:
        if r.goal_id:
            by_goal[r.goal_id] = max(by_goal.get(r.goal_id, 0), SEVERITY_RANK[r.severity])

    def risk(a: ActionItem) -> int:
        if a.source.startswith("risk:"):
            return by_id.get(int(a.source.split(":")[1]), 0)
        return by_goal.get(a.goal_id or 0, 0)

    order = sorted(rows, key=lambda a: (any(d in open_ids for d in a.depends_on or []), a.status != "in_progress",
                                        a.priority, _aware(a.due_date) or FAR, -risk(a), a.id))
    return [item_dict(a, names) | {"rank": i, "blocked_by": [d for d in a.depends_on or [] if d in open_ids]}
            for i, a in enumerate(order[:limit], 1)]


# --- insights (S05-R-03) ------------------------------------------------------------------

_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def parse_insights(output: str) -> list[dict[str, Any]]:
    """Insights from fenced JSON blocks holding {"insights": [...]}; other JSON in the output is ignored."""
    found: list[dict[str, Any]] = []
    for block in _FENCED.findall(output or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        items = data.get("insights") if isinstance(data, dict) else None
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and str(it.get("title", "")).strip():
                found.append(it)
    return found[:INSIGHTS_PER_TASK]


def _confidence(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def extract_insights(goal_id: int, task_id: int | None, agent_id: int | None, output: str) -> int:
    found = parse_insights(output)
    if not found:
        return 0
    with session() as s:
        g = s.get(Goal, goal_id)
        for it in found:
            s.add(Insight(project_id=g.project_id if g else None, goal_id=goal_id, task_id=task_id, agent_id=agent_id,
                          title=str(it["title"]).strip()[:256], description=str(it.get("description", ""))[:4000],
                          evidence=_strs(it.get("evidence")), sources=_strs(it.get("sources")),
                          expected_benefit=str(it.get("expected_benefit", ""))[:2000],
                          estimated_cost=str(it.get("estimated_cost", ""))[:128],
                          confidence=_confidence(it.get("confidence", 0.5)),
                          related_tasks=[f"task:{task_id}"] if task_id else []))
        log_event("insight", f"{len(found)} insight(s) reported", goal_id=goal_id, agent_id=agent_id, s=s)
    return len(found)


def insight_dict(i: Insight, names: dict[int, str] | None = None) -> dict[str, Any]:
    return {"id": i.id, "project_id": i.project_id, "goal_id": i.goal_id, "task_id": i.task_id,
            "agent_id": i.agent_id, "agent": (names or {}).get(i.agent_id) if i.agent_id else None, "title": i.title,
            "description": i.description, "evidence": i.evidence or [], "sources": i.sources or [],
            "expected_benefit": i.expected_benefit, "estimated_cost": i.estimated_cost, "confidence": i.confidence,
            "related_tasks": i.related_tasks or [], "status": i.status, "decision": i.decision,
            "decided_by": i.decided_by, "result": i.result, "knowledge_id": i.knowledge_id,
            "proposal_id": i.proposal_id, "created_at": _iso(i.created_at)}


def list_insights(project_id: int, status: str = "") -> list[dict[str, Any]]:
    with session() as s:
        q = select(Insight).where(Insight.project_id == project_id)
        if status:
            q = q.where(Insight.status == status)
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        return [insight_dict(i, names) for i in s.scalars(q.order_by(Insight.id.desc()).limit(200))]


def _allowed_step(current: str, target: str) -> bool:
    if target == "rejected":
        return current in INSIGHT_CHAIN[:3]
    if target not in INSIGHT_CHAIN or current not in INSIGHT_CHAIN:
        return False
    i, j = INSIGHT_CHAIN.index(current), INSIGHT_CHAIN.index(target)
    # forward only; nothing is implemented or evaluated before it was accepted
    return j > i and (j <= INSIGHT_CHAIN.index("accepted") or i >= INSIGHT_CHAIN.index("accepted"))


def _knowledge(s: Session, i: Insight) -> int | None:
    """An accepted insight as a knowledge item, screened and classified like a lesson (S11-R-01, S14-R-05).
    Written directly: an insight is not a task outcome, so it earns no XP and does not steer evolution."""
    from agent_company.learning import screen

    p = s.get(Project, i.project_id) if i.project_id else None
    agent_id = i.agent_id or (p.manager_id if p else None)
    if agent_id is None or s.get(Agent, agent_id) is None:
        return None
    classification = (p.classification if p else None) or "internal"
    text = f"{i.title}: {i.description}".strip()[:1000]
    status, reason = screen(text)
    if classification == "secret":
        text, status, reason = "(withheld: the project is classified secret)", "rejected", "secret project"
    sources = [f"insight:{i.id}"] + ([f"goal:{i.goal_id}"] if i.goal_id else []) + list(i.related_tasks or [])
    sources += [x for x in i.sources or [] if x not in sources]
    item = Lesson(agent_id=agent_id, goal_id=i.goal_id, text=text, outcome="success", kind="semantic",
                  evidence_class="INFERENCE", project_id=i.project_id, task_id=i.task_id, sources=sources[:20],
                  status=status, status_reason=reason, classification=classification)
    s.add(item)
    s.flush()
    if status != "approved":
        log_event("security", f"insight #{i.id} as knowledge {status}: {reason}", goal_id=i.goal_id, s=s,
                  rule="memory_poisoning")
    return item.id


def advance_insight(insight_id: int, status: str, actor: str, decision: str = "", result: str = "",
                    as_proposal: bool = False) -> dict[str, Any]:
    with session() as s:
        i = s.get(Insight, insight_id)
        if i is None:
            raise KeyError(f"insight {insight_id}")
        if not _allowed_step(i.status, status):
            raise ValueError(f"an insight that is {i.status} cannot become {status}")
        i.status = status
        if decision:
            i.decision = decision[:4000]
        if result:
            i.result = result[:4000]
        if status in ("accepted", "rejected"):
            i.decided_by = actor[:128]
        if status == "accepted":
            i.knowledge_id = _knowledge(s, i)
            if as_proposal and i.goal_id:
                prop = Proposal(goal_id=i.goal_id, project_id=i.project_id, agent_id=i.agent_id, title=i.title,
                                description=f"{i.description}\n\nFrom insight #{i.id}. {i.expected_benefit}".strip())
                s.add(prop)
                s.flush()
                i.proposal_id = prop.id
        log_event("insight", f"insight #{i.id} {status} by {actor}", goal_id=i.goal_id, s=s, insight_id=i.id)
        out = insight_dict(i, dict(s.execute(select(Agent.id, Agent.name)).all()))
    if out["proposal_id"]:
        if settings().auto_approve_proposals:  # owner policy: a follow-up from an insight, after the session
            from agent_company import ownerpolicy

            ownerpolicy.approve_pending(ids=[out["proposal_id"]])
        sync(out["project_id"])
    return out


# --- replacing a project's General Manager (S05-R-09) --------------------------------------

def gm_handover(s: Session, p: Project, new_id: int, actor: str, reason: str = "") -> dict[str, Any]:
    """Inside the open session that replaces `p.manager_id`: the handover package (open goals, open blockers and
    open action items), written as a `project_handovers` row and a `handover_to` link old → new; the old GM's open
    blockers and action items move to the new one. Call `sync(p.id)` before opening the session so the blockers
    and generated items are current."""
    from agent_company import registry
    from agent_company.workflow import FINAL

    old_id = p.manager_id
    goals = [{"id": g.id, "title": g.title, "status": g.status, "iteration": g.iteration}
             for g in s.scalars(select(Goal).where(Goal.project_id == p.id, Goal.origin != "eval",
                                                   Goal.status.not_in(FINAL)).order_by(Goal.id))]
    blockers = s.scalars(select(Risk).where(Risk.project_id == p.id, Risk.kind == "blocker", Risk.status == "open")
                         .order_by(Risk.id)).all()
    items = s.scalars(select(ActionItem).where(ActionItem.project_id == p.id,
                                               ActionItem.status.in_(("open", "in_progress"))).order_by(ActionItem.id)).all()
    package = {"goals": goals,
               "blockers": [{"id": r.id, "title": r.title, "severity": r.severity, "category": r.category,
                             "goal_id": r.goal_id} for r in blockers],
               "action_items": [{"id": a.id, "title": a.title, "priority": a.priority, "status": a.status,
                                 "due_date": _iso(a.due_date), "goal_id": a.goal_id} for a in items]}
    moved = 0
    if old_id:
        for r in blockers:
            if r.owner_agent_id == old_id:
                r.owner_agent_id, moved = new_id, moved + 1
        for a in items:
            if a.responsible_agent_id == old_id:
                a.responsible_agent_id, moved = new_id, moved + 1
        registry.connect(s, old_id, new_id, "handover_to", 0.1, evidence=f"project:{p.id}:gm")
    package["reassigned"] = moved
    h = ProjectHandover(project_id=p.id, from_agent_id=old_id, to_agent_id=new_id, package=package,
                        actor=actor[:128], reason=reason)
    s.add(h)
    s.flush()
    log_event("handover", f"project {p.name}: GM handover #{h.id} with {len(goals)} open goal(s), "
              f"{len(blockers)} blocker(s), {len(items)} action item(s)", s=s, project_id=p.id,
              from_agent_id=old_id, to_agent_id=new_id)
    return handover_dict(h)


def handover_dict(h: ProjectHandover, names: dict[int, str] | None = None) -> dict[str, Any]:
    names = names or {}
    return {"id": h.id, "project_id": h.project_id, "from_agent_id": h.from_agent_id,
            "from_agent": names.get(h.from_agent_id or 0), "to_agent_id": h.to_agent_id,
            "to_agent": names.get(h.to_agent_id or 0), "package": h.package or {}, "actor": h.actor,
            "reason": h.reason, "at": _iso(h.created_at)}


def list_handovers(project_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        return [handover_dict(h, names) for h in s.scalars(
            select(ProjectHandover).where(ProjectHandover.project_id == project_id)
            .order_by(ProjectHandover.id.desc()).limit(limit))]


# --- time and weighted progress (S05-R-05) ------------------------------------------------

def goal_time(goal_id: int) -> dict[str, float]:
    """Seconds of model/agent runs (active), queued or waiting for approval (waiting) and paused (blocked), and the
    planner's estimate: Σ `estimated_minutes` of the latest iteration's task contracts (0 = none given)."""
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(f"goal {goal_id}")
        estimated = 60.0 * sum(int((t.contract or {}).get("estimated_minutes", 0) or 0) for t in s.scalars(
            select(Task).where(Task.goal_id == goal_id, Task.iteration == g.iteration)))
        active = s.scalar(select(func.coalesce(func.sum(Run.duration), 0.0)).where(Run.goal_id == goal_id)) or 0.0
        steps = s.scalars(select(GoalTransition).where(GoalTransition.goal_id == goal_id)
                          .order_by(GoalTransition.id)).all()
        start, status = _aware(g.created_at), "queued"
        spans: dict[str, float] = {}
        for t in steps:
            at = _aware(t.created_at)
            spans[status] = spans.get(status, 0.0) + max(0.0, (at - start).total_seconds())
            start, status = at, t.to_status
        if status == g.status:
            spans[status] = spans.get(status, 0.0) + max(0.0, (now() - start).total_seconds())
    return {"active": round(float(active), 1),
            "waiting": round(spans.get("queued", 0.0) + spans.get("awaiting_approval", 0.0), 1),
            "blocked": round(spans.get("paused", 0.0), 1), "estimated": estimated}


def progress(project_id: int) -> dict[str, Any]:
    """Σ(weight × completion) / Σ weights over the project's goals and milestones. A goal weighs the sum of its
    latest iteration's task weights (contract `weight`, default 1; 1 before it is planned); failed, cancelled and
    rejected goals and benchmark goals do not count."""
    parts: list[tuple[float, float]] = []
    with session() as s:
        for g in s.scalars(select(Goal).where(Goal.project_id == project_id, Goal.origin != "eval",
                                              Goal.status.not_in(("failed", "cancelled", "rejected")))):
            tasks = s.scalars(select(Task).where(Task.goal_id == g.id, Task.iteration == g.iteration)).all()
            weights = [float((t.contract or {}).get("weight", 1) or 1) for t in tasks]
            weight = sum(weights) or 1.0
            if g.status == "approved":
                done = 1.0
            elif tasks:
                done = sum(w for w, t in zip(weights, tasks, strict=True) if t.status in ("done", "skipped")) / weight
                done *= 0.9  # the gate and the merge are still ahead
            else:
                done = 0.0
            parts.append((weight, done))
    for m in list_milestones(project_id):
        if m["status"] != "cancelled":
            parts.append((float(m["weight"]), m["progress"]))
    total = sum(w for w, _ in parts)
    return {"progress": round(sum(w * d for w, d in parts) / total, 3) if total else 0.0, "parts": len(parts)}


def overview(project_id: int) -> dict[str, Any]:
    """Everything the Projects view's Plan tab shows, after a sync."""
    sync(project_id)
    return {"milestones": list_milestones(project_id), "risks": list_risks(project_id),
            "insights": list_insights(project_id), "action_items": ranked_items(project_id),
            "progress": progress(project_id), "handovers": list_handovers(project_id, 5),
            "categories": RISK_CATEGORIES, "severities": SEVERITIES}


def status_lines(project_id: int, items: int = 5) -> tuple[list[str], list[str]]:
    """(open blockers, next action items) for /status and the stand-up."""
    sync(project_id)
    blockers = [f"! {r['title']} ({r['severity']}, {r['category']})" for r in list_risks(project_id)
                if r["kind"] == "blocker" and r["status"] == "open"]
    nxt = [f"{a['rank']}. {a['title']}" + (f" (due {a['due_date'][:10]})" if a["due_date"] else "")
           + (" [overdue]" if a["overdue"] else "") for a in ranked_items(project_id, items)]
    return blockers, nxt
