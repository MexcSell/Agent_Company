"""The mission report of a finished goal (REQ-004): objective, plan, tasks done/failed/retried, verification evidence,
cost, time, remaining limitations, lessons and recommendations.

Each task's label (verified|partial|failed) comes only from what the platform recorded: the task's acceptance results
(`Task.gate["acceptance"]`, checked by the loop) and the review verdicts of the quality gate
(`Event(kind="gate").data["checks"]`, rows `review <key>`). An agent's output text is never read for it (ADR 0017).
  failed   = the task failed or was cancelled, a recorded check of it failed, or a review of it is FAIL
  verified = done, every recorded check passed, every review of it is PASS, and at least one of them exists
  partial  = everything else (done without recorded evidence, skipped, ...)
A review verdict is recorded per reviewer task, so it is attributed to the tasks the reviewer's contract inputs name,
else to its `depends_on`, else to the writing tasks of its iteration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from agent_company.db import (Defect, Event, Goal, GoalTransition, Lesson, Risk, Run, Task, event_iteration,
                               log_event, now, session)

REVIEW_ROLES = {"qa", "security", "network_security"}  # quality.REVIEW_ROLES


def _aware(t: datetime | None) -> datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=now().tzinfo)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _gates(s: Any, goal_id: int) -> dict[int, list[dict[str, Any]]]:
    """The last recorded gate's checks per iteration (its `iteration` field, else the one in its message)."""
    out: dict[int, list[dict[str, Any]]] = {}
    for e in s.scalars(select(Event).where(Event.goal_id == goal_id, Event.kind == "gate").order_by(Event.id)):
        data = e.data or {}
        if "checks" not in data:
            continue
        it = event_iteration(e)
        if it is None:
            continue
        out[it] = [c for c in data["checks"] if isinstance(c, dict)]
    return out


def _input_keys(contract: dict[str, Any], keys: set[str]) -> list[str]:
    found: list[str] = []
    for v in ((contract or {}).get("inputs") or {}).values() if isinstance((contract or {}).get("inputs"), dict) else []:
        for x in v if isinstance(v, list) else [v]:
            if isinstance(x, str) and x in keys and x not in found:
                found.append(x)
    return found


def _reviewed(reviewer: Task, tasks: list[Task]) -> list[str]:
    """The task keys a reviewer's verdict applies to: contract inputs → depends_on → the iteration's writing tasks."""
    keys = {t.key for t in tasks if t.key != reviewer.key}
    named = _input_keys(reviewer.contract or {}, keys)
    if named:
        return named
    deps = [str(k) for k in reviewer.depends_on or [] if str(k) in keys]
    if deps:
        return deps
    return [t.key for t in tasks if t.writes and t.key != reviewer.key and t.role not in REVIEW_ROLES]


def label(status: str, evidence: list[dict[str, Any]]) -> str:
    if status in ("failed", "cancelled") or any(not e["passed"] for e in evidence):
        return "failed"
    return "verified" if status == "done" and evidence else "partial"


