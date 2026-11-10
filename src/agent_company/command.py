"""G5 Control Hub UI-G5-CC: what the Command Center adds to the Dashboard's figures (dashboard.py) for a period.

The nine operational metrics of the G5 specification, alerts and risks, and the resource and budget monitor. Every
figure is counted from stored records; a rate carries its sample size `n` and is None without samples, and its
`basis` says whether it covers the period or all recorded work (docs/requirements/G5-CONTROL-HUB.md).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from agent_company.dashboard import LIVE, PERIODS, _iso
from agent_company.db import Agent, Event, Experiment, Goal, Incident, ModelEntry, Risk, Room, Run, Task, now, session

FINISHED = ("approved", "failed", "rejected")


def _rate(hits: int, n: int, basis: str = "period") -> dict[str, Any]:
    return {"rate": round(hits / n, 3) if n else None, "hits": hits, "n": n, "basis": basis}


def _visible(seen: set[int] | None):  # noqa: ANN202
    return (lambda pid: True) if seen is None else (lambda pid: pid is None or pid in seen)


def metrics(s: Any, start: Any, end: Any, seen: set[int] | None = None, ops: dict[str, Any] | None = None
            ) -> dict[str, Any]:
    """The nine operational metrics (link 6af191fa §9) over [start, end); benchmark goals left out. The all-time
    ones come from `performance.operations` (`ops`, computed once by the caller)."""
    from agent_company import performance

    ok = _visible(seen)
    goals = [g for g in s.scalars(select(Goal).where(Goal.origin != "eval", Goal.status.in_(FINISHED),
                                                     Goal.updated_at >= start, Goal.updated_at < end))
             if ok(g.project_id)]
    accepted = [g for g in goals if g.status == "approved"]
    ids = [g.id for g in goals] or [-1]
    # recovery: of the finished missions that had a failed or skipped task, the share that still ended accepted
    troubled = set(s.scalars(select(Task.goal_id).where(Task.goal_id.in_(ids), Task.status.in_(("failed", "skipped")))))
    tasks = dict(s.execute(select(Task.status, func.count()).join(Goal, Goal.id == Task.goal_id).where(
        Goal.origin != "eval", Task.finished_at >= start, Task.finished_at < end,
        Task.status.in_(("done", "failed"))).group_by(Task.status)).all())
    spend = float(s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(
        Run.started_at >= start, Run.started_at < end)) or 0.0)
    busy = float(s.scalar(select(func.coalesce(func.sum(Run.duration), 0.0)).where(
        Run.started_at >= start, Run.started_at < end, Run.agent_id.is_not(None))) or 0.0)
    enabled = s.scalar(select(func.count()).select_from(Agent).where(Agent.enabled)) or 0
    capacity = enabled * max(1.0, (end - start).total_seconds())
    ops = ops or performance.operations(s)
    last = next((e for e in s.scalars(select(Experiment).where(Experiment.status.in_(
        ("review", "canary", "rolled_out", "rolled_back", "rejected"))).order_by(Experiment.updated_at.desc()))
        if isinstance((e.results or {}).get("comparison"), dict)), None)
    cmp = (last.results or {}).get("comparison", {}) if last else {}
    review, retry = ops["review_rejection_rate"], ops["retry_rate"]
    rework_n = (review["n"] or 0) + (retry["n"] or 0)
    return {
        "mission_success_rate": _rate(len(accepted), len(goals)),
        "task_completion_rate": _rate(tasks.get("done", 0), tasks.get("done", 0) + tasks.get("failed", 0)),
        "validation_score": ops["gate_pass_rate"] | {"basis": "all"},
        "cost_per_successful_mission": {"usd": round(spend / len(accepted), 4) if accepted else None,
                                        "n": len(accepted), "basis": "period"},
        "agent_utilisation": {"rate": round(min(1.0, busy / capacity), 4) if capacity and enabled else None,
                              "busy_seconds": round(busy, 1), "agents": enabled, "n": enabled, "basis": "period"},
        "rework_rate": _rate((review["hits"] or 0) + (retry["hits"] or 0), rework_n, "all"),
        "recovery_rate": _rate(len(troubled & {g.id for g in accepted}), len(troubled)),
        "improvement_gain": {"pass_rate_change": cmp.get("pass_rate_change"), "experiment_id": last.id if last else None,
                             "target": last.target if last else None, "better": cmp.get("better"),
                             "n": (cmp.get("samples") or {}).get("candidate") or 0, "basis": "latest experiment"},
        "approval_wait": ops["approval_latency"] | {"basis": "all"},
    }


def alerts(s: Any, start: Any, seen: set[int] | None = None, admin: bool = True) -> list[dict[str, Any]]:
    """Alerts & risks, the most severe first: stops, incidents, degraded agents, limited models, open blockers,
    paused missions waiting for a person, failed missions in the period and failed external-source syncs."""
    from agent_company import governance

    ok = _visible(seen)
    out: list[dict[str, Any]] = []

    def add(level: str, kind: str, title: str, link: str, at: Any = None) -> None:
        out.append({"level": level, "kind": kind, "title": title, "link": link, "at": _iso(at)})

    if level := governance.stop_level():
        add("critical", "stop", f"Emergency stop {level} is on", "#/security")
    if admin:
        for i in s.scalars(select(Incident).where(Incident.status != "closed").order_by(Incident.id.desc()).limit(20)):
            add("critical" if i.severity in ("SEV-1", "SEV-2") else "warning", "incident",
                f"{i.severity} incident: {i.title}", "#/security", i.created_at)
    for a in s.scalars(select(Agent).where(Agent.enabled, Agent.health == "degraded")):
        add("warning", "agent", f"{a.name} is degraded: {(a.health_reason or '')[:120]}", f"#/agents/{a.id}",
            a.degraded_at)
    t = now()
    for m in s.scalars(select(ModelEntry).where(ModelEntry.enabled, ModelEntry.limited_until.is_not(None))):
        until = m.limited_until if m.limited_until.tzinfo else m.limited_until.replace(tzinfo=t.tzinfo)
        if until > t:
            add("warning", "model", f"{m.name} is at its usage limit until {until.strftime('%H:%M UTC')}", "#/models")
    for r in s.scalars(select(Risk).where(Risk.kind == "blocker", Risk.status == "open").order_by(Risk.id.desc())
                       .limit(20)):
        if ok(r.project_id):
            add("critical" if r.severity == "critical" else "warning", "blocker", f"Blocker: {r.title}",
                f"#/projects/{r.project_id}", r.created_at)
    for g in s.scalars(select(Goal).where(Goal.status == "paused", Goal.origin != "eval").order_by(Goal.id.desc())):
        why = (g.checkpoint or {}).get("paused_for") or ""
        if ok(g.project_id) and why in ("blocker", "infrastructure", "budget", "cost_overrun", "quota"):
            add("warning", "mission", f"Mission #{g.id} paused ({why}): {g.title}", f"#/goals/{g.id}", g.updated_at)
    for g in s.scalars(select(Goal).where(Goal.status == "failed", Goal.origin != "eval", Goal.updated_at >= start)
                       .order_by(Goal.id.desc()).limit(10)):
        if ok(g.project_id):
            add("warning", "mission", f"Mission #{g.id} failed: {g.title}", f"#/goals/{g.id}", g.updated_at)
    for e in s.scalars(select(Event).where(Event.kind == "source_sync", Event.created_at >= start)
                       .order_by(Event.id.desc()).limit(50)):
        if (e.data or {}).get("ok") is False:
            add("warning", "source", e.message[:200], "#/knowledge", e.created_at)
    rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda x: (rank[x["level"]], x["at"] is None, "" if x["at"] is None else x["at"]))[:40]


def budget(s: Any, admin: bool = True) -> dict[str, Any]:
    """Spend per day over the last seven days against the daily room budgets; tokens today; the host (admins)."""
    from agent_company import system

    t = now()
    day0 = t.replace(hour=0, minute=0, second=0, microsecond=0)
    days = []
    for i in range(6, -1, -1):
        a, b = day0 - timedelta(days=i), day0 - timedelta(days=i - 1)
        cost = float(s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(
            Run.started_at >= a, Run.started_at < b)) or 0.0)
        days.append({"day": a.date().isoformat(), "usd": round(cost, 4)})
    daily = float(s.scalar(select(func.coalesce(func.sum(Room.daily_budget_usd), 0.0)).where(
        Room.enabled, Room.parent_key.is_(None))) or 0.0)
    tokens = int(s.scalar(select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0)).where(
        Run.started_at >= day0)) or 0)
    out: dict[str, Any] = {"days": days, "daily_budget_usd": round(daily, 2), "today_usd": days[-1]["usd"],
                           "tokens_today": tokens}
    if admin:
        host = system.status()["host"]
        out["host"] = {k: host.get(k) for k in ("cpus", "load", "memory_total", "memory_available", "disk_total",
                                                 "disk_free")}
    return out


def center(user: dict[str, Any], period: str = "24h", seen: set[int] | None = None) -> dict[str, Any]:
    span = PERIODS.get(period, PERIODS["24h"])
    end = now()
    start, prev_start = end - span, end - 2 * span
    admin = bool(user.get("is_admin"))
    ok = _visible(seen)
    with session() as s:
        live = [g for g in s.scalars(select(Goal).where(Goal.status.in_(LIVE), Goal.origin != "eval")) if ok(g.project_id)]
        from agent_company import performance

        ops = performance.operations(s)
        cur, prev = metrics(s, start, end, seen, ops), metrics(s, prev_start, start, seen, ops)
        al = alerts(s, start, seen, admin)
        money = budget(s, admin)
        busy = s.scalar(select(func.count()).select_from(Agent).where(Agent.enabled, Agent.status == "busy")) or 0
        enabled = s.scalar(select(func.count()).select_from(Agent).where(Agent.enabled)) or 0
    states: dict[str, int] = {}
    for g in live:
        states[g.status] = states.get(g.status, 0) + 1
    return {
        "period": period if period in PERIODS else "24h", "since": _iso(start),
        "missions": {"active": len(live), "by_status": states},
        "agents": {"busy": busy, "enabled": enabled},
        "metrics": cur, "previous": prev, "alerts": al,
        "critical": sum(1 for a in al if a["level"] == "critical"), "budget": money,
    }
