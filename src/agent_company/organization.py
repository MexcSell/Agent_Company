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
        record_version(s, actor, f"room {key} created")
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
        if set(changes) & STRUCTURAL:
            record_version(s, actor, reason or f"room {key}: " + ", ".join(sorted(set(changes) & STRUCTURAL)))
        return room_dict(r, s.scalar(select(func.count()).select_from(Agent).where(Agent.room_key == key)) or 0)


# --- organisation versions (AICO UI-ORG-01/03/04) -----------------------------------------------

STRUCTURAL = {"parent_key", "lead_agent_id", "enabled", "name", "daily_budget_usd"}  # a room change that is a version


def _snapshot(s: Session) -> dict[str, Any]:
    rooms = {r.key: {"name": r.name, "parent": r.parent_key, "lead": r.lead_agent_id, "enabled": r.enabled is not False,
                     "daily_budget_usd": r.daily_budget_usd or 0.0} for r in s.scalars(select(Room).order_by(Room.id))}
    agents = {str(a.id): {"name": a.name, "role": a.role, "room": a.room_key, "superior": a.superior_id,
                          "enabled": bool(a.enabled), "lifecycle": a.lifecycle}
              for a in s.scalars(select(Agent).order_by(Agent.id)) if a.lifecycle != "retired"}
    return {"rooms": rooms, "agents": agents}


def record_version(s: Session, actor: str, reason: str) -> int | None:
    """Store the organisation as a new version inside the caller's session; nothing when it did not change."""
    from agent_company.db import OrgVersion

    snap = _snapshot(s)
    last = s.scalars(select(OrgVersion).order_by(OrgVersion.version.desc())).first()
    if last is not None and last.snapshot == snap:
        return None
    v = (last.version if last else 0) + 1
    s.add(OrgVersion(version=v, snapshot=snap, reason=(reason or "")[:500], actor=actor[:128]))
    s.flush()
    return v


def list_versions(limit: int = 200) -> list[dict[str, Any]]:
    """Newest first. The first call records the current organisation as version 1 (the baseline)."""
    from agent_company.db import OrgVersion

    with session() as s:
        if s.scalar(select(func.count()).select_from(OrgVersion)) == 0:
            record_version(s, "system", "baseline: the organisation when versions began")
        rows = s.scalars(select(OrgVersion).order_by(OrgVersion.version.desc()).limit(limit)).all()
        return [{"version": r.version, "reason": r.reason, "actor": r.actor, "at": r.created_at.isoformat(),
                 "rooms": len(r.snapshot.get("rooms", {})), "agents": len(r.snapshot.get("agents", {}))} for r in rows]


def get_version(version: int) -> dict[str, Any]:
    from agent_company.db import OrgVersion

    with session() as s:
        r = s.scalars(select(OrgVersion).where(OrgVersion.version == version)).first()
        if r is None:
            raise KeyError(f"organisation version {version}")
        return {"version": r.version, "reason": r.reason, "actor": r.actor, "at": r.created_at.isoformat(),
                "snapshot": r.snapshot}


def compare_versions(a: int, b: int) -> dict[str, Any]:
    """UI-ORG-03: what changed from version a to b — rooms and agents added, removed, changed (field: old → new)."""
    sa, sb = get_version(a)["snapshot"], get_version(b)["snapshot"]
    out: dict[str, Any] = {"a": a, "b": b}
    for part in ("rooms", "agents"):
        x, y = sa.get(part, {}), sb.get(part, {})
        out[part] = {"added": [{"key": k, **y[k]} for k in y if k not in x],
                     "removed": [{"key": k, **x[k]} for k in x if k not in y],
                     "changed": [{"key": k, "name": y[k].get("name"),
                                  "fields": {f: [x[k].get(f), y[k].get(f)] for f in set(x[k]) | set(y[k])
                                             if x[k].get(f) != y[k].get(f)}}
                                 for k in x if k in y and x[k] != y[k]]}
    return out


