"""Agent Registry: rooms, agents, hierarchy, relationships and staffing."""

from __future__ import annotations

import re
import threading
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from agent_company import org
from agent_company.db import Agent, Relationship, Room, log_event, now, session

_staff_lock = threading.Lock()


def agent_dict(a: Agent) -> dict[str, Any]:
    return {
        "id": a.id, "name": a.name, "role": a.role, "title": a.title, "room": a.room_key,
        "superior_id": a.superior_id, "parent_id": a.parent_id, "goal": a.goal,
        "skills": a.skills, "permissions": a.permissions, "model_pref": a.model_pref,
        "status": a.status, "enabled": a.enabled, "xp": a.xp, "level": a.level,
        "tasks_done": a.tasks_done, "tasks_failed": a.tasks_failed, "playbook": a.playbook,
        "lifecycle": a.lifecycle, "playbook_version": a.playbook_version, "in_trial": bool(a.experiment),
        "plugins": list(a.plugins or []), "definition_version": a.definition_version or 0,
        "last_active_at": a.last_active_at.isoformat() if a.last_active_at else None,
    }


def seed() -> None:
    """Create the rooms and one agent per catalog role. Idempotent."""
    with session() as s:
        have_rooms = set(s.scalars(select(Room.key)))
        for key, name, division, desc, rot in org.ROOMS:
            if key not in have_rooms:
                s.add(Room(key=key, name=name, division=division, description=desc, rotational=rot))
        s.flush()
        by_role: dict[str, Agent] = {}
        for spec in org.ROLES.values():
            a = s.scalars(select(Agent).where(Agent.role == spec.role, Agent.parent_id.is_(None))
                          .order_by(Agent.id)).first()
            if a is None:
                a = Agent(name=spec.title, role=spec.role, title=spec.title, room_key=spec.room,
                          goal=spec.goal, backstory=spec.backstory, skills=dict(spec.skills),
                          permissions=list(spec.permissions), enabled=spec.enabled)
                s.add(a)
                s.flush()
            by_role[spec.role] = a
        for spec in org.ROLES.values():
            a = by_role[spec.role]
            if spec.superior and a.superior_id is None:
                a.superior_id = by_role[spec.superior].id
    with session() as s:
        for a in s.scalars(select(Agent)).all():
            _link_similar(s, a)


def similarity(a: Agent, b: Agent) -> float:
    """0..1: same role counts most, then shared skills, then same room."""
    sa, sb = set(a.skills or {}), set(b.skills or {})
    jaccard = len(sa & sb) / len(sa | sb) if sa | sb else 0.0
    score = 0.5 * (a.role == b.role) + 0.4 * jaccard + 0.1 * (a.room_key == b.room_key)
    return round(score, 3)


def _link_similar(s: Session, agent: Agent, k: int = 3, threshold: float = 0.2) -> None:
    others = [o for o in s.scalars(select(Agent).where(Agent.id != agent.id)).all()]
    ranked = sorted(((similarity(agent, o), o) for o in others), key=lambda t: -t[0])[:k]
    for score, other in ranked:
        if score < threshold:
            continue
        a_id, b_id = sorted((agent.id, other.id))
        rel = s.scalars(select(Relationship).where(
            Relationship.a_id == a_id, Relationship.b_id == b_id, Relationship.kind == "similar")).first()
        if rel is None:
            s.add(Relationship(a_id=a_id, b_id=b_id, kind="similar", weight=score))


def related(s: Session, agent_id: int, limit: int = 5) -> list[tuple[Agent, float]]:
    rels = s.scalars(select(Relationship).where(
        or_(Relationship.a_id == agent_id, Relationship.b_id == agent_id))
        .order_by(Relationship.weight.desc())).all()
    out: dict[int, float] = {}
    for r in rels:
        other = r.b_id if r.a_id == agent_id else r.a_id
        out[other] = max(out.get(other, 0.0), r.weight)
    ids = sorted(out, key=lambda i: -out[i])[:limit]
    agents = {a.id: a for a in s.scalars(select(Agent).where(Agent.id.in_(ids))).all()}
    return [(agents[i], out[i]) for i in ids if i in agents]


