"""Agent Registry: rooms, agents, hierarchy, relationships and staffing.

Every configuration change of an agent is a numbered snapshot (`agent_versions`, SPEC-02 S02-R-01): creation,
card edits, lifecycle changes, emergency stops and their lifting, merges. A snapshot can be rolled back, and an
update based on a stale version is refused. Health (S02-R-02): an agent whose recent runs keep failing in the
executor, or whose model fails its test, is `degraded` and not staffed until an administrator resets it or its
model is changed; agents idle longer than `inactive_days` are listed for retirement review.
"""

from __future__ import annotations

import random
import re
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from agent_company import org, organization
from agent_company.executors import AUTONOMY, level
from agent_company.db import Agent, AgentVersion, Goal, Relationship, Room, log_event, now, session

_staff_lock = threading.Lock()


def agent_dict(a: Agent) -> dict[str, Any]:
    return {
        "id": a.id, "name": a.name, "role": a.role, "title": a.title, "room": a.room_key,
        "superior_id": a.superior_id, "parent_id": a.parent_id, "goal": a.goal,
        "skills": a.skills, "permissions": a.permissions, "autonomy": a.autonomy or "G3", "model_pref": a.model_pref,
        "status": a.status, "enabled": a.enabled, "xp": a.xp, "level": a.level,
        "tasks_done": a.tasks_done, "tasks_failed": a.tasks_failed, "playbook": a.playbook,
        "lifecycle": a.lifecycle, "playbook_version": a.playbook_version, "in_trial": bool(a.experiment),
        "plugins": list(a.plugins or []), "definition_version": a.definition_version or 0,
        "last_active_at": a.last_active_at.isoformat() if a.last_active_at else None,
        "config_version": a.config_version or 0, "health": a.health or "ok", "health_reason": a.health_reason or "",
        "max_concurrent": a.max_concurrent or 1, "daily_cost_quota_usd": a.daily_cost_quota_usd or 0.0,
        "active_goals": list(a.active_goals or []),
    }


# --- concurrency and quotas (SPEC-02 S02-R-03) -----------------------------------------------

class QuotaExceeded(RuntimeError):
    """Every agent that could take the work has used its daily cost quota."""


def day_start():  # noqa: ANN201
    """Quotas are per UTC day."""
    t = now()
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def _family(s: Session, a: Agent) -> tuple[Agent, list[int]]:
    base = s.get(Agent, a.parent_id) if a.parent_id else a
    base = base or a
    return base, [base.id, *s.scalars(select(Agent.id).where(Agent.parent_id == base.id))]


def spent_today(s: Session, a: Agent) -> float:
    """What the agent and its clones (the quota covers them together) spent since 00:00 UTC."""
    from agent_company.db import Run

    _, ids = _family(s, a)
    return float(s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(
        Run.agent_id.in_(ids), Run.started_at >= day_start().replace(tzinfo=None))) or 0.0)


def room_spent_today(s: Session, key: str, parents: dict[str, str | None] | None = None) -> float:
    """What the agents of a room and of every room below it spent since 00:00 UTC (SPEC-04 S04-R-09)."""
    from agent_company.db import Run

    parents = parents if parents is not None else dict(s.execute(select(Room.key, Room.parent_key)).all())
    tree, grew = {key}, True
    while grew:
        more = {k for k, p in parents.items() if p in tree and k not in tree}
        tree |= more
        grew = bool(more)
    return float(s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).join(Agent, Agent.id == Run.agent_id).where(
        Agent.room_key.in_(tree), Run.started_at >= day_start().replace(tzinfo=None))) or 0.0)


def room_over_budget(s: Session, room_key: str | None) -> str:
    """The first room on the way from `room_key` to the top whose daily budget its subtree has used up."""
    rooms = {r.key: r for r in s.scalars(select(Room))}
    parents = {k: r.parent_key for k, r in rooms.items()}
    seen: set[str] = set()
    key = room_key
    while key and key in rooms and key not in seen:
        seen.add(key)
        r = rooms[key]
        if (r.daily_budget_usd or 0) > 0:
            spent = room_spent_today(s, key, parents)
            if spent >= r.daily_budget_usd:
                return f"room {r.name} used ${spent:.2f} of its ${r.daily_budget_usd:.2f} daily budget"
        key = r.parent_key
    return ""


def over_quota(s: Session, a: Agent) -> str:
    """Why the agent may not take work today: its own (and its clones') quota, or the budget of its room or a room
    above it."""
    base, _ = _family(s, a)
    quota = base.daily_cost_quota_usd or 0.0
    if quota > 0:
        spent = spent_today(s, a)
        if spent >= quota:
            return f"{base.name} used ${spent:.2f} of its ${quota:.2f} daily quota"
    return room_over_budget(s, a.room_key)


def _other_projects(s: Session, agents: list[Agent], goal: Goal | None) -> dict[int, list[int]]:
    """For each agent working on a goal of another project than `goal`'s: those project ids
    (ASEAO05 S05-R-09). Goals without a project and benchmark goals never conflict."""
    if goal is None or goal.project_id is None or goal.origin == "eval":
        return {}
    ids = {gid for a in agents for gid in (a.active_goals or []) if gid}
    if not ids:
        return {}
    project = dict(s.execute(select(Goal.id, Goal.project_id).where(Goal.id.in_(ids), Goal.origin != "eval")).all())
    out: dict[int, list[int]] = {}
    for a in agents:
        found = sorted({project[gid] for gid in (a.active_goals or []) if project.get(gid) not in (None, goal.project_id)})
        if found:
            out[a.id] = found
    return out


def _log_conflict(s: Session, role: str, goal_id: int | None, conflict: dict[str, Any]) -> str:
    why = f"{conflict['agent']} works for project(s) {', '.join(f'#{x}' for x in conflict['projects'])}: " \
          f"{conflict['resolution']}"
    log_event("staffing_conflict", f"{role}: {why}", goal_id=goal_id, agent_id=conflict["shared_agent_id"], s=s,
              **conflict)
    return why


def _free(a: Agent, goal_id: int | None) -> bool:
    """A slot is free, and it is not already working on this goal (two of its runs would share a session)."""
    active = list(a.active_goals or [])
    return len(active) < max(1, a.max_concurrent or 1) and (goal_id is None or goal_id not in active)


