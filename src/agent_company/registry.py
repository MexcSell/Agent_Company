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


def connect(s: Session, a_id: int, b_id: int, kind: str = "collaborated", inc: float = 0.1) -> None:
    if a_id == b_id:
        return
    lo, hi = sorted((a_id, b_id))
    rel = s.scalars(select(Relationship).where(
        Relationship.a_id == lo, Relationship.b_id == hi, Relationship.kind == kind)).first()
    if rel is None:
        s.add(Relationship(a_id=lo, b_id=hi, kind=kind, weight=min(1.0, 0.3 + inc)))
    else:
        rel.weight = min(1.0, rel.weight + inc)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "specialist"


def _skill_score(agent: Agent, wanted: list[str]) -> float:
    skills = agent.skills or {}
    return sum(skills.get(w, 0) for w in wanted) + 0.01 * agent.level


def resolve_role(role: str) -> tuple[str, str | None]:
    """Map a planner role onto the catalog. Returns (role, note)."""
    role = _slug(role)
    spec = org.ROLES.get(role)
    if spec is None or spec.enabled:
        return role, None
    with session() as s:
        if s.scalars(select(Agent).where(Agent.role == role, Agent.enabled)).first():
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
        pool = s.scalars(select(Agent).where(Agent.role == role, Agent.enabled)).all()
        idle = [a for a in pool if a.status == "idle"]
        if idle:
            agent = max(idle, key=lambda a: _skill_score(a, skills))
            action = "assigned"
        elif pool:
            agent = _clone(s, max(pool, key=lambda a: _skill_score(a, skills)))
            action = "cloned"
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
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is not None and a.status == "busy":
            a.status = "idle"


def list_agents() -> list[dict[str, Any]]:
    with session() as s:
        return [agent_dict(a) for a in s.scalars(select(Agent).order_by(Agent.id)).all()]


def list_rooms() -> list[dict[str, Any]]:
    with session() as s:
        rooms = s.scalars(select(Room).order_by(Room.id)).all()
        counts = dict(s.execute(select(Agent.room_key, func.count()).group_by(Agent.room_key)).all())
        return [{"key": r.key, "name": r.name, "division": r.division, "description": r.description,
                 "rotational": r.rotational, "agents": counts.get(r.key, 0)} for r in rooms]


EDITABLE = {"enabled", "model_pref", "skills", "playbook", "superior_id", "goal", "permissions"}


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
        runs = s.scalars(select(Run).where(Run.agent_id == a.id)).all()
        d["usage"] = {"runs": len(runs), "input_tokens": sum(r.input_tokens for r in runs),
                      "output_tokens": sum(r.output_tokens for r in runs),
                      "cost": round(sum(r.cost for r in runs), 6)}
        return d