def connect(s: Session, a_id: int, b_id: int, kind: str = "collaborated", inc: float = 0.1,
            evidence: str | None = None) -> None:
    """Strengthen (or, with a negative `inc`, weaken) a link. With `evidence`, the same evidence never
    changes the link twice (duplicate records are ignored, SPEC-12 AR-015)."""
    if a_id == b_id:
        return
    lo, hi = sorted((a_id, b_id))
    rel = s.scalars(select(Relationship).where(
        Relationship.a_id == lo, Relationship.b_id == hi, Relationship.kind == kind)).first()
    if rel is None:
        if inc <= 0:
            return  # nothing to weaken
        s.add(Relationship(a_id=lo, b_id=hi, kind=kind, weight=min(1.0, 0.3 + inc),
                           evidence=[evidence] if evidence else [], updated_at=now()))
        return
    seen = list(rel.evidence or [])
    if evidence:
        if evidence in seen:
            return
        rel.evidence = [*seen, evidence][-200:]
    rel.weight = max(0.0, min(1.0, rel.weight + inc))
    rel.updated_at = now()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "specialist"


def _skill_score(agent: Agent, wanted: list[str], team: dict[int, float] | None = None) -> float:
    """Team utility of one candidate (SPEC12 §7.3): skill coverage + experience + collaboration
    evidence with the agents already on the goal + quality (success rate)."""
    skills = agent.skills or {}
    done, failed = agent.tasks_done or 0, agent.tasks_failed or 0
    quality = done / (done + failed) if done + failed else 0.5
    return (sum(skills.get(w, 0) for w in wanted) + 0.05 * agent.level + 2.0 * (team or {}).get(agent.id, 0.0)
            + 1.0 * quality)


def _team_weights(s: Session, goal_id: int | None) -> dict[int, float]:
    """For each agent: summed 'collaborated' weight with the agents already working on this goal."""
    if goal_id is None:
        return {}
    from agent_company.db import Task

    team = set(s.scalars(select(Task.agent_id).where(Task.goal_id == goal_id, Task.agent_id.is_not(None))))
    if not team:
        return {}
    out: dict[int, float] = {}
    for r in s.scalars(select(Relationship).where(Relationship.kind == "collaborated",
                                                  or_(Relationship.a_id.in_(team), Relationship.b_id.in_(team)))):
        for me, other in ((r.a_id, r.b_id), (r.b_id, r.a_id)):
            if other in team and me not in team:
                out[me] = out.get(me, 0.0) + r.weight
    return out


def resolve_role(role: str) -> tuple[str, str | None]:
    """Map a planner role onto the catalog. Returns (role, note)."""
    role = _slug(role)
    spec = org.ROLES.get(role)
    if spec is None or spec.enabled:
        return role, None
    with session() as s:
        if s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "active")).first():
            return role, None
    fallback = org.FALLBACK_WRITE_ROLE if spec.writes else org.FALLBACK_READ_ROLE
    return fallback, f"role '{role}' is disabled; using '{fallback}'"


def staff(role: str, skills: list[str] | None = None, goal_id: int | None = None,
          writes: bool | None = None) -> Agent:
    """Return an agent for the role and mark it busy.

    Picks the best idle enabled agent by skill score. If all are busy the best one
    is cloned; if the role does not exist a new specialist is recruited and linked
    to similar agents so it can draw on their experience.
    """
    skills = [_slug(x) for x in (skills or [])]
    with _staff_lock, session() as s:
        pool = s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "active")).all()
        idle = [a for a in pool if a.status == "idle"]
        team = _team_weights(s, goal_id)
        if idle:
            agent = max(idle, key=lambda a: _skill_score(a, skills, team))
            action = "assigned"
        elif pool:
            agent = _clone(s, max(pool, key=lambda a: _skill_score(a, skills, team)))
            action = "cloned"
        elif s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "paused")).first():
            # an emergency stop (E1/E4) or an administrator paused this role's agents: recruiting a
            # replacement would walk around the stop
            raise RuntimeError(f"every {role} agent is paused; lift the stop or resume one first")
        else:
            agent = _recruit(s, role, skills, writes)
            action = "recruited"
        agent.status = "busy"
        agent.last_active_at = now()
        s.flush()
        s.expunge(agent)
    log_event("staff", f"{action} {agent.name} as {role}", goal_id=goal_id, agent_id=agent.id)
    return agent


