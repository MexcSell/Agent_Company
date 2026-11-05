"""Agent Registry: rooms, agents, hierarchy, relationships and staffing.

Every configuration change of an agent is a numbered snapshot (`agent_versions`, SPEC-02 S02-R-01): creation,
card edits, lifecycle changes, emergency stops and their lifting, merges. A snapshot can be rolled back, and an
update based on a stale version is refused. Health (S02-R-02): an agent whose recent runs keep failing in the
executor, or whose model fails its test, is `degraded` and not staffed until an administrator resets it or its
model is changed; agents idle longer than `inactive_days` are listed for retirement review.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from agent_company import org
from agent_company.db import Agent, AgentVersion, Goal, Relationship, Room, log_event, now, session

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


def over_quota(s: Session, a: Agent) -> str:
    base, _ = _family(s, a)
    quota = base.daily_cost_quota_usd or 0.0
    if quota <= 0:
        return ""
    spent = spent_today(s, a)
    return f"{base.name} used ${spent:.2f} of its ${quota:.2f} daily quota" if spent >= quota else ""


def _free(a: Agent, goal_id: int | None) -> bool:
    """A slot is free, and it is not already working on this goal (two of its runs would share a session)."""
    active = list(a.active_goals or [])
    return len(active) < max(1, a.max_concurrent or 1) and (goal_id is None or goal_id not in active)


# --- configuration versions (SPEC-02 S02-R-01) ------------------------------------------

# What a version records and a rollback restores. The playbook is versioned on its own (evolution.versions /
# restore) and the definition file in its git repository; skill levels grown by experience are not versions.
CONFIG_FIELDS = ("skills", "permissions", "model_pref", "superior_id", "room_key", "enabled", "lifecycle", "goal",
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
    log_event("agent_health", f"{a.name} is {state}" + (f": {reason}" if reason else ""), agent_id=a.id, s=s)
    if state == "degraded":  # once per degradation, not for every failing run after it
        from agent_company.governance import notify

        notify(f"Agent degraded: {a.name}"[:256], f"{reason} It is not staffed until an administrator resets its "
               "health (agent card) or changes its model.", level="warning", source="agents", admin_only=True, s=s)
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
    why = ""
    with _staff_lock, session() as s:
        pool = s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "active")).all()
        quota = {a.id: over_quota(s, a) for a in pool}  # S02-R-03: the quota covers an agent and its clones
        idle = [a for a in pool if _free(a, goal_id) and a.health != "degraded" and not quota[a.id]]  # S02-R-02
        team = _team_weights(s, goal_id)
        trainee = _trainee(s, goal_id, pool)  # an exercise or experiment measures one agent (SPEC-13)
        if trainee is not None and trainee in idle:
            agent, action = trainee, "assigned"
        elif trainee is not None and not quota[trainee.id]:  # busy: its clone, which merges back after the goal
            agent, action = _clone(s, trainee), "cloned"
        elif idle:
            agent = max(idle, key=lambda a: _skill_score(a, skills, team))
            action = "assigned"
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
            agent = _clone(s, max(healthy, key=lambda a: _skill_score(a, skills, team)))
            action = "cloned"
            if any(a.status == "idle" and a.health == "degraded" for a in pool):
                why = "its idle agents are degraded"
        elif s.scalars(select(Agent).where(Agent.role == role, Agent.enabled, Agent.lifecycle == "paused")).first():
            # an emergency stop (E1/E4) or an administrator paused this role's agents: recruiting a
            # replacement would walk around the stop
            raise RuntimeError(f"every {role} agent is paused; lift the stop or resume one first")
        else:
            found = _covering(s, skills, writes)
            if found is not None:  # discovery before recruitment (SPEC-03 S03-R-02): no duplicate specialist
                base, share = found
                if over_quota(s, base):
                    raise QuotaExceeded(f"{over_quota(s, base)}; it covers {', '.join(skills)} (resets at 00:00 UTC)")
                agent = base if _free(base, goal_id) and base.health != "degraded" else _clone(s, base)
                action = "reused" if agent is base else "cloned"
                why = f"{base.role} {base.name} covers {share:.0%} of {', '.join(skills)}: no new '{role}' recruited"
            else:
                agent = _recruit(s, role, skills, writes)
                action = "recruited"
        agent.active_goals = [*(agent.active_goals or []), goal_id or 0]
        agent.status = "busy"
        agent.last_active_at = now()
        s.flush()
        s.expunge(agent)
    log_event("staff", f"{action} {agent.name} as {role}" + (f" ({why})" if why else ""), goal_id=goal_id,
              agent_id=agent.id)
    return agent


def _trainee(s: Session, goal_id: int | None, pool: list[Agent]) -> Agent | None:
    """The agent an eval goal's trial names (a learning plan's trainee, a playbook experiment's agent), if it is
    in this role's pool."""
    if goal_id is None:
        return None
    g = s.get(Goal, goal_id)
    tid = (g.trial or {}).get("trainee_id") if g is not None and g.origin == "eval" else None
    return next((a for a in pool if a.id == tid), None) if tid else None


def _covering(s: Session, skills: list[str], writes: bool | None) -> tuple[Agent, float] | None:
    """The active agent whose skills cover the most of `skills`, if it covers at least REUSE_COVERAGE."""
    if not skills:
        return None
    best: tuple[Agent, float] | None = None
    for a in s.scalars(select(Agent).where(Agent.enabled, Agent.lifecycle == "active", Agent.parent_id.is_(None))):
        if writes and "write" not in (a.permissions or []):
            continue
        share = len(set(skills) & set(a.skills or {})) / len(set(skills))
        if share >= REUSE_COVERAGE and (best is None or share > best[1]
                                        or (share == best[1] and _skill_score(a, skills) > _skill_score(best[0], skills))):
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
                  superior_id=base.superior_id, parent_id=base.id, goal=base.goal,
                  backstory=base.backstory, skills=dict(base.skills), permissions=list(base.permissions),
                  model_pref=base.model_pref, playbook=base.playbook, enabled=True,
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
    _link_new(s, agent, skills)
    record_version(s, agent, "system", f"recruited for {role}")
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
        return [{"key": r.key, "name": r.name, "division": r.division, "description": r.description,
                 "rotational": r.rotational, "agents": counts.get(r.key, 0)} for r in rooms]


EDITABLE = {"enabled", "model_pref", "skills", "playbook", "superior_id", "goal", "permissions", "plugins",
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
        d["spent_today"] = round(spent_today(s, a), 6)
        d["quota_reason"] = over_quota(s, a)
        d["trial_playbook"] = (a.experiment or {}).get("playbook")
        d["trial_outcomes"] = (a.experiment or {}).get("outcomes", [])
        runs = s.scalars(select(Run).where(Run.agent_id == a.id)).all()
        d["usage"] = {"runs": len(runs), "input_tokens": sum(r.input_tokens for r in runs),
                      "output_tokens": sum(r.output_tokens for r in runs),
                      "cost": round(sum(r.cost for r in runs), 6)}
        return d


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
                  enabled=True)
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
        if state != "active" and a.role == "general_manager" and a.parent_id is None:
            raise ValueError("the company General Manager cannot be paused or retired")
        if state != "active" and a.status == "busy":
            raise ValueError("the agent is working; try again when it is idle")
        a.lifecycle, a.enabled = state, state == "active"
        record_version(s, a, actor, f"lifecycle {state}")
        return agent_dict(a)