def report(goal_id: int) -> dict[str, Any]:
    """The mission report of a finished goal; ValueError while it is still active, KeyError for an unknown one."""
    from agent_company import budgets, performance, workflow

    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(f"goal {goal_id}")
        if g.status in workflow.ACTIVE:
            raise ValueError(f"goal {goal_id} is not finished")
        tasks = list(s.scalars(select(Task).where(Task.goal_id == goal_id).order_by(Task.iteration, Task.id)))
        gates = _gates(s, goal_id)
        runs = dict(s.execute(select(Run.task_id, func.count()).where(Run.goal_id == goal_id, Run.task_id.is_not(None))
                              .group_by(Run.task_id)).all())
        tokens, worked = s.execute(select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0),
                                          func.coalesce(func.sum(Run.duration), 0.0)).where(Run.goal_id == goal_id)).one()
        ended = s.scalars(select(GoalTransition.created_at).where(
            GoalTransition.goal_id == goal_id, GoalTransition.from_status.in_(workflow.ACTIVE),
            GoalTransition.to_status.not_in(workflow.ACTIVE)).order_by(GoalTransition.id.desc())).first()
        defects = [{"check": d.check, "severity": d.severity, "status": d.status}
                   for d in s.scalars(select(Defect).where(Defect.goal_id == goal_id,
                                                           Defect.status.in_(performance.OPEN)))]
        blockers = [r.title for r in s.scalars(select(Risk).where(Risk.goal_id == goal_id, Risk.kind == "blocker",
                                                                  Risk.status == "open"))]
        lessons = [{"id": x.id, "kind": x.kind, "text": x.text[:500], "status": x.status}
                   for x in s.scalars(select(Lesson).where(Lesson.goal_id == goal_id).order_by(Lesson.id))
                   if x.status != "rejected" and not (x.report or {}).get("rca")]

        by_iter: dict[int, list[Task]] = {}
        for t in tasks:
            by_iter.setdefault(t.iteration, []).append(t)
        evidence: dict[int, list[dict[str, Any]]] = {t.id: [] for t in tasks}
        for t in tasks:  # acceptance results the loop recorded for the task
            for c in (t.gate or {}).get("acceptance", []) or []:
                if isinstance(c, dict) and "passed" in c:
                    evidence[t.id].append({"kind": "check", "check": str(c.get("type") or c.get("check") or "acceptance"),
                                           "passed": bool(c["passed"]), "detail": str(c.get("detail", ""))[:300]})
        checks: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        for it, rows in sorted(gates.items()):
            its = by_iter.get(it, [])
            by_key = {t.key: t for t in its}
            for c in rows:
                name, passed = str(c.get("check", "")), bool(c.get("passed"))
                if name.startswith("review ") and name[7:] in by_key:
                    reviewer = by_key[name[7:]]
                    targets = _reviewed(reviewer, its)
                    reviews.append({"iteration": it, "reviewer": reviewer.key, "role": reviewer.role,
                                    "passed": passed, "detail": str(c.get("detail", ""))[:300], "reviewed": targets})
                    for k in targets:
                        evidence[by_key[k].id].append({"kind": "review", "check": name, "passed": passed,
                                                       "detail": str(c.get("detail", ""))[:300]})
                    continue
                checks.append({"iteration": it, "check": name, "passed": passed, "stage": c.get("stage", ""),
                               "detail": str(c.get("detail", ""))[:300]})
                if name.startswith("task ") and name[5:] in by_key and not passed:
                    evidence[by_key[name[5:]].id].append({"kind": "check", "check": name, "passed": False,
                                                          "detail": str(c.get("detail", ""))[:300]})

        iterations_of: dict[str, set[int]] = {}
        for t in tasks:
            iterations_of.setdefault(t.key, set()).add(t.iteration)
        retried = sorted({t.key for t in tasks if len(iterations_of[t.key]) > 1 or runs.get(t.id, 0) > 1})
        items = [{"key": t.key, "title": t.title, "role": t.role, "iteration": t.iteration, "status": t.status,
                  "label": label(t.status, evidence[t.id]), "evidence": evidence[t.id],
                  "runs": int(runs.get(t.id, 0))} for t in tasks]
        plan = [{"iteration": it, "tasks": [{"key": t.key, "title": t.title, "role": t.role,
                                             "depends_on": list(t.depends_on or [])} for t in ts]}
                for it, ts in sorted(by_iter.items())]
        created = _aware(g.created_at)
        finished = _aware(ended) or _aware(g.updated_at)
        deadline = _aware(g.deadline)
        head = {"goal_id": g.id, "title": g.title, "status": g.status, "project_id": g.project_id,
                "iterations": g.iteration, "objective": g.description or g.title}
    spend = budgets.summary(goal_id)
    last = gates.get(max(gates)) if gates else []
    limitations = ([f"open defect: {d['check']} ({d['severity']}, {d['status']})" for d in defects]
                   + [f"failed check: {c['check']}" for c in last if not c.get("passed")]
                   + [f"open blocker: {b}" for b in blockers]
                   + [f"task {i['key']} ({i['iteration']}) has no recorded passing check or review"
                      for i in items if i["label"] == "partial"])
    from agent_company import rca

    recommendations = ([f"{a.get('title', '')}: {a.get('why', '')}".strip(": ")
                        for x in rca.list_analyses(goal_id=goal_id) for a in x.get("actions") or []]
                       + [f"fix the open defect '{d['check']}'" for d in defects]
                       + [f"verify task {i['key']}: add an acceptance check or a review"
                          for i in items if i["label"] == "partial"])
    return {**head, "plan": plan,
            "tasks": {"done": sum(i["status"] == "done" for i in items),
                      "failed": sum(i["status"] in ("failed", "cancelled") for i in items),
                      "retried": retried, "items": items,
                      "labels": {k: sum(i["label"] == k for i in items) for k in ("verified", "partial", "failed")}},
            "verification": {"checks": checks, "reviews": reviews},
            "cost": {"usd": spend["used"]["cost_usd"], "tokens": int(tokens),
                     "budget_usd": spend["limits"].get("cost_usd", 0.0)},
            "time": {"created": _iso(created), "finished": _iso(finished),
                     "duration": round(max(0.0, (finished - created).total_seconds()), 1) if finished else None,
                     "active": round(float(worked), 1), "deadline": _iso(deadline),
                     "met": (finished <= deadline) if deadline and finished else None},
            "limitations": limitations, "lessons": lessons, "recommendations": recommendations}


