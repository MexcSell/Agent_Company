"""G5 Control Hub UI-G5-SW: Swarm Control, the live topology Mission -> Team -> Agent -> Task.

A mission is a goal (G5-D-03); its swarm is the team working its current iteration (G5-D-04): teams are the rooms of
the agents its tasks are staffed with. Everything is read from stored records — tasks, runs (tool calls, isolation
level, cost), live executor processes, the approval queue and the goal's events — never simulated (G5 spec Part 16:
"the visualization must represent actual runtime state").
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func, select

from agent_company.dashboard import LIVE, _iso
from agent_company.db import Agent, Event, Goal, LiveProcess, Room, Run, Task, session

# goal events a person may want to see on the mission node: policy, approval and recovery
POLICY_EVENTS = ("security", "escalation", "budget", "risk", "retry", "critic", "error", "command_refused", "team")
RECENT_TOOLS = 8


def pattern(execution: str, agents: int, rooms: int) -> dict[str, str]:
    """UI-G5-SW-05: the coordination pattern (link 6af21ead §3): one agent, a sequential (hierarchical) team, or a
    parallel swarm across rooms; its template's execution mode decides parallel vs sequential."""
    if agents == 0:
        return {"key": "forming", "label": "Forming", "why": "no task is staffed yet"}
    if agents == 1:
        return {"key": "single", "label": "Single agent", "why": "one agent works the mission"}
    if execution == "sequential":
        return {"key": "hierarchical", "label": "Hierarchical team",
                "why": "the General Manager hands tasks over one at a time (sequential template)"}
    if rooms > 1:
        return {"key": "parallel", "label": "Parallel swarm",
                "why": f"{agents} agents from {rooms} teams work independent tasks at once"}
    return {"key": "collaborative", "label": "Collaborative team", "why": f"{agents} agents of one team"}


def _mission(s: Any, g: Goal, names: dict[int, Agent], rooms: dict[str, str], live_pids: dict[tuple, list[int]],
             waiting: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = list(s.scalars(select(Task).where(Task.goal_id == g.id, Task.iteration == g.iteration).order_by(Task.id)))
    earlier = Counter(k for (k,) in s.execute(select(Task.key).where(Task.goal_id == g.id, Task.iteration < g.iteration)))
    runs = list(s.scalars(select(Run).where(Run.goal_id == g.id).order_by(Run.id)))
    by_agent: dict[int, list[Run]] = defaultdict(list)
    for r in runs:
        if r.agent_id is not None:
            by_agent[r.agent_id].append(r)
    agent_ids = sorted({t.agent_id for t in tasks if t.agent_id is not None})
    agents = []
    for aid in agent_ids:
        a = names.get(aid)
        own = by_agent.get(aid, [])
        calls = [c for r in own for c in (r.tool_calls or []) if isinstance(c, dict)]
        last = own[-1] if own else None
        agents.append({
            "id": aid, "name": a.name if a else f"agent #{aid}", "role": a.role if a else "",
            "team": (a.room_key if a else "") or "unassigned", "status": a.status if a else "",
            "health": a.health if a else "", "autonomy": a.autonomy if a else "",
            "model": last.model_name if last else "", "isolation": last.isolation if last else "",
            "running": bool(live_pids.get((aid, g.id))), "runs": len(own),
            "cost": round(sum(r.cost or 0.0 for r in own), 6),
            "tokens": sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in own),
            "tool_calls": len(calls), "tool_failures": sum(1 for c in calls if c.get("ok") is False),
            "recent_tools": [{"tool": c.get("tool", ""), "ok": c.get("ok") is not False,
                              "input": str(c.get("input", ""))[:160]} for c in calls[-RECENT_TOOLS:]],
            "denied_tools": sorted({str(d) for r in own for d in (r.denied_tools or [])})[:10],
            "last_error": (last.error or "")[:300] if last and not last.ok else "",
        })
    team_keys = sorted({x["team"] for x in agents})
    teams = [{"key": k, "name": rooms.get(k, k.replace("_", " ")),
              "agents": [x["id"] for x in agents if x["team"] == k]} for k in team_keys]
    task_out = []
    for t in tasks:
        verdicts = [c.get("verdict") for c in (t.critic or []) if isinstance(c, dict)]
        task_out.append({
            "id": t.id, "key": t.key, "title": t.title, "role": t.role, "agent_id": t.agent_id,
            "status": t.status, "stage": t.stage, "writes": t.writes, "depends_on": list(t.depends_on or []),
            "retries": earlier.get(t.key, 0), "critic": verdicts, "model": t.model_name,
            "started_at": _iso(t.started_at), "finished_at": _iso(t.finished_at),
            "output": (t.output or "")[-600:],
        })
    events = [{"kind": e.kind, "message": e.message[:240], "at": _iso(e.created_at)} for e in s.scalars(
        select(Event).where(Event.goal_id == g.id, Event.kind.in_(POLICY_EVENTS)).order_by(Event.id.desc()).limit(8))]
    rooms_n = len(team_keys)
    execution = (g.strategy or {}).get("execution") or "parallel"
    return {
        "id": g.id, "title": g.title, "status": g.status, "project_id": g.project_id, "kind": g.kind,
        "iteration": g.iteration, "max_iterations": g.max_iterations, "origin": g.origin,
        "paused_for": (g.checkpoint or {}).get("paused_for") or "", "deadline": _iso(g.deadline),
        "budget_usd": g.budget_usd, "spent_usd": round(sum(r.cost or 0.0 for r in runs), 6),
        "tokens": sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in runs),
        "budget_tokens": g.budget_tokens, "max_active_agents": g.max_active_agents,
        "execution": execution, "pattern": pattern(execution, len(agents), rooms_n),
        "teams": teams, "agents": agents, "tasks": task_out,
        "edges": [[d, t.key] for t in tasks for d in (t.depends_on or [])],
        "approvals": [w for w in waiting if w.get("goal_id") == g.id],
        "events": events,
        "failed": sum(1 for t in tasks if t.status == "failed"),
        "recovered": sum(1 for t in tasks if t.status == "done" and earlier.get(t.key)),
    }