# --- configuration versions (SPEC-02 S02-R-01) ------------------------------------------

# What a version records and a rollback restores. The playbook is versioned on its own (evolution.versions /
# restore) and the definition file in its git repository; skill levels grown by experience are not versions.
CONFIG_FIELDS = ("skills", "permissions", "autonomy", "model_pref", "superior_id", "room_key", "enabled", "lifecycle", "goal",
                 "plugins", "max_concurrent", "daily_cost_quota_usd")


class StaleVersion(ValueError):
    """An update was based on an older configuration version than the current one (AT-023)."""


def config_of(a: Agent) -> dict[str, Any]:
    snap = {k: getattr(a, k) for k in CONFIG_FIELDS}
    snap["skills"], snap["permissions"], snap["plugins"] = (dict(a.skills or {}), list(a.permissions or []),
                                                             list(a.plugins or []))
    return snap


def record_version(s: Session, a: Agent, changed_by: str, reason: str) -> int | None:
    """Snapshot the agent's configuration if it differs from its latest version; returns the new version."""
    snap = config_of(a)
    last = s.scalars(select(AgentVersion).where(AgentVersion.agent_id == a.id)
                     .order_by(AgentVersion.version.desc())).first()
    if last is not None and last.snapshot == snap:
        return None
    a.config_version = max(a.config_version or 0, last.version if last else 0) + 1
    s.add(AgentVersion(agent_id=a.id, version=a.config_version, snapshot=snap, changed_by=str(changed_by)[:128],
                       reason=str(reason)[:500]))
    s.flush()
    return a.config_version


def config_versions(agent_id: int) -> list[dict[str, Any]]:
    with session() as s:
        if s.get(Agent, agent_id) is None:
            raise KeyError(agent_id)
        return [{"version": v.version, "snapshot": v.snapshot, "changed_by": v.changed_by, "reason": v.reason,
                 "at": v.created_at.isoformat()}
                for v in s.scalars(select(AgentVersion).where(AgentVersion.agent_id == agent_id)
                                   .order_by(AgentVersion.version.desc()))]


def _check_model(s: Session, pref: Any, agent_id: int) -> None:
    """A model preference names a catalog model or another agent (`agent:<id>`) (SPEC-02 AT-008)."""
    from agent_company.db import ModelEntry

    if not pref:
        return
    pref = str(pref)
    if pref.startswith("agent:"):
        if pref[6:] == str(agent_id):
            raise ValueError("an agent cannot use itself as its model")
        if not pref[6:].isdigit() or s.get(Agent, int(pref[6:])) is None:
            raise ValueError(f"no agent {pref[6:]} to take the model from")
        return
    if s.scalars(select(ModelEntry.id).where(ModelEntry.name == pref)).first() is None:
        raise ValueError(f"'{pref}' is not in the model catalog")


# --- health and inactivity (SPEC-02 S02-R-02) ---------------------------------------------

HEALTH_WINDOW, DEGRADED_ERRORS = 5, 3  # 3 executor errors among the last 5 runs
STOPPED = "terminated by signal"  # an emergency stop or a cancel ended the run: not the agent's fault


def _set_health(s: Session, a: Agent, state: str, reason: str, reset: bool = False) -> bool:
    """Set the health; `reset` also starts a new window (earlier runs and model tests no longer count)."""
    if reset:
        a.health_since = now()
    was = a.health or "ok"
    a.health_reason = reason[:1000]  # the latest cause, even while it stays degraded
    if was == state:
        return False
    a.health = state
    a.degraded_at = now() if state == "degraded" else None
    a.recovery_probe_at, a.recovery_probes = None, 0
    log_event("agent_health", f"{a.name} is {state}" + (f": {reason}" if reason else ""), agent_id=a.id, s=s)
    if state == "degraded":  # once per degradation, not for every failing run after it
        from agent_company.governance import notify

        notify(f"Agent degraded: {a.name}"[:256], f"{reason} It is not staffed until an administrator resets its "
               "health (agent card) or changes its model, or a recovery probe of its model passes.", level="warning",
               source="agents", admin_only=True, s=s)
    return True


def _since(a: Agent):  # noqa: ANN202
    return a.health_since.replace(tzinfo=None) if a.health_since else None


def first_model(role: str, model_pref: str | None) -> str | None:
    """The model an agent asks for first: its preference (or the agent it borrows from), else its role's route.
    Not `router.resolve`, which moves on to a fallback when that model is unavailable."""
    from agent_company import router
    from agent_company.db import Route

    pref = router.agent_model(model_pref)[0]
    if pref:
        return pref
    with session() as s:
        r = s.get(Route, role)
        return r.model_name if r else None


def _counts(r) -> bool:  # noqa: ANN001
    """Stops and cancels, and refusals by the command policy (behaviour rules handle those), are not
    executor errors."""
    from agent_company.loop import NO_RETRY

    return not (STOPPED in (r.error or "") or NO_RETRY.search(r.error or ""))


MODEL_TEST = "failed its test"


def _health_reason(s: Session, a: Agent, first: str | None) -> str:
    from agent_company.db import Run

    q = select(Run).where(Run.agent_id == a.id, Run.purpose != "test")
    if _since(a) is not None:
        q = q.where(Run.started_at > _since(a))
    runs = [r for r in s.scalars(q.order_by(Run.id.desc()).limit(3 * HEALTH_WINDOW)) if r.ok or _counts(r)]
    runs = runs[:HEALTH_WINDOW]
    errors = [r for r in runs if not r.ok]
    # the latest run must have failed too: an agent that recovered (a retry or the next task succeeded) is fine
    if len(errors) >= DEGRADED_ERRORS and not runs[0].ok:
        return (f"{len(errors)} of its last {len(runs)} runs failed in the executor (last: "
                f"{(errors[0].error or 'no detail').strip()[:200]}).")
    if not first:
        return ""
    test = s.scalars(select(Run).where(Run.purpose == "test", Run.model_name == first).order_by(Run.id.desc())).first()
    if test is not None and not test.ok and (_since(a) is None or test.started_at.replace(tzinfo=None) > _since(a)):
        return f"its model {first} {MODEL_TEST} ({(test.error or 'no detail').strip()[:200]})."
    return ""


