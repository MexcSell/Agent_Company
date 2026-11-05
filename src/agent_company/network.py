"""Agent relationships with evidence (SPEC-12 S12-R-01, R-02, R-06).

Kinds are in `registry.KINDS`. Inferred ones come from profiles: `similar` (role and skills, on creation) and
`complementary_skill` (the specialists of other roles that the new agent's peers verifiably worked with).
`mentored_by` starts as an inferred candidate on creation (the most certified agent of the same role, SPEC-13
certification) and becomes observed when a mentoring is recorded (mentoring.py). Observed kinds are written as
side effects of finished work, never from message counts (§18.1):

  reviewed_by     a reviewing role's task (QA, security) depended on a writer's task: writer reviewed_by reviewer
  depends_on      any other task dependency between two agents: dependant depends_on dependency
  handover_to     a task moved to another agent between iterations (loop._handover)
  research_partner  two research tasks of a goal that passed the gate
  knowledge_contributor  an agent's task used knowledge another agent wrote, and the goal passed
  collaborated    gate results only (learning.link_team, S12-D-01)

Each piece of evidence (a task, a goal, a plan) changes a link once. Administrators can correct a link's
weight or expire it; the correction is recorded in its evidence and audited by the API (S12-R-06).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from agent_company import registry
from agent_company.db import Agent, Event, Lesson, Relationship, Task, log_event, now, session
from agent_company.quality import REVIEW_ROLES
OBSERVED_GAIN = 0.1
COMPLEMENTARY = 3  # complementary specialists linked to a new agent


def origin(r: Relationship) -> str:
    """inferred (from profiles), observed (from verified work) or corrected (an administrator set it); a mentor
    candidate is inferred until a mentoring is recorded."""
    evidence = [str(e) for e in r.evidence or []]
    if any(e.startswith("admin:") for e in evidence):
        return "corrected"
    if r.kind in registry.INFERRED or (r.kind == "mentored_by" and not any(e.startswith("mentoring:")
                                                                           for e in evidence)):
        return "inferred"
    return "observed"


def rel_dict(r: Relationship, names: dict[int, str] | None = None) -> dict[str, Any]:
    names = names or {}
    return {"id": r.id, "a_id": r.a_id, "b_id": r.b_id, "a": names.get(r.a_id), "b": names.get(r.b_id),
            "kind": r.kind, "directed": r.kind in registry.DIRECTED, "weight": round(r.weight, 3),
            "status": r.status or "active", "origin": origin(r), "evidence": list(r.evidence or [])[-20:],
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


# --- observed relationships (side effects of finished work) ---------------------------------

def observe(goal_id: int, iteration: int, passed: bool) -> int:
    """Write the observed relationships of one finished iteration. Returns how many links changed."""
    n = 0
    with session() as s:
        tasks = [t for t in s.scalars(select(Task).where(Task.goal_id == goal_id, Task.iteration == iteration))
                 if t.agent_id and t.status in {"done", "failed"}]
        by_key = {t.key: t for t in tasks}
        for t in tasks:
            for dep in (by_key.get(k) for k in t.depends_on or []):
                if dep is None or dep.agent_id == t.agent_id:
                    continue
                if t.role in REVIEW_ROLES and dep.writes:
                    registry.connect(s, dep.agent_id, t.agent_id, "reviewed_by", OBSERVED_GAIN,
                                     evidence=f"task:{t.id}")
                else:
                    registry.connect(s, t.agent_id, dep.agent_id, "depends_on", OBSERVED_GAIN,
                                     evidence=f"task:{t.id}")
                n += 1
        if not passed:
            return n
        research = sorted({t.agent_id for t in tasks if t.role == "research" and t.status == "done"})
        for i, a in enumerate(research):
            for b in research[i + 1:]:
                registry.connect(s, a, b, "research_partner", OBSERVED_GAIN, evidence=f"goal:{goal_id}")
                n += 1
        team = {t.agent_id for t in tasks if t.status == "done"}
        used: dict[int, set[int]] = {}
        for agent_id, data in s.execute(select(Event.agent_id, Event.data).where(
                Event.kind == "retrieval", Event.goal_id == goal_id, Event.agent_id.is_not(None))):
            if agent_id in team:
                used.setdefault(agent_id, set()).update(int(x) for x in (data or {}).get("ids", []))
        ids = set().union(*used.values()) if used else set()
        authors = dict(s.execute(select(Lesson.id, Lesson.agent_id).where(Lesson.id.in_(ids))).all()) if ids else {}
        for agent_id, items in used.items():
            for author in {authors[i] for i in items if i in authors} - {agent_id}:
                registry.connect(s, author, agent_id, "knowledge_contributor", OBSERVED_GAIN,
                                 evidence=f"goal:{goal_id}")
                n += 1
    return n


def handover(s: Session, from_id: int, to_id: int, goal_id: int, task_key: str, iteration: int) -> None:
    registry.connect(s, from_id, to_id, "handover_to", OBSERVED_GAIN,
                     evidence=f"goal:{goal_id}:{task_key}:{iteration}")


# --- relationships of a new agent (S12-R-02) ---------------------------------------------

def mentor_for(s: Session, agent: Agent, skills: list[str] | None = None) -> tuple[Agent, int, str] | None:
    """The most certified active agent of the same role (SPEC-13 certification from final evaluations), on the
    given skills when any of them is certified, else on any: (mentor, level, capability)."""
    from agent_company import training

    family = {agent.id, agent.parent_id} - {None}
    best: tuple[int, int, int, Agent, str] | None = None
    for o in s.scalars(select(Agent).where(Agent.role == agent.role, Agent.lifecycle == "active",
                                           Agent.id != agent.id)):
        if o.id in family or o.parent_id in family:  # its own original or a sibling clone is not a mentor
            continue
        certs = training.certified(o.id, s)
        on_skill = {k: v["level"] for k, v in certs.items() if not skills or k in skills}
        pool = on_skill or {k: v["level"] for k, v in certs.items()}
        if not pool:
            continue
        cap = max(pool, key=lambda k: pool[k])
        key = (1 if on_skill else 0, pool[cap], o.tasks_done or 0)
        if pool[cap] >= 1 and (best is None or key > best[:3]):
            best = (*key, o, cap)
    return (best[3], best[1], best[4]) if best else None


def link_on_creation(s: Session, agent: Agent, skills: list[str] | None = None) -> None:
    """`complementary_skill` to the specialists of other roles its peers (same role, or the similar agents just
    linked) worked with, and a `mentored_by` candidate. `similar` is linked by registry._link_similar."""
    peers = set(s.scalars(select(Agent.id).where(Agent.role == agent.role, Agent.id != agent.id)))
    if not peers:
        peers = {r.b_id if r.a_id == agent.id else r.a_id for r in s.scalars(select(Relationship).where(
            Relationship.kind == "similar", or_(Relationship.a_id == agent.id, Relationship.b_id == agent.id)))}
    partners: dict[int, float] = {}
    for r in s.scalars(select(Relationship).where(Relationship.kind == "collaborated", Relationship.status != "expired",
                                                  or_(Relationship.a_id.in_(peers), Relationship.b_id.in_(peers)))):
        for p, other in ((r.a_id, r.b_id), (r.b_id, r.a_id)):
            if p in peers and other not in peers and other != agent.id:
                partners[other] = max(partners.get(other, 0.0), r.weight)
    roles = dict(s.execute(select(Agent.id, Agent.role).where(Agent.id.in_(list(partners)),
                                                             Agent.lifecycle == "active")).all()) if partners else {}
    for other in sorted((p for p in partners if p in roles and roles[p] != agent.role),
                        key=lambda p: -partners[p])[:COMPLEMENTARY]:
        # a new link starts at 0.3 + inc: 80 % of the peers' collaboration weight, at least a trace
        registry.connect(s, agent.id, other, "complementary_skill", max(0.01, round(0.8 * partners[other] - 0.3, 3)),
                         evidence="inferred:peers worked with it")
    found = mentor_for(s, agent, skills)
    if found is not None:
        mentor, level, cap = found
        registry.connect(s, agent.id, mentor.id, "mentored_by", 0.2 + 0.1 * level,
                         evidence=f"candidate:{cap}:L{level}")


# --- reading and correcting (S12-R-06) ---------------------------------------------------------

def of_agent(agent_id: int) -> list[dict[str, Any]]:
    with session() as s:
        if s.get(Agent, agent_id) is None:
            raise KeyError(f"agent {agent_id}")
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        rels = s.scalars(select(Relationship).where(or_(Relationship.a_id == agent_id, Relationship.b_id == agent_id))
                         .order_by(Relationship.weight.desc())).all()
        return [rel_dict(r, names) for r in rels]


def correct(agent_id: int, other_id: int, kind: str, actor: str, reason: str, weight: float | None = None,
            status: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Set a link's weight and/or status (creating an observed link an administrator vouches for).
    Returns (before, after). A directed kind reads agent <kind> other."""
    if kind not in registry.KINDS:
        raise ValueError(f"kind is one of {', '.join(sorted(registry.KINDS))}")
    if status not in {None, "active", "expired"}:
        raise ValueError("status is active or expired")
    if weight is not None and not 0 <= float(weight) <= 1:
        raise ValueError("weight is 0 to 1")
    if not reason.strip():
        raise ValueError("a reason is required")
    if agent_id == other_id:
        raise ValueError("an agent has no relationship with itself")
    a_id, b_id = (agent_id, other_id) if kind in registry.DIRECTED else sorted((agent_id, other_id))
    with session() as s:
        if s.get(Agent, agent_id) is None or s.get(Agent, other_id) is None:
            raise KeyError("agent")
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        r = s.scalars(select(Relationship).where(Relationship.a_id == a_id, Relationship.b_id == b_id,
                                                 Relationship.kind == kind)).first()
        before = rel_dict(r, names) if r is not None else None
        if r is None:
            if weight is None:
                raise ValueError("a new link needs a weight")
            r = Relationship(a_id=a_id, b_id=b_id, kind=kind, weight=float(weight), evidence=[])
            s.add(r)
        if weight is not None:
            r.weight = float(weight)
        if status is not None:
            r.status = status
        r.evidence = [*(r.evidence or []), f"admin:{actor}: {reason.strip()[:200]}"][-200:]
        r.updated_at = now()
        s.flush()
        log_event("relationship", f"{names.get(a_id)} {kind} {names.get(b_id)} corrected by {actor}: "
                  f"{reason.strip()[:200]}", agent_id=agent_id, s=s)
        return before, rel_dict(r, names)