def _clone(s: Session, src: Agent) -> Agent:
    base = s.get(Agent, src.parent_id) if src.parent_id else src
    n = s.scalar(select(func.count()).select_from(Agent).where(Agent.parent_id == base.id)) or 0
    name = f"{base.name} #{n + 2}"
    while s.scalars(select(Agent).where(Agent.name == name)).first():
        n += 1
        name = f"{base.name} #{n + 2}"
    clone = Agent(name=name, role=base.role, title=base.title, room_key=base.room_key,
                  superior_id=base.superior_id, parent_id=base.id, goal=base.goal,
                  backstory=base.backstory, skills=dict(base.skills), permissions=list(base.permissions),
                  model_pref=base.model_pref, playbook=base.playbook, enabled=True)
    s.add(clone)
    s.flush()
    connect(s, clone.id, base.id, "similar", 0.7)
    return clone


def _recruit(s: Session, role: str, skills: list[str], writes: bool | None) -> Agent:
    title = role.replace("_", " ").title() + " Specialist"
    perms = ["read", "write", "shell"] if writes else ["read"]
    gm = s.scalars(select(Agent).where(Agent.role == "general_manager")).first()
    lead = s.scalars(select(Agent).where(Agent.role == ("architect" if writes else "research"))).first()
    name, n = title, 1
    while s.scalars(select(Agent).where(Agent.name == name)).first():
        n += 1
        name = f"{title} #{n}"
    agent = Agent(name=name, role=role, title=title,
                  room_key="development" if writes else "information_collection",
                  superior_id=(lead or gm).id if (lead or gm) else None,
                  goal=f"Handle {role.replace('_', ' ')} work to a high standard",
                  backstory=f"You are a newly recruited {title.lower()}. Draw on the experience "
                            "of related agents shown in your context.",
                  skills={k: 5 for k in skills} or {role: 5}, permissions=perms, enabled=True)
    s.add(agent)
    s.flush()
    _link_similar(s, agent, threshold=0.05)  # a recruit always learns from its closest peers
    return agent


def release(agent_id: int) -> None:
    name = None
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is not None and a.status == "busy":
            a.status = "idle"
            name = a.name
    if name:
        from agent_company import hooks

        hooks.emit("TeammateIdle", agent_id=agent_id, agent=name)


def list_agents() -> list[dict[str, Any]]:
    with session() as s:
        return [agent_dict(a) for a in s.scalars(select(Agent).order_by(Agent.id)).all()]


def list_rooms() -> list[dict[str, Any]]:
    with session() as s:
        rooms = s.scalars(select(Room).order_by(Room.id)).all()
        counts = dict(s.execute(select(Agent.room_key, func.count()).group_by(Agent.room_key)).all())
        return [{"key": r.key, "name": r.name, "division": r.division, "description": r.description,
                 "rotational": r.rotational, "agents": counts.get(r.key, 0)} for r in rooms]


EDITABLE = {"enabled", "model_pref", "skills", "playbook", "superior_id", "goal", "permissions", "plugins"}
PERMISSIONS = {"read", "write", "shell", "network"}