def check_health(agent_id: int) -> str:
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            return "ok"
        role, pref = a.role, a.model_pref
    first = first_model(role, pref)
    with session() as s:
        a = s.get(Agent, agent_id)
        reason = _health_reason(s, a, first)
        _set_health(s, a, "degraded" if reason else "ok", reason)
        return a.health


def after_run(agent_id: int) -> None:
    """Called for every recorded run (usage.record): the agent was active, and its health may have changed."""
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is not None:
            a.last_active_at = now()
    check_health(agent_id)


def recheck_model(model_name: str) -> list[int]:
    """After a model test: re-check the active agents that ask for that model first."""
    hit = []
    with session() as s:
        agents = [(a.id, a.role, a.model_pref) for a in s.scalars(select(Agent).where(Agent.lifecycle == "active"))]
    for aid, role, pref in agents:
        if first_model(role, pref) == model_name:
            check_health(aid)
            hit.append(aid)
    return hit


def reset_health(agent_id: int, actor: str) -> dict[str, Any]:
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        _set_health(s, a, "ok", "", reset=True)
        log_event("agent_health", f"{a.name}: health reset by {actor}", agent_id=a.id, s=s)
        return agent_dict(a)


RECOVERY_MAX_HOURS = 6


def _probe_due(a: Agent, minutes: int, at: Any) -> bool:
    start = a.recovery_probe_at or a.degraded_at
    if start is None:
        return True
    wait = min(minutes * 2 ** (a.recovery_probes or 0), RECOVERY_MAX_HOURS * 60)
    return start.replace(tzinfo=None) + timedelta(minutes=wait) <= at.replace(tzinfo=None)


def recover_degraded(at: Any = None) -> list[int]:
    """S02-FR-18 automatic runtime recovery. A degraded agent is never staffed, so no run of its own can show that it
    works again: every `health_recovery_minutes` (doubling after each failed probe, at most 6 h) its first model gets
    the model test, once per model per sweep. A passing probe starts a new health window, as an administrator's reset
    would; a failing one keeps it degraded until the next probe. 0 switches the probes off."""
    from agent_company import router
    from agent_company.config import settings
    from agent_company.db import ModelEntry

    minutes = settings().health_recovery_minutes
    if minutes <= 0:
        return []
    at = at or now()
    with session() as s:
        due = [(a.id, a.role, a.model_pref) for a in s.scalars(select(Agent).where(
            Agent.health == "degraded", Agent.lifecycle == "active", Agent.enabled)) if _probe_due(a, minutes, at)]
    results: dict[str, bool] = {}
    recovered = []
    for aid, role, pref in due:
        model = first_model(role, pref)
        if model and model not in results:
            with session() as s:
                m = s.scalars(select(ModelEntry).where(ModelEntry.name == model)).first()
                mid = m.id if m else None
            try:
                results[model] = bool(mid) and bool(router.test_model(mid).get("ok"))
            except Exception:  # noqa: BLE001 - a probe that cannot run counts as failed
                results[model] = False
        ok = bool(model) and results.get(model, False)
        with session() as s:
            a = s.get(Agent, aid)
            if a is None or a.health != "degraded":  # the model test's own re-check may have cleared it already
                if a is not None and ok:
                    recovered.append(aid)
                continue
            if ok:
                _set_health(s, a, "ok", "", reset=True)
                log_event("agent_health", f"{a.name} recovered automatically: a probe of {model} passed",
                          agent_id=a.id, s=s, model=model)
                from agent_company.governance import notify

                notify(f"Agent recovered: {a.name}"[:256], f"A recovery probe of {model} passed; it is staffed again.",
                       level="info", source="agents", admin_only=True, s=s)
                recovered.append(aid)
            else:
                a.recovery_probe_at, a.recovery_probes = at, (a.recovery_probes or 0) + 1
                log_event("agent_health", f"{a.name}: recovery probe of {model or 'no model'} failed "
                          f"({a.recovery_probes})", agent_id=a.id, s=s)
    return recovered


_recovering = threading.Lock()


def recovery_tick() -> None:
    """For the goal queue's watcher: a sweep in a thread of its own (a probe may take minutes), one at a time."""
    with session() as s:  # the quiet path: one query, no thread, nothing logged
        if s.scalar(select(Agent.id).where(Agent.health == "degraded", Agent.lifecycle == "active").limit(1)) is None:
            return
    if not _recovering.acquire(blocking=False):
        return

    def run() -> None:
        try:
            recover_degraded()
        except Exception as e:  # noqa: BLE001 - try again on the next tick
            log_event("error", f"recovery sweep failed: {e}")
        finally:
            _recovering.release()

    threading.Thread(target=run, daemon=True, name="agent-recovery").start()


def inactive(days: int | None = None) -> list[dict[str, Any]]:
    """Active original agents idle for more than `inactive_days`: candidates for retirement review. The company
    GM and clones are left out (clones are retired after their goal)."""
    from datetime import timedelta

    from agent_company.config import settings

    days = settings().inactive_days if days is None else days
    cutoff = (now() - timedelta(days=days)).replace(tzinfo=None)
    out = []
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.lifecycle == "active", Agent.parent_id.is_(None))):
            if a.role == "general_manager":
                continue
            last = (a.last_active_at or a.created_at).replace(tzinfo=None)
            if last < cutoff:
                out.append(agent_dict(a) | {"idle_days": (now().replace(tzinfo=None) - last).days,
                                            "never_active": a.last_active_at is None})
    return sorted(out, key=lambda x: -x["idle_days"])


def health_overview() -> dict[str, Any]:
    with session() as s:
        degraded = [agent_dict(a) for a in s.scalars(select(Agent).where(Agent.health == "degraded",
                                                                         Agent.lifecycle != "retired"))]
    return {"degraded": degraded, "inactive": inactive()}


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
                          permissions=list(spec.permissions), enabled=spec.enabled,
                          autonomy=spec.default_autonomy)
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
            if not a.config_version:  # agents from before versioning get their current state as version 1
                record_version(s, a, "system", "baseline")


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


# Relationship kinds (SPEC-12 S12-R-01). Symmetric ones are stored once per pair (lower id first); directed
# ones read "a <kind> b". Inferred kinds come from profiles, observed ones from verified work (network.py).
SYMMETRIC = {"similar", "collaborated", "complementary_skill", "research_partner", "testing_partner"}
DIRECTED = {"reviewed_by", "mentored_by", "depends_on", "handover_to", "supervises", "knowledge_contributor"}
KINDS = SYMMETRIC | DIRECTED
INFERRED = {"similar", "complementary_skill"}
# kinds whose agents share experience: their knowledge ranks higher for each other (learning.retrieve)
SHARING = {"similar", "collaborated", "complementary_skill", "mentored_by", "knowledge_contributor"}


