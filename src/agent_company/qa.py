"""AICO UI-QA-01/02: Evaluation & QA figures from what the quality gate recorded.

Every gate run is an event (`kind="gate"`, data {iteration, passed, checks, commit}); each check names its stage
(contract, unit, static, security, integration, review). Counts are of those records only: executions passed or
failed per day, goals waiting for their first gate (pending), blocked tasks, checks whose result changed on the same
commit (flaky: the same code passed and failed), open defects by priority, and per goal the latest gate by area
(functional = contract + unit, integration, security, final acceptance = review verdicts, static analysis).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from agent_company.dashboard import LIVE, PERIODS
from agent_company.db import Event, Goal, Task, now, session

AREAS = {"functional": ("contract", "unit"), "integration": ("integration",), "security": ("security",),
         "acceptance": ("review",), "static": ("static",)}


def _iso(t: datetime | None) -> str | None:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()


def _area(stage: str) -> str:
    return next((a for a, stages in AREAS.items() if stage in stages), "other")


def report(period: str = "7d", visible_projects: set[int] | None = None) -> dict[str, Any]:
    from agent_company import performance

    span = PERIODS.get(period, PERIODS["7d"])
    start = now() - span
    with session() as s:
        goals = {g.id: g for g in s.scalars(select(Goal).where(Goal.origin != "eval"))
                 if visible_projects is None or g.project_id is None or g.project_id in visible_projects}
        events = [e for e in s.scalars(select(Event).where(Event.kind == "gate", Event.created_at >= start)
                                       .order_by(Event.id)) if e.goal_id in goals and "checks" in (e.data or {})]
        blocked_n = sum(1 for gid in s.scalars(select(Task.goal_id).where(Task.stage == "blocked"))
                        if gid in goals and goals[gid].status in LIVE)
        per_day: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "gates": 0, "gates_passed": 0})
        by_stage: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0})
        seen: dict[tuple[int, str, str], set[bool]] = defaultdict(set)
        latest: dict[int, Event] = {}
        for e in events:
            day = _iso(e.created_at)[:10]
            per_day[day]["gates"] += 1
            per_day[day]["gates_passed"] += bool(e.data.get("passed"))
            for c in e.data.get("checks") or []:
                ok = bool(c.get("passed"))
                per_day[day]["passed" if ok else "failed"] += 1
                by_stage[c.get("stage") or "other"]["passed" if ok else "failed"] += 1
                if e.data.get("commit"):
                    seen[(e.goal_id, str(e.data["commit"]), str(c.get("check")))].add(ok)
            latest[e.goal_id] = e
        flaky = sorted({check for (gid, commit, check), results in seen.items() if len(results) == 2})
        gated = set(s.scalars(select(Event.goal_id).where(Event.kind == "gate").distinct()))
        pending = [g for g in goals.values() if g.status in LIVE and g.id not in gated]  # no gate run yet
        rows = []
        for gid, e in sorted(latest.items(), key=lambda x: -x[1].id):
            g = goals[gid]
            areas: dict[str, dict[str, Any]] = {}
            for c in e.data.get("checks") or []:
                a = areas.setdefault(_area(c.get("stage") or ""), {"passed": 0, "failed": 0, "failing": []})
                if c.get("passed"):
                    a["passed"] += 1
                else:
                    a["failed"] += 1
                    a["failing"].append({"check": c.get("check"), "detail": str(c.get("detail") or "")[:300]})
            rows.append({"goal_id": gid, "title": g.title, "status": g.status, "project_id": g.project_id,
                         "iteration": e.data.get("iteration"), "passed": bool(e.data.get("passed")),
                         "commit": e.data.get("commit"), "at": _iso(e.created_at), "areas": areas})
    passed = sum(d["passed"] for d in per_day.values())
    failed = sum(d["failed"] for d in per_day.values())
    defects = [d for d in performance.list_defects("open") if d["goal_id"] in goals]
    by_prio: dict[str, int] = defaultdict(int)
    for d in defects:
        by_prio[f"P{d['priority']}"] += 1
    return {"period": period if period in PERIODS else "7d", "since": _iso(start),
            "executions": {"total": passed + failed, "passed": passed, "failed": failed, "blocked": blocked_n,
                           "pending": len(pending)},
            "gates": {"total": len(events), "passed": sum(bool(e.data.get("passed")) for e in events)},
            "days": [{"day": k} | v for k, v in sorted(per_day.items())],
            "stages": [{"stage": k} | v for k, v in sorted(by_stage.items())],
            "flaky": flaky[:50], "defects": {"open": len(defects), "by_priority": dict(sorted(by_prio.items()))},
            "goals": rows[:100]}