def update_agent(agent_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Update an agent. Names are fixed by design and cannot be edited."""
    bad = set(changes) - EDITABLE
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        if "superior_id" in changes and changes["superior_id"] == agent_id:
            raise ValueError("an agent cannot report to itself")
        if "permissions" in changes and not set(changes["permissions"]) <= PERMISSIONS:
            raise ValueError(f"permissions are a subset of {sorted(PERMISSIONS)}")
        if a.lifecycle == "retired" and changes.get("enabled"):
            raise ValueError("a retired agent cannot be enabled")
        if "plugins" in changes and not (isinstance(changes["plugins"], list)
                                         and all(isinstance(x, str) and len(x) <= 64 for x in changes["plugins"])):
            raise ValueError("plugins is a list of catalog names")
        pref = changes.get("model_pref")
        if pref and str(pref).startswith("agent:") and str(pref)[6:] == str(agent_id):
            raise ValueError("an agent cannot use itself as its model")
        for k, v in changes.items():
            setattr(a, k, v)
        s.flush()
        return agent_dict(a)


def agent_detail(agent_id: int) -> dict[str, Any]:
    from agent_company.db import Lesson, Run

    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        d = agent_dict(a)
        d["backstory"] = a.backstory
        d["related"] = [{"id": o.id, "name": o.name, "role": o.role, "weight": w}
                        for o, w in related(s, a.id, 8)]
        d["subordinates"] = [{"id": o.id, "name": o.name, "role": o.role}
                             for o in s.scalars(select(Agent).where(Agent.superior_id == a.id))]
        d["lessons"] = [{"text": lesson.text, "outcome": lesson.outcome,
                         "at": lesson.created_at.isoformat()}
                        for lesson in s.scalars(select(Lesson).where(Lesson.agent_id == a.id)
                                                .order_by(Lesson.id.desc()).limit(20))]
        from agent_company.db import Handover

        d["handovers"] = [{"goal_id": h.goal_id, "task": h.task_key, "from": h.from_agent_id, "to": h.to_agent_id,
                           "package": h.package, "at": h.created_at.isoformat()}
                          for h in s.scalars(select(Handover).where(
                              or_(Handover.from_agent_id == a.id, Handover.to_agent_id == a.id))
                              .order_by(Handover.id.desc()).limit(10))]
        d["trial_playbook"] = (a.experiment or {}).get("playbook")
        d["trial_outcomes"] = (a.experiment or {}).get("outcomes", [])
        runs = s.scalars(select(Run).where(Run.agent_id == a.id)).all()
        d["usage"] = {"runs": len(runs), "input_tokens": sum(r.input_tokens for r in runs),
                      "output_tokens": sum(r.output_tokens for r in runs),
                      "cost": round(sum(r.cost for r in runs), 6)}
        return d


# --- manual management and lifecycle (SPEC02 §6: active, paused, retired) -------------

def create_agent(role: str, title: str = "", skills: dict[str, int] | None = None, room: str = "",
                 superior_id: int | None = None, goal: str = "", writes: bool = False) -> dict[str, Any]:
    role = _slug(role)
    with session() as s:
        spec = org.ROLES.get(role)
        room = room or (spec.room if spec else ("development" if writes else "information_collection"))
        if s.scalars(select(Room).where(Room.key == room)).first() is None:
            raise ValueError(f"unknown room {room!r}")
        if superior_id is not None and s.get(Agent, superior_id) is None:
            raise ValueError("unknown superior")
        base = title.strip() or (spec.title if spec else role.replace("_", " ").title() + " Specialist")
        name, n = base, 1
        while s.scalars(select(Agent).where(Agent.name == name)).first():
            n += 1
            name = f"{base} #{n}"
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
        a = Agent(name=name, role=role, title=base, room_key=room,
                  superior_id=superior_id or (gm.id if gm else None),
                  goal=goal or (spec.goal if spec else f"Handle {role.replace('_', ' ')} work"),
                  backstory=spec.backstory if spec else "",
                  skills=dict(skills or (spec.skills if spec else {role: 5})),
                  permissions=list(spec.permissions) if spec else (["read", "write", "shell"] if writes else ["read"]),
                  enabled=True)
        s.add(a)
        s.flush()
        _link_similar(s, a, threshold=0.05)
        return agent_dict(a)


def clone_agent(agent_id: int) -> dict[str, Any]:
    with _staff_lock, session() as s:
        src = s.get(Agent, agent_id)
        if src is None:
            raise KeyError(agent_id)
        if src.lifecycle == "retired":
            raise ValueError("a retired agent cannot be cloned")
        c = _clone(s, src)
        return agent_dict(c)


def set_lifecycle(agent_id: int, state: str) -> dict[str, Any]:
    if state not in {"active", "paused", "retired"}:
        raise ValueError("state is active, paused or retired")
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        if a.lifecycle == "retired":
            raise ValueError("retirement is permanent")
        if state != "active" and a.role == "general_manager" and a.parent_id is None:
            raise ValueError("the company General Manager cannot be paused or retired")
        if state != "active" and a.status == "busy":
            raise ValueError("the agent is working; try again when it is idle")
        a.lifecycle, a.enabled = state, state == "active"
        return agent_dict(a)