def related(s: Session, agent_id: int, limit: int = 5) -> list[tuple[Agent, float]]:
    rels = s.scalars(select(Relationship).where(
        or_(Relationship.a_id == agent_id, Relationship.b_id == agent_id), Relationship.kind.in_(SHARING),
        Relationship.status != "expired")
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
    changes the link twice (duplicate records are ignored, SPEC-12 AR-015). A directed kind keeps its order."""
    if a_id == b_id or not a_id or not b_id:
        return
    lo, hi = (a_id, b_id) if kind in DIRECTED else sorted((a_id, b_id))
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


EVIDENCE_MIN, QUALITY_WINDOW = 3, 20  # fewer verified outcomes are insufficient evidence; older ones decay
WORKLOAD_COST = 0.2  # per goal the agent is already working on
_random = random.random  # tests replace it


def _quality(s: Session, ids: list[int]) -> dict[int, tuple[float | None, int]]:
    """(success rate, outcomes) over each agent's last QUALITY_WINDOW verified task outcomes (the lesson step's
    verdict per task). Fewer than EVIDENCE_MIN: no rate (insufficient evidence, not incompetence)."""
    from agent_company.db import Lesson

    out: dict[int, tuple[float | None, int]] = {}
    for i in ids:
        seen: dict[int, str] = {}
        for task_id, outcome in s.execute(select(Lesson.task_id, Lesson.outcome).where(
                Lesson.agent_id == i, Lesson.task_id.is_not(None), Lesson.kind.in_(("episodic", "negative")),
                Lesson.outcome.in_(("success", "failure"))).order_by(Lesson.id.desc()).limit(3 * QUALITY_WINDOW)):
            seen.setdefault(task_id, outcome)
        outcomes = list(seen.values())[:QUALITY_WINDOW]
        out[i] = (outcomes.count("success") / len(outcomes) if len(outcomes) >= EVIDENCE_MIN else None, len(outcomes))
    return out


def _rank(s: Session, agents: list[Agent], wanted: list[str], team: dict[int, float] | None = None
          ) -> dict[int, dict[str, Any]]:
    """Team utility of each candidate (SPEC-12 §7.3, §11.2, S12-R-03): skill coverage + experience + collaboration
    evidence with the agents already on the goal + quality − workload. An agent without enough evidence gets the
    pool's mean quality: neither a bonus nor a penalty."""
    quality = _quality(s, [a.id for a in agents])
    known = [q for q, _ in quality.values() if q is not None]
    neutral = sum(known) / len(known) if known else 0.0
    out = {}
    for a in agents:
        skills = a.skills or {}
        q, n = quality[a.id]
        parts = {"coverage": float(sum(skills.get(w, 0) for w in wanted)), "experience": round(0.05 * a.level, 3),
                 "team": round(2.0 * (team or {}).get(a.id, 0.0), 3),
                 "quality": round(q if q is not None else neutral, 3), "evidence": n,
                 "workload": round(-WORKLOAD_COST * len(a.active_goals or []), 3)}
        parts["score"] = round(parts["coverage"] + parts["experience"] + parts["team"] + parts["quality"]
                               + parts["workload"], 3)
        out[a.id] = parts
    return out


def _skill_score(s: Session, agent: Agent, wanted: list[str]) -> float:
    return _rank(s, [agent], wanted)[agent.id]["score"]


def _team_weights(s: Session, goal_id: int | None) -> dict[int, float]:
    """For each agent: summed 'collaborated' weight with the agents already working on this goal."""
    if goal_id is None:
        return {}
    from agent_company.db import Task

    team = set(s.scalars(select(Task.agent_id).where(Task.goal_id == goal_id, Task.agent_id.is_not(None))))
    if not team:
        return {}
    out: dict[int, float] = {}
    for r in s.scalars(select(Relationship).where(Relationship.kind == "collaborated", Relationship.status != "expired",
                                                  or_(Relationship.a_id.in_(team), Relationship.b_id.in_(team)))):
        for me, other in ((r.a_id, r.b_id), (r.b_id, r.a_id)):
            if other in team and me not in team:
                out[me] = out.get(me, 0.0) + r.weight
    return out


# Planner role names mapped onto existing roles before staffing (SPEC-03 S03-R-02).
ROLE_SYNONYMS = {
    "dev": "developer", "backend_dev": "developer", "backend_developer": "developer", "backend": "developer",
    "software_engineer": "developer", "software_developer": "developer", "engineer": "developer",
    "programmer": "developer", "coder": "developer", "implementer": "developer", "python_developer": "developer",
    "tester": "qa", "test_engineer": "qa", "qa_engineer": "qa", "quality_assurance": "qa", "reviewer": "qa",
    "code_reviewer": "qa", "verifier": "qa",
    "researcher": "research", "research_analyst": "research",
    "software_architect": "architect", "system_architect": "architect", "solution_architect": "architect",
    "designer": "architect",
    "planner": "general_manager", "manager": "general_manager", "project_manager": "general_manager",
    "pm": "general_manager", "gm": "general_manager",
}
REUSE_COVERAGE = 0.7  # an existing agent covering this share of a task's skills is reused, not recruited


def resolve_role(role: str) -> tuple[str, str | None]:
    """Map a planner role onto the catalog. Returns (role, note)."""
    role = _slug(role)
    if role in ROLE_SYNONYMS and role not in org.ROLES:
        return ROLE_SYNONYMS[role], f"role '{role}' is '{ROLE_SYNONYMS[role]}'"
    spec = org.ROLES.get(role)
    if spec is None or spec.enabled:
        return role, None
    with session() as s:
        off = organization.disabled_rooms(s)
        if s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "active",
                                         Agent.room_key.not_in(off))).first():
            return role, None
    fallback = org.FALLBACK_WRITE_ROLE if spec.writes else org.FALLBACK_READ_ROLE
    return fallback, f"role '{role}' is disabled; using '{fallback}'"