def topology(user: dict[str, Any], seen: set[int] | None = None, recent: int = 0,
             goal_id: int | None = None) -> dict[str, Any]:
    """Every live mission's swarm (and the `recent` last finished ones), or one mission with `goal_id`."""
    from agent_company import approvals

    ok = (lambda pid: True) if seen is None else (lambda pid: pid is None or pid in seen)
    waiting = []
    for w in approvals.queue(user, seen):
        gid = w["id"] if w["kind"] in ("goal", "plan", "questions") else None
        if gid is None and w["link"].startswith("#/goals/"):
            gid = int(w["link"].rsplit("/", 1)[-1]) if w["link"].rsplit("/", 1)[-1].isdigit() else None
        waiting.append(w | {"goal_id": gid})
    with session() as s:
        names = {a.id: a for a in s.scalars(select(Agent))}
        rooms = dict(s.execute(select(Room.key, Room.name)).all())
        live_pids: dict[tuple, list[int]] = defaultdict(list)
        for p in s.scalars(select(LiveProcess)):
            live_pids[(p.agent_id, p.goal_id)].append(p.pid)
        q = select(Goal).where(Goal.origin != "eval")
        if goal_id is not None:
            goals = [g for g in s.scalars(q.where(Goal.id == goal_id)) if ok(g.project_id)]
        else:
            goals = [g for g in s.scalars(q.where(Goal.status.in_(LIVE)).order_by(Goal.id.desc()).limit(30))
                     if ok(g.project_id)]
            if recent:
                goals += [g for g in s.scalars(q.where(Goal.status.not_in(LIVE)).order_by(Goal.updated_at.desc())
                                               .limit(max(0, min(recent, 20)))) if ok(g.project_id)]
        missions = [_mission(s, g, names, rooms, live_pids, waiting) for g in goals]
        idle = s.scalar(select(func.count()).select_from(Agent).where(Agent.enabled, Agent.status == "idle")) or 0
    working = {a["id"] for m in missions if m["status"] in LIVE for a in m["agents"]}
    return {
        "missions": missions,
        "summary": {"missions": sum(1 for m in missions if m["status"] in LIVE), "agents": len(working),
                    "running_now": sum(1 for m in missions for a in m["agents"] if a["running"]),
                    "idle_agents": idle,
                    "tool_calls": sum(a["tool_calls"] for m in missions for a in m["agents"]),
                    "tool_failures": sum(a["tool_failures"] for m in missions for a in m["agents"]),
                    "approvals": sum(len(m["approvals"]) for m in missions),
                    "failed_tasks": sum(m["failed"] for m in missions)},
    }
