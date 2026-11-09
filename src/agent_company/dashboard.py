"""AICO UI-DASH: what the Dashboard (Company Control Hub) shows for a period, read from the tables that hold it.

Figures are counted, never estimated: a trend is given only where the earlier period can be counted the same way
(`prev`), and a project's phase (UI-D-06) is derived from its stored status and its goals, not stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from agent_company.db import Agent, Goal, Run, Task, now, session

PERIODS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
LIVE = ("queued", "planning", "running", "recovering", "paused", "awaiting_approval")
PHASES = ("draft", "planning", "active", "blocked", "review", "release_ready", "paused", "completed", "archived",
          "cancelled")


def _iso(t: datetime | None) -> str | None:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()


def phase(status: str, goal_states: list[str], open_blockers: int, milestones: list[dict[str, Any]]) -> str:
    """UI-D-06: the lifecycle phase shown for a project. Stored statuses other than active keep their meaning."""
    if status != "active":
        return status if status in PHASES else "active"
    live = [g for g in goal_states if g in LIVE]
    if open_blockers:
        return "blocked"
    if "awaiting_approval" in live:
        return "review"
    if live and all(g in ("queued", "planning") for g in live):
        return "planning"
    open_ms = [m for m in milestones if m["status"] != "cancelled"]
    if open_ms and all(m["status"] == "completed" or m["progress"] >= 1 for m in open_ms) and not live:
        return "release_ready"
    if not goal_states and not milestones:
        return "draft"
    return "active"


def _projects(user: dict[str, Any]) -> list[dict[str, Any]]:
    from agent_company import management, projects
    from agent_company.db import Risk

    out = []
    with session() as s:
        rows = s.scalars(projects.visible(user).where(projects.Project.status.in_(("active", "paused")))
                         .order_by(projects.Project.id)).all()
        for p in rows:
            states = list(s.scalars(select(Goal.status).where(Goal.project_id == p.id, Goal.origin != "eval")))
            blockers = s.scalar(select(func.count()).select_from(Risk).where(
                Risk.project_id == p.id, Risk.kind == "blocker", Risk.status == "open")) or 0
            team = s.scalar(select(func.count(func.distinct(Task.agent_id))).join(Goal, Goal.id == Task.goal_id)
                            .where(Goal.project_id == p.id, Goal.status.in_(LIVE), Task.agent_id.is_not(None))) or 0
            out.append({"id": p.id, "name": p.name, "status": p.status, "states": states, "blockers": blockers,
                        "team": team, "running": sum(g in LIVE for g in states),
                        "deadline": _iso(p.deadline), "priority": p.priority})
    rank = {p: i for i, p in enumerate(reversed(projects.PRIORITIES))}  # the highest priority first
    out.sort(key=lambda x: (rank.get(x["priority"] or "normal", 0), x["id"]))
    for x in out:
        ms = management.list_milestones(x["id"])
        x["phase"] = phase(x.pop("status"), x.pop("states"), x.pop("blockers"), ms)
        x["progress"] = management.progress(x["id"])["progress"]
        nxt = sorted((m for m in ms if m["status"] == "open"), key=lambda m: (m["target_date"] is None,
                                                                             m["target_date"] or ""))
        x["next_milestone"] = {"name": nxt[0]["name"], "target_date": nxt[0]["target_date"],
                               "overdue": nxt[0]["overdue"]} if nxt else None
    return out


def _count_window(s: Any, start: datetime, end: datetime) -> dict[str, Any]:
    spend = s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(Run.started_at >= start,
                                                                         Run.started_at < end)) or 0.0
    finished = dict(s.execute(select(Task.status, func.count()).where(
        Task.finished_at >= start, Task.finished_at < end, Task.status.in_(("done", "failed")))
        .group_by(Task.status)).all())
    merged = s.scalar(select(func.count()).select_from(Goal).where(
        Goal.status == "approved", Goal.updated_at >= start, Goal.updated_at < end)) or 0
    return {"spend": round(float(spend), 6), "tasks_done": finished.get("done", 0),
            "tasks_failed": finished.get("failed", 0), "goals_merged": merged}


def report(user: dict[str, Any], period: str = "24h", visible_projects: set[int] | None = None) -> dict[str, Any]:
    from agent_company import registry

    span = PERIODS.get(period, PERIODS["24h"])
    end = now()
    start, prev_start = end - span, end - 2 * span
    seen = (lambda pid: True) if visible_projects is None else (lambda pid: pid is None or pid in visible_projects)
    with session() as s:
        cur, prev = _count_window(s, start, end), _count_window(s, prev_start, start)
        agents = dict(s.execute(select(Agent.status, func.count()).where(Agent.enabled)
                                .group_by(Agent.status)).all())
        running_tasks = s.scalar(select(func.count()).select_from(Task).where(Task.status == "running")) or 0
        blocked_tasks = s.scalar(select(func.count()).select_from(Task).join(Goal, Goal.id == Task.goal_id).where(
            Task.stage == "blocked", Goal.status.in_(LIVE))) or 0
        # live goals with the current iteration's tasks: the browser derives the lifecycle step (companymap.js)
        live = []
        for g in s.scalars(select(Goal).where(Goal.status.in_(LIVE), Goal.origin != "eval")
                           .order_by(Goal.id.desc()).limit(20)):
            if not seen(g.project_id):
                continue
            ts = s.execute(select(Task.role, Task.stage, Task.status).where(
                Task.goal_id == g.id, Task.iteration == g.iteration)).all()
            live.append({"id": g.id, "title": g.title, "status": g.status, "project_id": g.project_id,
                         "iteration": g.iteration,
                         "tasks": [{"role": r, "stage": st, "status": x} for r, st, x in ts]})
        tokens = s.execute(select(Run.model_name, func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0),
                                  func.count(), func.coalesce(func.sum(Run.cost), 0.0))
                           .where(Run.started_at >= start).group_by(Run.model_name)).all()
        rooms = s.execute(select(Agent.room_key, Task.status, func.count()).join(Agent, Agent.id == Task.agent_id)
                          .where(Task.finished_at >= start, Task.status.in_(("done", "failed")))
                          .group_by(Agent.room_key, Task.status)).all()
        done = []
        for g in s.scalars(select(Goal).where(Goal.status.in_(("approved", "failed")), Goal.origin != "eval",
                                              Goal.updated_at >= start).order_by(Goal.updated_at.desc()).limit(8)):
            if seen(g.project_id):
                done.append({"id": g.id, "title": g.title, "status": g.status, "project_id": g.project_id,
                             "at": _iso(g.updated_at)})
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.enabled)
                       .order_by(Agent.superior_id.is_not(None), Agent.id)).first()
        gm_out = None
        if gm is not None:
            gm_out = {"id": gm.id, "name": gm.name, "status": gm.status, "health": gm.health,
                      "pref": gm.model_pref or ""}
    if gm_out:
        gm_out["model"] = registry.first_model("general_manager", gm_out.pop("pref")) or ""
    perf: dict[str, dict[str, int]] = {}
    for room, st, n in rooms:
        perf.setdefault(room or "unassigned", {"done": 0, "failed": 0})[st] += n
    total_tokens = sum(int(t) for _, t, _, _ in tokens) or 0
    return {
        "period": period if period in PERIODS else "24h", "since": _iso(start), "now": _iso(end),
        "current": cur, "previous": prev,
        "agents": {"busy": agents.get("busy", 0), "registered": sum(agents.values())},
        "tasks": {"running": running_tasks, "blocked": blocked_tasks},
        "projects": _projects(user), "live_goals": live, "completed": done, "general_manager": gm_out,
        "models": sorted(({"name": m or "unknown", "tokens": int(t), "runs": int(r), "cost": round(float(c), 6),
                           "share": round(int(t) / total_tokens, 4) if total_tokens else 0.0}
                          for m, t, r, c in tokens), key=lambda x: -x["tokens"]),
        "rooms": sorted(({"room": k} | v for k, v in perf.items()), key=lambda x: -(x["done"] + x["failed"])),
    }