def staff(role: str, skills: list[str] | None = None, goal_id: int | None = None,
          writes: bool | None = None, critical: bool = False, task_id: int | None = None) -> Agent:
    """Return an agent for the role and mark it busy.

    Picks the best idle enabled agent by team utility (`_rank`). With probability `explore_rate`, on a
    non-critical task of a user's goal, the best idle agent without enough evidence is given the chance instead
    (SPEC-12 S12-R-03). If all are busy the best one is cloned; if the role does not exist a new specialist is
    recruited and linked to similar agents so it can draw on their experience. With `task_id` the decision, its
    candidates and their scores are recorded in the task's contract (`staffing`, AR-018).
    """
    from agent_company.config import settings

    skills = [_slug(x) for x in (skills or [])]
    why = ""
    ranks: dict[int, dict[str, Any]] = {}
    explored = False
    conflict: dict[str, Any] | None = None
    with _staff_lock, session() as s:
        off = organization.disabled_rooms(s)  # S01-R-01: a disabled room's agents are not staffed
        pool = [a for a in s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "active"))
                if level(a.autonomy) > 0 and a.room_key not in off]  # G0 observes only (S14-R-02)
        from agent_company import teams

        gone = teams.released(s, goal_id)  # S04-R-11: released from this goal's team, with their clones
        if gone and pool:
            pool = [a for a in pool if a.id not in gone and a.parent_id not in gone]
            if not pool:
                raise RuntimeError(f"every {role} agent was released from goal #{goal_id}'s team; add one back "
                                   "or plan without the role")
        g = s.get(Goal, goal_id) if goal_id else None
        quota ={a.id: over_quota(s, a) for a in pool}  # S02-R-03: the quota covers an agent and its clones
        idle = [a for a in pool if _free(a, goal_id) and a.health != "degraded" and not quota[a.id]]  # S02-R-02
        team = _team_weights(s, goal_id)
        trainee = _trainee(s, goal_id, pool, role, writes)  # an exercise or experiment measures one agent (SPEC-13)
        if trainee is not None and trainee.id not in quota:  # a candidate in its qualification run (SPEC-03)
            quota[trainee.id] = over_quota(s, trainee)
        if trainee is not None and (trainee in idle or (trainee not in pool and _free(trainee, goal_id)
                                                         and not quota[trainee.id])):
            agent, action = trainee, "assigned"
        elif trainee is not None and not quota[trainee.id]:  # busy: its clone, which merges back after the goal
            agent, action = _clone(s, trainee), "cloned"
        elif idle:
            ranks = _rank(s, idle, skills, team)
            agent = max(idle, key=lambda a: ranks[a.id]["score"])
            action = "assigned"
            others = _other_projects(s, idle, g)
            if agent.id in others:  # S05-R-09: the best agent works for another project's goal right now
                free = [a for a in idle if a.id not in others]
                conflict = {"shared_agent_id": agent.id, "agent": agent.name, "projects": others[agent.id],
                            "project_id": g.project_id if g is not None else None}
                if free:
                    agent = max(free, key=lambda a: ranks[a.id]["score"])
                    conflict["resolution"] = f"free agent {agent.name}"
                else:
                    agent, action = _clone(s, agent), "cloned"
                    conflict["resolution"] = f"clone {agent.name}"
                idle = free
                why = _log_conflict(s, role, goal_id, conflict)
            new = [a for a in idle if ranks[a.id]["evidence"] < EVIDENCE_MIN]
            if (action == "assigned" and new and agent not in new and not critical
                    and not (g is not None and g.origin == "eval") and _random() < settings().explore_rate):
                agent, explored = max(new, key=lambda a: ranks[a.id]["score"]), True
                why = f"exploration: {agent.name} has {ranks[agent.id]['evidence']} verified outcome(s) so far"
        elif pool:  # all busy, degraded or over quota: a fresh clone (a clone starts healthy, with its own runs)
            if all(quota.values()):  # a clone would spend from the same quota
                raise QuotaExceeded(f"every {role} agent has used its daily quota ({next(iter(quota.values()))}); "
                                    "it resets at 00:00 UTC")
            pool = [a for a in pool if not quota[a.id]]
            healthy = [a for a in pool if a.health != "degraded"]
            if not healthy and all(MODEL_TEST in (a.health_reason or "") for a in pool):
                # a clone would ask for the same failing model: cloning cannot help
                raise RuntimeError(f"every {role} agent is degraded because its model fails its test; fix the "
                                   "model, give the agent another one, or reset its health")
            healthy = healthy or pool
            ranks = _rank(s, healthy, skills, team)
            base = max(healthy, key=lambda a: ranks[a.id]["score"])
            others = _other_projects(s, [base], g)
            agent = _clone(s, base)
            action = "cloned"
            if any(a.status == "idle" and a.health == "degraded" for a in pool):
                why = "its idle agents are degraded"
            elif others:  # S05-R-09: the agent the goal needed works for another project's goal
                conflict = {"shared_agent_id": base.id, "agent": base.name, "projects": others[base.id],
                            "project_id": g.project_id if g is not None else None,
                            "resolution": f"clone {agent.name}"}
                why = _log_conflict(s, role, goal_id, conflict)
        elif off and s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "active",
                                                   Agent.room_key.in_(off))).first():
            # an administrator disabled their room: a clone or a recruit would walk around that decision
            raise RuntimeError(f"every {role} agent is in a disabled room; an administrator enables the room")
        elif s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "paused")).first():
            # an emergency stop (E1/E4) or an administrator paused this role's agents: recruiting a
            # replacement would walk around the stop
            raise RuntimeError(f"every {role} agent is paused; lift the stop or resume one first")
        elif s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "active")).first():
            # an administrator set them to autonomy G0: a clone or a recruit would walk around that decision
            raise RuntimeError(f"every {role} agent is at autonomy G0 (observe only); an administrator raises it")
        else:  # nobody holds the role: a recruitment request (SPEC-03 S03-R-03), approved by policy or a person
            from agent_company import recruitment

            found = _covering(s, skills, writes)
            if found is not None:  # discovery before recruitment (SPEC-03 S03-R-02): no duplicate specialist
                base, share = found
                if over_quota(s, base):
                    raise QuotaExceeded(f"{over_quota(s, base)}; it covers {', '.join(skills)} (resets at 00:00 UTC)")
                reuse = _free(base, goal_id) and base.health != "degraded"
                why = f"{base.role} {base.name} covers {share:.0%} of {', '.join(skills)}: no new '{role}' recruited"
                rec = recruitment.open_request(s, role, skills, writes, goal_id, task_id,
                                               "reuse" if reuse else "clone", why)
                agent = base if reuse else _clone(s, base)
                action = "reused" if agent is base else "cloned"
            else:
                rec = recruitment.open_request(s, role, skills, writes, goal_id, task_id, "recruit",
                                               f"no active agent covers {REUSE_COVERAGE:.0%} of "
                                               f"{', '.join(skills) or role}")
                if rec.agent_id is None:
                    agent = _recruit(s, rec)
                    recruitment.on_recruited(s, rec, agent)
                else:  # approve() registered it already
                    agent = s.get(Agent, rec.agent_id)
                action = "recruited"
            why = f"{why} (recruitment #{rec.id})".strip()
        agent.active_goals = [*(agent.active_goals or []), goal_id or 0]
        agent.status = "busy"
        agent.last_active_at = now()
        names = dict(s.execute(select(Agent.id, Agent.name).where(Agent.id.in_(list(ranks)))).all()) if ranks else {}
        decision = {"action": action, "agent_id": agent.id, "agent": agent.name, "explored": explored, "why": why,
                    "conflict": conflict,
                    "skills": skills, "critical": critical, "gap": _gap(s, skills) if task_id is not None else {},
                    "candidates": [{"id": i, "name": names.get(i), **p} for i, p in
                                   sorted(ranks.items(), key=lambda x: -x[1]["score"])[:6]]}
        if task_id is not None:
            from agent_company.db import Task

            t = s.get(Task, task_id)
            if t is not None:
                from agent_company import kgraph

                t.contract = {**(t.contract or {}), "staffing": decision}
                kgraph.link(s, ("task", task_id), "ASSIGNED_TO", ("agent", agent.id))
        s.flush()
        s.expunge(agent)
    log_event("staff", f"{action} {agent.name} as {role}" + (f" ({why})" if why else ""), goal_id=goal_id,
              agent_id=agent.id)
    return agent