def restore_version(version: int, actor: str) -> dict[str, Any]:
    """UI-ORG-04: set rooms' parent, lead, enabled and budget back to `version`. Rooms created later stay as they
    are, no agent is deleted or moved; a room with running goals is not disabled (skipped, named). The result is a
    new version."""
    target = get_version(version)["snapshot"]["rooms"]
    skipped: list[str] = []
    changed: list[str] = []
    with session() as s:
        rooms = {r.key: r for r in s.scalars(select(Room))}
        agents = set(s.scalars(select(Agent.id)))
        for key, want in target.items():
            r = rooms.get(key)
            if r is None:
                skipped.append(f"{key}: no longer exists")
                continue
            if want["enabled"] is False and r.enabled is not False and running_goals(s, key):
                skipped.append(f"{key}: has running goals, stays enabled")
                want = {**want, "enabled": True}
            lead = want["lead"] if want["lead"] in agents else r.lead_agent_id
            parent = want["parent"] if (want["parent"] is None or want["parent"] in rooms) else r.parent_key
            new = {"parent_key": parent, "lead_agent_id": lead, "enabled": want["enabled"],
                   "daily_budget_usd": want.get("daily_budget_usd", 0.0), "name": want.get("name") or r.name}
            if any(getattr(r, k) != v for k, v in new.items()):
                if r.lead_agent_id != lead:
                    record_position(s, f"room:{key}:lead", r.lead_agent_id, lead, actor, f"restored version {version}")
                for k, v in new.items():
                    setattr(r, k, v)
                changed.append(key)
        s.flush()
        for key in rooms:  # the restored tree must still be a tree
            seen, cur = set(), key
            while cur is not None:
                if cur in seen:
                    raise ValueError(f"restoring version {version} would make a cycle at room {key}")
                seen.add(cur)
                cur = rooms[cur].parent_key if cur in rooms else None
        v = record_version(s, actor, f"restored version {version}")
    return {"restored": version, "version": v, "changed": changed, "skipped": skipped}


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


# --- organisation change proposals (AICO UI-ORG-02) ---------------------------------------------

AGENT_FIELDS = {"superior_id", "enabled", "room_key"}  # what a proposal may change on an agent (registry.update_agent)