# --- the network view (S12-R-07): server-side filters, bounded depth, pages ---------------------

MAX_GRAPH_DEPTH, MAX_GRAPH_LIMIT, MAX_GRAPH_EDGES = 2, 200, 5000
ORIGINS = {"inferred", "observed", "corrected"}


def _node(a: Agent, depth: int | None = None) -> dict[str, Any]:
    skills = dict(a.skills or {})
    top = max(skills, key=lambda k: (skills[k], k)) if skills else ""
    return {"id": a.id, "name": a.name, "role": a.role, "room": a.room_key, "lifecycle": a.lifecycle,
            "enabled": a.enabled, "top_skill": top} | ({"depth": depth} if depth is not None else {})


def _matching(s: Session, room: str, skill: str) -> set[int] | None:
    """Agents in the room that have the skill (None = no filter)."""
    if not room and not skill:
        return None
    q = select(Agent)
    if room:
        q = q.where(Agent.room_key == room)
    term = skill.strip().lower()
    return {a.id for a in s.scalars(q) if not term or any(term in k.lower() for k in (a.skills or {}))}


def graph(agent_id: int | None = None, room: str = "", skill: str = "", kind: str = "", origin_: str = "",
          include_expired: bool = False, depth: int = 1, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """One page of relationship edges and the agents they touch. Without `agent_id`: every link, strongest
    first, where either end is in `room` and has `skill`. With it: links within `depth` (≤ 2) steps of that agent,
    nearest first, where the agent reached is in `room` and has `skill`. Filters run on the server; the order is
    deterministic, so pages never overlap (AR-009, AR-020)."""
    if kind and kind not in registry.KINDS:
        raise ValueError(f"kind is one of {', '.join(sorted(registry.KINDS))}")
    if origin_ and origin_ not in ORIGINS:
        raise ValueError(f"origin is one of {', '.join(sorted(ORIGINS))}")
    depth = max(1, min(int(depth), MAX_GRAPH_DEPTH))
    offset, limit = max(0, int(offset)), max(1, min(int(limit), MAX_GRAPH_LIMIT))
    with session() as s:
        match = _matching(s, room, skill)
        base = select(Relationship)
        if kind:
            base = base.where(Relationship.kind == kind)
        if not include_expired:
            base = base.where(Relationship.status != "expired")
        keep = (lambda r: True) if not origin_ else (lambda r: origin(r) == origin_)  # noqa: E731
        reached: dict[int, int] = {}
        if agent_id is None:
            q = base if match is None else base.where(or_(Relationship.a_id.in_(match), Relationship.b_id.in_(match)))
            edges = [(r, None) for r in s.scalars(q.order_by(Relationship.weight.desc(), Relationship.id)
                                                  .limit(MAX_GRAPH_EDGES)) if keep(r)]
        else:
            if s.get(Agent, agent_id) is None:
                raise KeyError(f"agent {agent_id}")
            reached, edges, seen, frontier = {agent_id: 0}, [], set(), {agent_id}
            for step in range(1, depth + 1):
                nxt: set[int] = set()
                for r in s.scalars(base.where(or_(Relationship.a_id.in_(frontier), Relationship.b_id.in_(frontier)))
                                   .order_by(Relationship.weight.desc(), Relationship.id)):
                    if r.id in seen or not keep(r):
                        continue
                    other = r.b_id if r.a_id in frontier else r.a_id
                    if other not in reached and match is not None and other not in match:
                        continue
                    seen.add(r.id)
                    edges.append((r, step))
                    if other not in reached:
                        reached[other] = step
                        nxt.add(other)
                if not nxt or len(edges) >= MAX_GRAPH_EDGES:
                    break
                frontier = nxt
        page = edges[offset:offset + limit]
        ids = {i for r, _ in page for i in (r.a_id, r.b_id)} | ({agent_id} if agent_id else set())
        agents = {a.id: a for a in s.scalars(select(Agent).where(Agent.id.in_(ids)))}
        names = {i: a.name for i, a in agents.items()}
        nodes = [_node(a, reached.get(a.id) if agent_id else None)
                 for a in sorted(agents.values(), key=lambda a: (reached.get(a.id, 9), a.id))]
        return {"agent_id": agent_id, "depth": depth, "total": len(edges), "offset": offset, "limit": limit,
                "edges": [rel_dict(r, names) | ({"depth": d} if d else {}) for r, d in page], "nodes": nodes}


def overview() -> dict[str, Any]:
    """Network overview counts (SPEC-12 §16.1)."""
    from datetime import timedelta

    from agent_company.db import Handover, LearningPlan, Mentoring, ResearchShift

    week = now() - timedelta(days=7)
    with session() as s:
        agents = s.scalars(select(Agent)).all()
        active = [a for a in agents if a.enabled and a.lifecycle == "active"]
        links: dict[str, int] = {o: 0 for o in sorted(ORIGINS)}
        expired = 0
        for r in s.scalars(select(Relationship)):
            if r.status == "expired":
                expired += 1
            else:
                links[origin(r)] += 1
        teams: dict[int, set[int]] = {}
        for a in agents:
            for g in a.active_goals or []:
                teams.setdefault(int(g), set()).add(a.id)
        certified = {(p.agent_id, p.capability) for p in s.scalars(
            select(LearningPlan).where(LearningPlan.status == "passed"))}
        handovers = len(s.scalars(select(Handover.id).where(Handover.created_at >= week)).all()) + len(
            [x for x in s.scalars(select(ResearchShift).where(ResearchShift.started_at >= week)) if x.handover])
        return {"active_agents": len(active), "agents": len(agents), "links": links, "expired_links": expired,
                "teams": len(teams), "team_sizes": sorted((len(v) for v in teams.values()), reverse=True),
                "certified_capabilities": len(certified), "handovers_7d": handovers,
                "mentorings": len(s.scalars(select(Mentoring.id).where(Mentoring.status == "active")).all()),
                "overloaded": [{"id": a.id, "name": a.name} for a in active if not registry._free(a, None)]}