def _trainee(s: Session, goal_id: int | None, pool: list[Agent], role: str = "",
             writes: bool | None = None) -> Agent | None:
    """The agent an eval goal's trial names (a learning plan's trainee, a playbook experiment's agent), if it is
    in this role's pool. A recruit's qualification run (SPEC-03 S03-R-04) gives its candidate every writing task
    whatever the role, never a review: the suites are planned with the catalog's roles."""
    if goal_id is None:
        return None
    g = s.get(Goal, goal_id)
    trial = (g.trial or {}) if g is not None and g.origin == "eval" else {}
    tid = trial.get("trainee_id")
    found = next((a for a in pool if a.id == tid), None) if tid else None
    if found is not None or not trial.get("qualify") or not writes:
        return found
    from agent_company.quality import REVIEW_ROLES

    a = s.get(Agent, tid)
    if role in REVIEW_ROLES or a is None or not a.enabled or a.lifecycle not in {"candidate", "active"}:
        return None
    return a if "write" in (a.permissions or []) and level(a.autonomy) >= 2 else None


def _covering(s: Session, skills: list[str], writes: bool | None) -> tuple[Agent, float] | None:
    """The active agent whose skills cover the most of `skills`, if it covers at least REUSE_COVERAGE."""
    if not skills:
        return None
    best: tuple[Agent, float] | None = None
    off = organization.disabled_rooms(s)
    for a in s.scalars(select(Agent).where(Agent.enabled, Agent.lifecycle == "active", Agent.parent_id.is_(None))):
        if level(a.autonomy) == 0 or a.room_key in off or (writes and ("write" not in (a.permissions or []) or level(a.autonomy) < 2)):
            continue
        share = len(set(skills) & set(a.skills or {})) / len(set(skills))
        if share >= REUSE_COVERAGE and (best is None or share > best[1]
                                        or (share == best[1] and _skill_score(s, a, skills) > _skill_score(s, best[0], skills))):
            best = (a, share)
    return best


def duplicates(threshold: float = 0.9) -> list[dict[str, Any]]:
    """Pairs of active original agents with near-identical role and skills: candidates to consolidate."""
    with session() as s:
        agents = list(s.scalars(select(Agent).where(Agent.lifecycle == "active", Agent.parent_id.is_(None))))
        out = []
        for i, a in enumerate(agents):
            for b in agents[i + 1:]:
                score = similarity(a, b)
                if score >= threshold:
                    out.append({"a": {"id": a.id, "name": a.name}, "b": {"id": b.id, "name": b.name},
                                "role": a.role if a.role == b.role else f"{a.role}/{b.role}", "similarity": score})
        return sorted(out, key=lambda x: -x["similarity"])


def _clone(s: Session, src: Agent) -> Agent:
    base = s.get(Agent, src.parent_id) if src.parent_id else src
    n = s.scalar(select(func.count()).select_from(Agent).where(Agent.parent_id == base.id)) or 0
    name = f"{base.name} #{n + 2}"
    while s.scalars(select(Agent).where(Agent.name == name)).first():
        n += 1
        name = f"{base.name} #{n + 2}"
    clone = Agent(name=name, role=base.role, title=base.title, room_key=base.room_key,
                  lifecycle="candidate" if base.lifecycle == "candidate" else "active",
                  superior_id=base.superior_id, parent_id=base.id, goal=base.goal,
                  backstory=base.backstory, skills=dict(base.skills), permissions=list(base.permissions),
                  autonomy=base.autonomy or "G3", model_pref=base.model_pref, playbook=base.playbook, enabled=True,
                  max_concurrent=base.max_concurrent or 1)
    s.add(clone)
    s.flush()
    connect(s, clone.id, base.id, "similar", 0.7)
    _link_new(s, clone, list(base.skills or {}))
    record_version(s, clone, "system", f"cloned from {base.name}")
    return clone


def _link_new(s: Session, agent: Agent, skills: list[str]) -> None:
    """Complementary specialists and a mentor candidate for a new agent (SPEC-12 S12-R-02)."""
    from agent_company import network

    s.flush()
    network.link_on_creation(s, agent, skills)