def _check_changes(s: Session, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The changes in a stored form; a ValueError names the first that cannot apply to today's organisation."""
    if not isinstance(changes, list) or not changes or len(changes) > 20:
        raise ValueError("changes: 1-20 operations")
    rooms = {r.key for r in s.scalars(select(Room))}
    out = []
    for i, c in enumerate(changes, 1):
        op = (c or {}).get("op")
        if op == "create_room":
            key = str(c.get("key") or "").strip().lower()
            if not KEY_RE.match(key) or key in rooms:
                raise ValueError(f"change {i}: room key {key!r} is invalid or exists")
            if c.get("division") not in DIVISIONS:
                raise ValueError(f"change {i}: division is one of {', '.join(DIVISIONS)}")
            if c.get("parent_key") and c["parent_key"] not in rooms:
                raise ValueError(f"change {i}: unknown parent room {c['parent_key']!r}")
            rooms.add(key)
            out.append({"op": op, "key": key, "name": str(c.get("name") or key)[:128], "division": c["division"],
                        "description": str(c.get("description") or "")[:2000], "parent_key": c.get("parent_key") or None})
        elif op == "update_room":
            fields = dict(c.get("changes") or {})
            if c.get("key") not in rooms:
                raise ValueError(f"change {i}: unknown room {c.get('key')!r}")
            if not fields or set(fields) - EDITABLE:
                raise ValueError(f"change {i}: room fields are {', '.join(sorted(EDITABLE))}")
            out.append({"op": op, "key": c["key"], "changes": fields})
        elif op == "update_agent":
            fields = dict(c.get("changes") or {})
            if s.get(Agent, int(c.get("agent_id") or 0)) is None:
                raise ValueError(f"change {i}: unknown agent {c.get('agent_id')!r}")
            if not fields or set(fields) - AGENT_FIELDS:
                raise ValueError(f"change {i}: agent fields are {', '.join(sorted(AGENT_FIELDS))}")
            out.append({"op": op, "agent_id": int(c["agent_id"]), "changes": fields})
        else:
            raise ValueError(f"change {i}: op is create_room, update_room or update_agent")
    return out


def proposal_dict(p: Any) -> dict[str, Any]:
    return {"id": p.id, "title": p.title, "changes": p.changes or [], "current": p.current or {}, "reason": p.reason,
            "impact": p.impact, "evidence": p.evidence or [], "migration": p.migration, "status": p.status,
            "proposed_by": p.proposed_by, "decided_by": p.decided_by, "decision_note": p.decision_note,
            "version": p.version, "outcome": p.outcome, "created_at": p.created_at.isoformat() if p.created_at else None,
            "decided_at": p.decided_at.isoformat() if p.decided_at else None}


def propose(title: str, changes: list[dict[str, Any]], reason: str, impact: str = "", evidence: list[Any] | None = None,
            migration: str = "", actor: str = "system") -> dict[str, Any]:
    """Store a proposed organisation change; nothing changes until a person approves it (`decide_proposal`)."""
    from agent_company.db import OrgProposal
    from agent_company.governance import notify

    if not (title or "").strip() or not (reason or "").strip():
        raise ValueError("a proposal needs a title and a reason")
    with session() as s:
        p = OrgProposal(title=title.strip()[:256], changes=_check_changes(s, changes), current=_snapshot(s),
                        reason=reason.strip()[:4000], impact=(impact or "").strip()[:4000],
                        evidence=[str(e)[:500] for e in (evidence or [])][:20], migration=(migration or "").strip()[:4000],
                        proposed_by=actor[:128])
        s.add(p)
        s.flush()
        notify(f"Organisation change proposed: {p.title}"[:256], p.reason[:1000], level="info", source="organization",
               admin_only=True, s=s)
        return proposal_dict(p)


def list_proposals(status: str = "") -> list[dict[str, Any]]:
    from agent_company.db import OrgProposal

    with session() as s:
        q = select(OrgProposal).order_by(OrgProposal.id.desc())
        if status:
            q = q.where(OrgProposal.status == status)
        return [proposal_dict(p) for p in s.scalars(q)]


def decide_proposal(proposal_id: int, approve: bool, actor: str, note: str = "") -> dict[str, Any]:
    """A person's decision. Approval applies the changes in order through the organisation's own calls (their checks:
    cycles, running goals, the GM's room) and records the resulting version; a change that fails stops there, the
    proposal is `failed` with the reason and the changes before it stay (restore the version before it to undo)."""
    from agent_company import registry
    from agent_company.db import OrgProposal, OrgVersion

    if not actor.startswith("user:"):
        raise ValueError("only a person decides an organisation change")
    with session() as s:
        p = s.get(OrgProposal, proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if p.status != "pending":
            raise ValueError(f"proposal #{proposal_id} is {p.status}")
        if not approve:
            p.status, p.decided_by, p.decision_note, p.decided_at = "rejected", actor, note[:2000], now()
            return proposal_dict(p)
        changes = _check_changes(s, p.changes)  # the organisation may have moved since it was proposed
        before = record_version(s, actor, f"before organisation proposal #{proposal_id}")
    why, error = f"organisation proposal #{proposal_id}: {p.title}"[:500], ""
    try:
        for c in changes:
            if c["op"] == "create_room":
                create_room(c["key"], c["name"], c["division"], c["description"], c["parent_key"], actor=actor)
            elif c["op"] == "update_room":
                update_room(c["key"], dict(c["changes"]), actor, why)
            else:
                registry.update_agent(c["agent_id"], dict(c["changes"]), actor)
    except (ValueError, KeyError) as e:
        error = str(e) or type(e).__name__
    with session() as s:
        version = record_version(s, actor, why) or s.scalar(select(func.max(OrgVersion.version)))
        p = s.get(OrgProposal, proposal_id)
        p.status = "failed" if error else "approved"
        p.decided_by, p.decided_at, p.version = actor, now(), version
        p.decision_note = (note[:2000] + (f"\nNot fully applied: {error}" if error else "")).strip()
        if error and before:
            p.decision_note += f" (version {before} is the organisation before it)"
        return proposal_dict(p)


def review_outcome(proposal_id: int, outcome: str, actor: str) -> dict[str, Any]:
    """The outcome review of an applied change: did it have the expected impact?"""
    from agent_company.db import OrgProposal

    if not (outcome or "").strip():
        raise ValueError("write the outcome")
    with session() as s:
        p = s.get(OrgProposal, proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if p.status not in ("approved", "failed"):
            raise ValueError("only an applied change has an outcome")
        p.outcome = f"{outcome.strip()[:4000]}\n— {actor}"
        return proposal_dict(p)
