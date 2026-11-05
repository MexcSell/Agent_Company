"""The knowledge graph (SPEC-11 S11-R-07): `kg_edges` between entities of the existing tables.

Entities are `type:id`: agent, project, goal, task, knowledge (lessons), decision, source, incident, hypothesis, and the
names technology, skill and user. Edges are written as side effects, in the caller's session (one writer):

  agent  CREATED       knowledge     a lesson was stored (learning.learn)
  knowledge APPLIES_TO technology    its technologies
  knowledge DERIVED_FROM goal|task   where it came from
  knowledge DERIVED_FROM hypothesis  a concluded hypothesis (hypotheses._knowledge)
  goal   USED          knowledge     retrieved for one of the goal's tasks (learning.retrieve)
  knowledge VERIFIED_BY user         a person approved it (learning.review)
  agent  EXPERIENCED_IN skill        a task with that skill passed the gate (weight = passed tasks)
  task   ASSIGNED_TO   agent         staffing (registry.staff)
  x      SUPPORTS      decision      each piece of evidence a decision names that is an entity (decisions.record)
  knowledge SUPERSEDES / CONTRADICTS knowledge   (learning.supersede, learning._link_contradictions)

Agent-agent relationships stay in `relationships`; the graph shows `collaborated` links as COLLABORATED_WITH
without copying them. `neighbourhood` walks the graph breadth-first from one entity (depth ≤ 3) and pages the
edges (KT-019). Deleting a knowledge item removes every edge naming it (learning.delete).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from agent_company.db import (Agent, Decision, Goal, Hypothesis, Incident, KgEdge, Lesson, Project, Relationship,
                              Source, Task, session)

RELS = {"CREATED", "USED", "DEPENDS_ON", "SUPPORTS", "CONTRADICTS", "DERIVED_FROM", "APPLIES_TO", "SUPERSEDES",
        "VERIFIED_BY", "EXPERIENCED_IN", "ASSIGNED_TO", "COLLABORATED_WITH"}
TYPES = {"agent", "project", "goal", "task", "knowledge", "decision", "source", "incident", "hypothesis", "technology",
         "skill", "user"}
NAMED = {"technology", "skill", "user"}  # ids are names, not rows
MAX_DEPTH, MAX_EDGES = 3, 2000
_REF = re.compile(r"^(?:(agent|project|goal|task|knowledge|decision|source|incident)[:#]|(K))(\d+)$", re.I)


def key(typ: str, ident: Any) -> str:
    return f"{typ}:{ident}"


def parse(entity: str) -> tuple[str, str]:
    typ, _, ident = (entity or "").partition(":")
    typ, ident = typ.strip().lower(), ident.strip()
    if typ not in TYPES or not ident:
        raise ValueError(f"entity is type:id with type one of {', '.join(sorted(TYPES))}")
    if typ in NAMED:
        ident = ident.lower()[:64]
    elif not ident.isdigit():
        raise ValueError(f"{typ} ids are numbers")
    return typ, ident


def link(s: Session, src: tuple[str, Any], rel: str, dst: tuple[str, Any], ref: str = "", inc: float = 0.0) -> None:
    """Add an edge once; the same edge again only adds its reference (and `inc` to its weight, once per
    reference)."""
    assert rel in RELS, rel
    (st, si), (dt, di) = src, dst
    si, di = str(si)[:64], str(di)[:64]
    if not si or not di:
        return
    for e in s.new:  # added earlier in this session and not flushed yet
        if isinstance(e, KgEdge) and (e.src_type, e.src_id, e.rel, e.dst_type, e.dst_id) == (st, si, rel, dt, di):
            break
    else:
        e = s.scalars(select(KgEdge).where(KgEdge.src_type == st, KgEdge.src_id == si, KgEdge.rel == rel,
                                           KgEdge.dst_type == dt, KgEdge.dst_id == di)).first()
    if e is None:
        s.add(KgEdge(src_type=st, src_id=si, rel=rel, dst_type=dt, dst_id=di, weight=1.0,
                     evidence={"refs": [ref]} if ref else {}))
        return
    refs = list((e.evidence or {}).get("refs", []))
    if ref and ref not in refs:
        e.evidence = {**(e.evidence or {}), "refs": [*refs, ref][-50:]}
        e.weight = (e.weight or 0.0) + inc


def evidence_ref(x: Any) -> tuple[str, str] | None:
    """An entity named by a decision's evidence entry: {"type": t, "id": n}, "goal:3", "task#5", "K12"."""
    if isinstance(x, dict) and str(x.get("type", "")).lower() in TYPES - NAMED and str(x.get("id", "")).isdigit():
        return str(x["type"]).lower(), str(x["id"])
    if isinstance(x, dict) and str(x.get("type", "")).lower() == "review" and str(x.get("id", "")).isdigit():
        return "task", str(x["id"])  # a conflict review is a task
    if isinstance(x, str):
        m = _REF.match(x.strip())
        if m:
            return ("knowledge" if m.group(2) else m.group(1).lower()), m.group(3)
    return None


# --- side effects -------------------------------------------------------------------------------

def on_lesson(s: Session, item: Lesson, success: bool, skills: list[str]) -> None:
    """Experience always counts; the item's own edges only once it is approved (screened or reviewed)."""
    if success and item.task_id:
        for sk in {x.strip().lower().replace(" ", "_") for x in skills if x.strip()}:
            link(s, ("agent", item.agent_id), "EXPERIENCED_IN", ("skill", sk), ref=f"task:{item.task_id}", inc=1.0)
    if item.status == "approved":
        on_knowledge(s, item)