def _gap(s: Session, skills: list[str]) -> dict[str, Any]:
    """The workforce's capability gap on the task's skills, one unit each (SPEC-03 S03-R-01), for the record."""
    from agent_company import recruitment

    if not skills:
        return {}
    return {k: {x: v[x] for x in ("required", "available", "gap")}
            for k, v in recruitment.capability_gap({k: 1.0 for k in skills}, s).items()}


def _recruit(s: Session, rec: Any) -> Agent:
    """A new specialist from a recruitment record's job description (SPEC-03 S03-R-03). It starts on probation
    (S03-R-04) at G2, with no more permissions than its role template (S03-R-05)."""
    from agent_company import recruitment

    role = rec.requested_role
    jd = rec.job_description or {}
    title = jd.get("title") or role.replace("_", " ").title() + " Specialist"
    skills = list(jd.get("expertise") or [])
    name, n = title, 1
    while s.scalars(select(Agent).where(Agent.name == name)).first():
        n += 1
        name = f"{title} #{n}"
    fields = recruitment.recruit_fields(rec)
    agent = Agent(name=name, role=role, title=title, room_key=fields["room_key"], superior_id=fields["superior_id"],
                  goal=fields["goal"], backstory=fields["backstory"],
                  skills={k: 5 for k in skills} or {role: 5}, permissions=fields["permissions"], enabled=True,
                  autonomy="G2",  # a new, unproven specialist gets no MCP tools until an administrator raises it
                  probation_tasks_left=recruitment.PROBATION_TASKS)
    s.add(agent)
    s.flush()
    _link_similar(s, agent, threshold=0.05)  # a recruit always learns from its closest peers
    _link_new(s, agent, skills)
    record_version(s, agent, "system", f"recruited for {role} (recruitment #{rec.id})")
    return agent


def release(agent_id: int, goal_id: int | None = None) -> None:
    """One task of the agent ended (for `goal_id`): its slot is free; it is idle once no task is left."""
    name = None
    with _staff_lock, session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            return
        active = list(a.active_goals or [])
        key = goal_id or 0
        if key in active:
            active.remove(key)
        elif active:
            active.pop()
        a.active_goals = active
        if not active and a.status == "busy":
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
        parents = {r.key: r.parent_key for r in rooms}
        return [organization.room_dict(r, counts.get(r.key, 0))
                | ({"spent_today_usd": round(room_spent_today(s, r.key, parents), 4)} if r.daily_budget_usd else {})
                for r in rooms]


EDITABLE = {"enabled", "model_pref", "skills", "playbook", "superior_id", "goal", "permissions", "autonomy", "plugins",
            "max_concurrent", "daily_cost_quota_usd"}
PERMISSIONS = {"read", "write", "shell", "network"}


def update_agent(agent_id: int, changes: dict[str, Any], actor: str = "system",
                 expected_version: int | None = None) -> dict[str, Any]:
    """Update an agent and record the new configuration version. Names are fixed by design and cannot be edited.
    With `expected_version`, an update made on an older version than the current one is refused (StaleVersion)."""
    bad = set(changes) - EDITABLE
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        if expected_version is not None and int(expected_version) != (a.config_version or 0):
            raise StaleVersion(f"{a.name} was changed meanwhile (now version {a.config_version}, this edit was "
                               f"made on version {expected_version}); reload and edit again")
        if "superior_id" in changes and changes["superior_id"] == agent_id:
            raise ValueError("an agent cannot report to itself")
        if "permissions" in changes and not set(changes["permissions"]) <= PERMISSIONS:
            raise ValueError(f"permissions are a subset of {sorted(PERMISSIONS)}")
        if "autonomy" in changes and changes["autonomy"] not in AUTONOMY:
            raise ValueError(f"autonomy is one of {', '.join(AUTONOMY)} (G5 is reserved to humans)")
        if a.lifecycle == "retired" and changes.get("enabled"):
            raise ValueError("a retired agent cannot be enabled")
        if "max_concurrent" in changes:
            try:
                changes["max_concurrent"] = int(changes["max_concurrent"])
            except (TypeError, ValueError):
                changes["max_concurrent"] = 0
            if not 1 <= changes["max_concurrent"] <= 8:
                raise ValueError("max_concurrent is 1 to 8")
        if "daily_cost_quota_usd" in changes:
            try:
                changes["daily_cost_quota_usd"] = round(float(changes["daily_cost_quota_usd"] or 0), 4)
            except (TypeError, ValueError):
                changes["daily_cost_quota_usd"] = -1.0
            if not 0 <= changes["daily_cost_quota_usd"] <= 10_000:
                raise ValueError("daily_cost_quota_usd is 0 (none) to 10000")
        if "plugins" in changes and not (isinstance(changes["plugins"], list)
                                         and all(isinstance(x, str) and len(x) <= 64 for x in changes["plugins"])):
            raise ValueError("plugins is a list of catalog names")
        if "model_pref" in changes:
            _check_model(s, changes["model_pref"], agent_id)
            if (changes["model_pref"] or None) != a.model_pref and a.health == "degraded":
                _set_health(s, a, "ok", f"model changed by {actor}", reset=True)
        for k, v in changes.items():
            setattr(a, k, v)
        s.flush()
        record_version(s, a, actor, "changed " + ", ".join(sorted(changes)))
        return agent_dict(a)