LABELS = ("verified", "partial", "failed")


def label_counts(goal_id: int) -> dict[str, int]:
    """report()'s task labels of a finished goal, stored in Goal.label_counts so that the Simple overview and RCA do
    not rebuild the report; computed and stored on the first read, a failed store still returns the counts.
    KeyError/ValueError as report()."""
    from agent_company import workflow

    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(f"goal {goal_id}")
        if g.status in workflow.ACTIVE:
            raise ValueError(f"goal {goal_id} is not finished")
        if g.label_counts:
            return {k: int(g.label_counts.get(k, 0)) for k in LABELS}
    counts = _count(goal_id)
    try:
        _write(goal_id, counts)
    except Exception as e:  # noqa: BLE001 - e.g. "database is locked": the read still gets its counts
        _log_not_stored(goal_id, e)
    return counts


def _count(goal_id: int) -> dict[str, int]:
    labels = report(goal_id)["tasks"]["labels"]
    return {k: int(labels.get(k, 0)) for k in LABELS}


def _write(goal_id: int, counts: dict[str, int]) -> None:
    """Store the counts unless the goal is gone or active again (resumed after its counts were computed)."""
    from agent_company import workflow

    with session() as s:  # opened after report() closed its own: SQLite allows one writer
        g = s.get(Goal, goal_id)
        if g is not None and g.status not in workflow.ACTIVE:
            g.label_counts = counts


def _store(goal_id: int) -> dict[str, int]:
    counts = _count(goal_id)
    _write(goal_id, counts)
    return counts


def _log_not_stored(goal_id: int, e: Exception) -> None:
    try:
        log_event("error", f"label counts of goal #{goal_id} not stored: {type(e).__name__}: {e}", goal_id=goal_id)
    except Exception:  # noqa: BLE001
        pass


def store_label_counts(goal_id: int) -> None:
    """Recompute and overwrite a finished goal's stored label counts; fail-open, never raises."""
    try:
        _store(goal_id)
    except Exception as e:  # noqa: BLE001 - the cache never changes a goal's outcome
        _log_not_stored(goal_id, e)


def report_markdown(d: dict[str, Any]) -> str:
    t, c, tm = d["tasks"], d["cost"], d["time"]
    met = {True: "met", False: "missed", None: "no deadline"}[tm["met"]]
    out = [f"# Mission report: goal #{d['goal_id']} {d['title']}", "", f"Status: {d['status']}", "",
           "## Objective", "", d["objective"], "", "## Plan", ""]
    for p in d["plan"]:
        out += [f"- Iteration {p['iteration']}: " + ", ".join(f"{x['key']} ({x['role']})" for x in p["tasks"])]
    out += ["", "## Tasks", "",
            f"{t['done']} done, {t['failed']} failed, retried: {', '.join(t['retried']) or 'none'}", ""]
    out += [f"- [{i['label']}] {i['key']} (iteration {i['iteration']}, {i['status']}): {i['title']}" for i in t["items"]]
    out += ["", "## Verification", ""]
    out += [f"- {'PASS' if x['passed'] else 'FAIL'} {x['check']} (iteration {x['iteration']})"
            for x in d["verification"]["checks"]]
    out += [f"- review {x['reviewer']}: {'PASS' if x['passed'] else 'FAIL'} for {', '.join(x['reviewed']) or '-'}"
            f" (iteration {x['iteration']})" for x in d["verification"]["reviews"]]
    out += ["", "## Cost", "", f"${c['usd']:.4f} of ${c['budget_usd']:.2f} budget" if c["budget_usd"]
            else f"${c['usd']:.4f} (no budget)", f"{c['tokens']:,} tokens", "",
            "## Time", "", f"Created {tm['created']}, finished {tm['finished']}, {tm['duration']} s "
            f"({tm['active']} s of agent work); deadline {tm['deadline'] or '-'}: {met}", ""]
    for title, key in (("Limitations", "limitations"), ("Lessons", "lessons"), ("Recommendations", "recommendations")):
        rows = [x["text"] if isinstance(x, dict) else x for x in d[key]]
        out += [f"## {title}", ""] + ([f"- {x}" for x in rows] or ["- none"]) + [""]
    return "\n".join(out)