def on_knowledge(s: Session, item: Lesson) -> None:
    k = ("knowledge", item.id)
    link(s, ("agent", item.agent_id), "CREATED", k)
    for tech in item.technologies or []:
        link(s, k, "APPLIES_TO", ("technology", tech))
    if item.goal_id:
        link(s, k, "DERIVED_FROM", ("goal", item.goal_id))
    if item.task_id:
        link(s, k, "DERIVED_FROM", ("task", item.task_id))


def on_retrieval(goal_id: int, item_ids: list[int]) -> None:
    with session() as s:
        for i in item_ids:
            link(s, ("goal", goal_id), "USED", ("knowledge", i))


def on_decision(s: Session, decision_id: int, evidence: list[Any]) -> None:
    for x in evidence or []:
        ref = evidence_ref(x)
        if ref is not None:
            link(s, ref, "SUPPORTS", ("decision", decision_id))


# --- reading ---------------------------------------------------------------------------------

def _label(s: Session, typ: str, ident: str) -> dict[str, Any]:
    if typ in NAMED:
        return {"label": ident}
    row = s.get({"agent": Agent, "project": Project, "goal": Goal, "task": Task, "knowledge": Lesson,
                 "decision": Decision, "source": Source, "incident": Incident, "hypothesis": Hypothesis}[typ],
                int(ident))
    if row is None:
        return {"label": f"{typ} {ident} (deleted)", "missing": True}
    if typ == "knowledge":
        return {"label": f"K{row.id} {row.text[:80]}", "status": row.status}
    if typ == "decision":
        return {"label": row.topic[:120], "status": row.status}
    if typ in {"goal", "task"}:
        return {"label": row.title[:120], "status": row.status} | ({"goal_id": row.goal_id} if typ == "task" else {})
    if typ == "source":
        return {"label": row.url[:200]}
    if typ == "incident":
        return {"label": row.title[:120], "status": row.status}
    if typ == "hypothesis":
        return {"label": row.statement[:120], "status": row.status}
    return {"label": row.name}


def _edges_of(s: Session, frontier: set[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    conds = [or_(and_(KgEdge.src_type == t, KgEdge.src_id == i), and_(KgEdge.dst_type == t, KgEdge.dst_id == i))
             for t, i in frontier]
    for j in range(0, len(conds), 200):
        for e in s.scalars(select(KgEdge).where(or_(*conds[j:j + 200])).order_by(KgEdge.id).limit(MAX_EDGES)):
            out.append({"id": e.id, "src": key(e.src_type, e.src_id), "rel": e.rel, "dst": key(e.dst_type, e.dst_id),
                        "weight": round(e.weight or 0.0, 3), "evidence": e.evidence or {},
                        "at": e.created_at.isoformat()})
    agents = [int(i) for t, i in frontier if t == "agent"]
    if agents:
        for r in s.scalars(select(Relationship).where(Relationship.kind == "collaborated", Relationship.status != "expired",
                                                      or_(Relationship.a_id.in_(agents), Relationship.b_id.in_(agents)))):
            out.append({"id": f"r{r.id}", "src": key("agent", r.a_id), "rel": "COLLABORATED_WITH",
                        "dst": key("agent", r.b_id), "weight": round(r.weight, 3),
                        "evidence": {"refs": list(r.evidence or [])[-10:]}, "at": None})
    return out


def neighbourhood(entity: str, depth: int = 2, offset: int = 0, limit: int = 100, rel: str = "") -> dict[str, Any]:
    """Edges within `depth` steps of the entity (breadth-first, nearest first), one page of them, and the nodes
    those edges touch."""
    typ, ident = parse(entity)
    depth = max(1, min(int(depth), MAX_DEPTH))
    if rel and rel not in RELS:
        raise ValueError(f"rel is one of {', '.join(sorted(RELS))}")
    root = key(typ, ident)
    with session() as s:
        seen_nodes, seen_edges, edges = {root: 0}, set(), []
        frontier = {(typ, ident)}
        for step in range(1, depth + 1):
            nxt: set[tuple[str, str]] = set()
            for e in _edges_of(s, frontier):
                if e["id"] in seen_edges or (rel and e["rel"] != rel):
                    continue
                seen_edges.add(e["id"])
                edges.append(e | {"depth": step})
                for end in (e["src"], e["dst"]):
                    if end not in seen_nodes:
                        seen_nodes[end] = step
                        t, _, i = end.partition(":")
                        nxt.add((t, i))
            if not nxt or len(edges) >= MAX_EDGES:
                break
            frontier = nxt
        page = edges[max(0, offset):max(0, offset) + max(1, min(limit, 500))]
        touched = {root} | {e["src"] for e in page} | {e["dst"] for e in page}
        nodes = []
        for n in sorted(touched, key=lambda x: (seen_nodes.get(x, 99), x)):
            t, _, i = n.partition(":")
            nodes.append({"key": n, "type": t, "id": i, "depth": seen_nodes.get(n, 0)} | _label(s, t, i))
    return {"root": root, "depth": depth, "total": len(edges), "offset": offset, "limit": limit, "edges": page,
            "nodes": nodes}