def rollback(agent_id: int, version: int, actor: str) -> dict[str, Any]:
    """Restore the configuration of an earlier version, recorded as a new version (S02-R-01, AT-016)."""
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        v = s.scalars(select(AgentVersion).where(AgentVersion.agent_id == agent_id,
                                                 AgentVersion.version == version)).first()
        if v is None:
            raise KeyError(f"agent {agent_id} version {version}")
        snap = dict(v.snapshot)
        if a.lifecycle == "retired":
            raise ValueError("retirement is permanent")
        if a.lifecycle == "candidate" or snap.get("lifecycle") == "candidate":
            raise ValueError("a candidate's lifecycle follows its qualification run (Evolution → Recruitment)")
        if snap.get("lifecycle") == "retired":
            raise ValueError("rolling back to a retired version would retire the agent; retire it instead")
        if snap.get("lifecycle") != "active" and a.role == "general_manager" and a.parent_id is None:
            raise ValueError("the company General Manager cannot be paused or retired")
        if snap.get("lifecycle") != a.lifecycle and a.status == "busy":
            raise ValueError("the agent is working; try again when it is idle")
        _check_model(s, snap.get("model_pref"), agent_id)
        if snap.get("superior_id") is not None and s.get(Agent, snap["superior_id"]) is None:
            snap["superior_id"] = None
        for k in CONFIG_FIELDS:
            if k in snap:
                setattr(a, k, snap[k])
        s.flush()
        record_version(s, a, actor, f"rolled back to v{version}")
        log_event("agent", f"{a.name}: configuration rolled back to v{version} by {actor}", agent_id=a.id, s=s)
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
        d["lessons"] = [{"id": lesson.id, "text": lesson.text, "outcome": lesson.outcome, "kind": lesson.kind,
                         "status": lesson.status, "goal_id": lesson.goal_id, "at": lesson.created_at.isoformat()}
                        for lesson in s.scalars(select(Lesson).where(Lesson.agent_id == a.id)
                                                .order_by(Lesson.id.desc()).limit(20))]
        from agent_company.db import Handover

        d["handovers"] = [{"goal_id": h.goal_id, "task": h.task_key, "from": h.from_agent_id, "to": h.to_agent_id,
                           "package": h.package, "at": h.created_at.isoformat()}
                          for h in s.scalars(select(Handover).where(
                              or_(Handover.from_agent_id == a.id, Handover.to_agent_id == a.id))
                              .order_by(Handover.id.desc()).limit(10))]
        from agent_company.db import ProjectHandover

        d["handovers"] += [{"project_id": h.project_id, "task": "General Manager", "from": h.from_agent_id,
                            "to": h.to_agent_id, "package": h.package, "at": h.created_at.isoformat()}
                           for h in s.scalars(select(ProjectHandover).where(
                               or_(ProjectHandover.from_agent_id == a.id, ProjectHandover.to_agent_id == a.id))
                               .order_by(ProjectHandover.id.desc()).limit(10))]
        d["handovers"].sort(key=lambda h: h["at"], reverse=True)
        d["spent_today"] = round(spent_today(s, a), 6)
        d["quota_reason"] = over_quota(s, a)
        d["trial_playbook"] = (a.experiment or {}).get("playbook")
        d["trial_outcomes"] = (a.experiment or {}).get("outcomes", [])
        runs = s.scalars(select(Run).where(Run.agent_id == a.id)).all()
        d["usage"] = {"runs": len(runs), "input_tokens": sum(r.input_tokens for r in runs),
                      "output_tokens": sum(r.output_tokens for r in runs),
                      "cost": round(sum(r.cost for r in runs), 6)}
        d["workload"] = workload(s, a.id)
        return d


def workload(s: Session, agent_id: int) -> dict[str, Any]:
    """Open tasks, hours active in the last 24 h (model runs and research visits) and the last handover the
    agent gave or received (SPEC-12 S12-R-05)."""
    from agent_company.db import Handover, ResearchShift, Run, Task

    since = now() - timedelta(hours=24)
    cut = since.replace(tzinfo=None)
    open_tasks = s.scalar(select(func.count()).select_from(Task).where(
        Task.agent_id == agent_id, Task.status.in_(("queued", "running")))) or 0
    seconds = sum(s.scalars(select(Run.duration).where(Run.agent_id == agent_id, Run.started_at >= since)))
    last: list[tuple[datetime, dict[str, Any]]] = []
    for sh in s.scalars(select(ResearchShift).where(ResearchShift.agent_id == agent_id,
                                                    or_(ResearchShift.ended_at.is_(None),
                                                        ResearchShift.ended_at >= since))):
        seconds += sum(v.get("seconds", 0) for v in (sh.state or {}).get("sources", [])
                       if datetime.fromisoformat(v["retrieved_at"]).replace(tzinfo=None) >= cut)
    for sh in s.scalars(select(ResearchShift).where(ResearchShift.agent_id == agent_id)
                        .order_by(ResearchShift.id.desc()).limit(2)):
        if sh.ended_at is not None:
            last.append((sh.ended_at, {"kind": "shift", "direction": "gave", "status": sh.status, "shift_id": sh.id,
                                       "at": sh.ended_at.isoformat()}))
        if sh.handover:
            last.append((sh.started_at, {"kind": "shift", "direction": "received", "status": "received",
                                         "shift_id": sh.id, "at": sh.started_at.isoformat()}))
    h = s.scalars(select(Handover).where(or_(Handover.from_agent_id == agent_id, Handover.to_agent_id == agent_id))
                  .order_by(Handover.id.desc())).first()
    if h is not None:
        last.append((h.created_at, {"kind": "task", "direction": "gave" if h.from_agent_id == agent_id else "received",
                                    "status": h.status, "goal_id": h.goal_id, "at": h.created_at.isoformat()}))
    latest = max(last, key=lambda x: x[0].replace(tzinfo=None))[1] if last else None
    return {"open_tasks": open_tasks, "hours_24h": round(seconds / 3600, 2), "last_handover": latest}


# --- manual management and lifecycle (SPEC02 §6: active, paused, retired) -------------

def create_agent(role: str, title: str = "", skills: dict[str, int] | None = None, room: str = "",
                 superior_id: int | None = None, goal: str = "", writes: bool = False, actor: str = "system"
                 ) -> dict[str, Any]:
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
                  autonomy=spec.default_autonomy if spec else "G2", enabled=True)
        s.add(a)
        s.flush()
        _link_similar(s, a, threshold=0.05)
        _link_new(s, a, list(a.skills or {}))
        record_version(s, a, actor, "created")
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


def set_lifecycle(agent_id: int, state: str, actor: str = "system") -> dict[str, Any]:
    if state not in {"active", "paused", "retired"}:
        raise ValueError("state is active, paused or retired")
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        if a.lifecycle == "retired":
            raise ValueError("retirement is permanent")
        if a.lifecycle == "candidate" and state != "retired":
            raise ValueError("a candidate becomes active by passing its qualification run; it can only be retired")
        if state != "active" and a.role == "general_manager" and a.parent_id is None:
            raise ValueError("the company General Manager cannot be paused or retired")
        if state != "active" and a.status == "busy":
            raise ValueError("the agent is working; try again when it is idle")
        a.lifecycle, a.enabled = state, state == "active"
        record_version(s, a, actor, f"lifecycle {state}")
        return agent_dict(a)
