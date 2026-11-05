"""Organisation editing (docs/requirements/SPEC-01-governance.md S01-R-01, S01-R-02, S01-R-04).

rooms      S01-R-01: administrators create rooms and change their name, description, division, parent room, lead
           agent and switch. A room cannot be its own ancestor. A disabled room's agents are never staffed
           (`registry.staff`) and new projects cannot list it. A room "has running goals" when one of its agents has
           a queued or running task of a goal that is not final; such a room cannot be disabled, and neither can the
           room of the company General Manager. Rooms are never deleted: the seed only adds missing keys, so edits
           persist. The API audits every change with its before/after values.
positions  S01-R-02: a change of a room's lead (`room:<key>:lead`) or a project's General Manager (`project:<id>:gm`)
           records (position, previous agent, new agent, effective at, reason, actor), so decisions stay attached to
           the position rather than to whoever holds it.
export     S01-R-04: rooms, agents (role, superior, lifecycle, certification level) and a relationship summary as
           JSON, or one of those tables as CSV.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_company.db import Agent, Goal, PositionChange, Relationship, Room, Task, log_event, now, session
from agent_company.workflow import ACTIVE

DIVISIONS = ("management", "development", "learning", "research", "security", "business")
EDITABLE = {"name", "description", "division", "parent_key", "lead_agent_id", "enabled", "daily_budget_usd"}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def room_dict(r: Room, agents: int = 0) -> dict[str, Any]:
    return {"key": r.key, "name": r.name, "division": r.division, "description": r.description,
            "rotational": r.rotational, "parent": r.parent_key, "lead_agent_id": r.lead_agent_id,
            "enabled": r.enabled is not False, "agents": agents, "daily_budget_usd": r.daily_budget_usd or 0.0}


def disabled_rooms(s: Session) -> set[str]:
    return set(s.scalars(select(Room.key).where(Room.enabled.is_(False))))


def get_room(key: str) -> dict[str, Any]:
    with session() as s:
        r = s.scalars(select(Room).where(Room.key == key)).first()
        if r is None:
            raise KeyError(key)
        return room_dict(r, s.scalar(select(func.count()).select_from(Agent).where(Agent.room_key == key)) or 0)


def _ancestors(s: Session, key: str | None) -> list[str]:
    parents = dict(s.execute(select(Room.key, Room.parent_key)).all())
    out: list[str] = []
    while key and key not in out:
        out.append(key)
        key = parents.get(key)
    return out


def _check_parent(s: Session, key: str, parent: str | None) -> None:
    if not parent:
        return
    if s.scalars(select(Room).where(Room.key == parent)).first() is None:
        raise ValueError(f"unknown parent room {parent!r}")
    if key in _ancestors(s, parent):
        raise ValueError(f"room {key!r} cannot be its own ancestor (via {parent!r})")


def _check_lead(s: Session, agent_id: int | None) -> None:
    if agent_id is None:
        return
    a = s.get(Agent, agent_id)
    if a is None:
        raise ValueError("unknown lead agent")
    if a.lifecycle in {"retired", "candidate"}:
        raise ValueError(f"{a.name} is {a.lifecycle} and cannot lead a room")


def running_goals(s: Session, key: str) -> list[int]:
    """Goals that are not final and have a queued or running task of an agent of the room."""
    q = (select(Task.goal_id).join(Agent, Agent.id == Task.agent_id).join(Goal, Goal.id == Task.goal_id)
         .where(Agent.room_key == key, Task.status.in_(["queued", "running"]), Goal.status.in_(ACTIVE)).distinct())
    return sorted(s.scalars(q))


def record_position(s: Session, position: str, previous: int | None, new: int | None, actor: str,
                    reason: str = "") -> None:
    if previous == new:
        return
    s.add(PositionChange(position=position, previous_agent_id=previous, new_agent_id=new, actor=actor,
                         reason=reason.strip()[:1000], effective_at=now()))
    log_event("position", f"{position}: agent #{previous} → #{new} by {actor}", position=position,
              previous_agent_id=previous, new_agent_id=new, reason=reason, s=s)


def create_room(key: str, name: str, division: str, description: str = "", parent_key: str | None = None,
                lead_agent_id: int | None = None, actor: str = "system") -> dict[str, Any]:
    key = (key or "").strip().lower()
    if not KEY_RE.match(key):
        raise ValueError("key is 2-64 lowercase letters, digits and _ and starts with a letter")
    if division not in DIVISIONS:
        raise ValueError(f"division is one of {', '.join(DIVISIONS)}")
    if not (name or "").strip():
        raise ValueError("a room needs a name")
    with session() as s:
        if s.scalars(select(Room).where(Room.key == key)).first() is not None:
            raise ValueError(f"room {key!r} exists")
        _check_parent(s, key, parent_key or None)
        _check_lead(s, lead_agent_id)
        r = Room(key=key, name=name.strip()[:128], division=division, description=(description or "").strip(),
                 parent_key=parent_key or None, lead_agent_id=lead_agent_id, enabled=True)
        s.add(r)
        s.flush()
        record_position(s, f"room:{key}:lead", None, lead_agent_id, actor, "room created")
        return room_dict(r)


def update_room(key: str, changes: dict[str, Any], actor: str = "system", reason: str = "") -> dict[str, Any]:
    """Change a room (administrators; the API audits before and after). `reason` goes to the position history when
    the lead changes."""
    bad = set(changes) - EDITABLE
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    with session() as s:
        r = s.scalars(select(Room).where(Room.key == key)).first()
        if r is None:
            raise KeyError(key)
        if "name" in changes:
            changes["name"] = str(changes["name"] or "").strip()[:128]
            if not changes["name"]:
                raise ValueError("a room needs a name")
        if "division" in changes and changes["division"] not in DIVISIONS:
            raise ValueError(f"division is one of {', '.join(DIVISIONS)}")
        if "parent_key" in changes:
            changes["parent_key"] = changes["parent_key"] or None
            _check_parent(s, key, changes["parent_key"])
        if "lead_agent_id" in changes:
            changes["lead_agent_id"] = int(changes["lead_agent_id"]) if changes["lead_agent_id"] else None
            _check_lead(s, changes["lead_agent_id"])
        if "enabled" in changes:
            changes["enabled"] = bool(changes["enabled"])
            if not changes["enabled"] and r.enabled is not False:
                gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
                if gm is not None and gm.room_key == key:
                    raise ValueError("the company General Manager's room cannot be disabled")
                busy = running_goals(s, key)
                if busy:
                    raise ValueError(f"the room has running goals ({', '.join(f'#{g}' for g in busy)}); "
                                     "disable it when they have ended")
        if "daily_budget_usd" in changes:
            changes["daily_budget_usd"] = float(changes["daily_budget_usd"] or 0)
            if not 0 <= changes["daily_budget_usd"] <= 100_000:
                raise ValueError("a room's daily budget is $0 (none) to $100,000")
        if "lead_agent_id" in changes:
            record_position(s, f"room:{key}:lead", r.lead_agent_id, changes["lead_agent_id"], actor, reason)
        for k, v in changes.items():
            setattr(r, k, v)
        s.flush()
        return room_dict(r, s.scalar(select(func.count()).select_from(Agent).where(Agent.room_key == key)) or 0)


def positions(position: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """The position history, newest first; `position` filters (`room:development:lead`, or a prefix like `project:`)."""
    with session() as s:
        q = select(PositionChange).order_by(PositionChange.id.desc()).limit(max(1, min(limit, 1000)))
        if position:
            q = q.where(PositionChange.position.startswith(position))
        rows = s.scalars(q).all()
        ids = {i for r in rows for i in (r.previous_agent_id, r.new_agent_id) if i}
        names = dict(s.execute(select(Agent.id, Agent.name).where(Agent.id.in_(ids))).all()) if ids else {}
        return [{"id": r.id, "position": r.position, "previous_agent_id": r.previous_agent_id,
                 "previous_agent": names.get(r.previous_agent_id), "new_agent_id": r.new_agent_id,
                 "new_agent": names.get(r.new_agent_id), "reason": r.reason, "actor": r.actor,
                 "effective_at": r.effective_at.isoformat()} for r in rows]


def holder(position: str, at: Any = None) -> int | None:
    """Who held the position at `at` (default: now), from the history."""
    with session() as s:
        q = select(PositionChange).where(PositionChange.position == position)
        if at is not None:
            q = q.where(PositionChange.effective_at <= at)
        r = s.scalars(q.order_by(PositionChange.effective_at.desc(), PositionChange.id.desc())).first()
        return r.new_agent_id if r else None


# --- organisation export (S01-R-04) ------------------------------------------------------------

TABLES = ("rooms", "agents", "relationships")


def export() -> dict[str, Any]:
    from agent_company.performance import certification

    with session() as s:
        counts = dict(s.execute(select(Agent.room_key, func.count()).group_by(Agent.room_key)).all())
        rooms = [room_dict(r, counts.get(r.key, 0)) for r in s.scalars(select(Room).order_by(Room.id))]
        agents = s.scalars(select(Agent).order_by(Agent.id)).all()
        names = {a.id: a.name for a in agents}
        rows = [{"id": a.id, "name": a.name, "role": a.role, "title": a.title, "room": a.room_key,
                 "superior_id": a.superior_id, "superior": names.get(a.superior_id), "clone_of": a.parent_id,
                 "lifecycle": a.lifecycle, "enabled": a.enabled, "autonomy": a.autonomy or "G3"} for a in agents]
        rels = s.execute(select(Relationship.kind, Relationship.status, func.count())
                         .group_by(Relationship.kind, Relationship.status)).all()
    for row in rows:
        row["certification_level"] = certification(row["id"])["level"]
    summary: dict[str, dict[str, int]] = {}
    for kind, status, n in rels:
        summary.setdefault(kind, {"active": 0, "expired": 0})[status or "active"] = n
    return {"generated_at": now().isoformat(), "rooms": rooms, "agents": rows,
            "relationships": [{"kind": k, **v} for k, v in sorted(summary.items())]}


def export_csv(table: str = "agents") -> str:
    if table not in TABLES:
        raise ValueError(f"table is one of {', '.join(TABLES)}")
    rows = export()[table]
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue()
