"""Recruitment (SPEC-03): capability gaps, recruitment records with job descriptions, approval and probation.

  gap        S03-R-01: for skill s, G_s = R_s - A_s with A_s = sum_i P_i,s x C_i x V_i,s over the active original
             agents: P = skill level / PROFICIENT (at most 1: one proficient agent is one unit), C = the share of the agent's concurrent slots that is free (0 when it
             is paused, disabled, degraded or at G0), V = 1, or 0.5 for a recruit's self-assigned skill until a
             final evaluation certifies it (SPEC-13 S13-R-03). Units are planning estimates.
  request    S03-R-03: when a task needs a role nobody holds, `registry.staff` writes one record per (goal, role):
             the job description (from the role template and the task contract, versioned), the gap, the
             decision reuse|clone|recruit with its reason, and later the agent it produced.
  approval   S03-R-05: setting `recruit_requires_approval` = never | permanent_only | always. A record that needs
             approval is `pending`; the task is skipped and the goal pauses (`paused_for = "recruitment"`) until an
             administrator approves (the agent is registered once: approving again changes nothing) or rejects it
             (the goal resumes and the planner gets the refusal). A recruit never gets more permissions than its
             role template; shell and network for a role outside the catalog wait for an administrator's grant.
  probation  S03-R-04: a recruit starts with PROBATION_TASKS tasks on probation. Each of its writing tasks is
             reviewed by another agent (the loop adds a QA review; reviews avoid the writer's provider), and the
             gate fails a probationer's change without a passing review. A verified pass counts down; after
             PROBATION_MAX_FAILURES failed tasks of its own the recruit is retired (its lessons stay).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company import org
from agent_company.db import Agent, Goal, Recruitment, Task, log_event, now, session

PROBATION_TASKS = 3
PROBATION_MAX_FAILURES = 2
SELF_ASSIGNED = 0.5  # verification factor of a recruit's uncertified skill
PROFICIENT = 5  # the skill level that counts as one unit (a recruit claims it for each skill of its task)
GRANTED_BY_ADMIN = {"shell", "network"}  # never given to a role outside the catalog without an administrator


class RecruitmentPending(RuntimeError):
    """The task needs a recruit that waits for an administrator's approval."""


class RecruitmentRefused(RuntimeError):
    """An administrator rejected the recruitment this task needs."""


# --- capability gap (S03-R-01) ---------------------------------------------------------------

def _recruits(s: Session) -> set[int]:
    return set(s.scalars(select(Recruitment.agent_id).where(Recruitment.decision == "recruit",
                                                            Recruitment.agent_id.is_not(None))))


def _availability(a: Agent) -> float:
    from agent_company.executors import level

    if not a.enabled or a.lifecycle != "active" or a.health == "degraded" or level(a.autonomy) == 0:
        return 0.0
    slots = max(1, a.max_concurrent or 1)
    return max(0.0, 1 - len(a.active_goals or []) / slots)


def available(s: Session, skills: list[str]) -> dict[str, dict[str, Any]]:
    """A_s per skill, with each contributing agent's P, C and V."""
    from agent_company.training import certified

    recruits = _recruits(s)
    out: dict[str, dict[str, Any]] = {k: {"available": 0.0, "agents": []} for k in skills}
    for a in s.scalars(select(Agent).where(Agent.lifecycle == "active", Agent.parent_id.is_(None))):
        held = [k for k in skills if (a.skills or {}).get(k)]
        if not held:
            continue
        certs = certified(a.id, s)
        c = _availability(a)
        for k in held:
            p = min(1.0, int((a.skills or {})[k]) / PROFICIENT)
            cert = certs.get(k, {})
            v = 1.0 if (a.id not in recruits or (cert.get("status") == "passed" and cert.get("level", 0) > 0)) \
                else SELF_ASSIGNED
            out[k]["available"] += p * c * v
            out[k]["agents"].append({"id": a.id, "name": a.name, "p": p, "c": round(c, 3), "v": v})
    return out


def capability_gap(required: dict[str, float], s: Session | None = None) -> dict[str, dict[str, Any]]:
    """{skill: {required, available, gap, agents}} for the required amount of each skill."""
    def run(s: Session) -> dict[str, dict[str, Any]]:
        have = available(s, list(required))
        return {k: {"required": r, "available": round(have[k]["available"], 2),
                    "gap": round(max(0.0, r - have[k]["available"]), 2), "agents": have[k]["agents"]}
                for k, r in required.items()}

    if s is not None:
        return run(s)
    with session() as own:
        return run(own)


def project_gap(project_id: int) -> dict[str, Any]:
    """The project's capability gap: R_s = the number of its unfinished tasks needing s, at least 1 for each of
    the project's required skills."""
    from agent_company.db import Project
    from agent_company.registry import _slug

    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        required = {_slug(k): 1.0 for k in (p.required_skills or [])}
        open_goals = list(s.scalars(select(Goal.id).where(
            Goal.project_id == project_id, Goal.origin != "eval",
            Goal.status.in_(("queued", "planning", "running", "paused", "recovering")))))
        counts: dict[str, float] = {}
        if open_goals:
            for t in s.scalars(select(Task).where(Task.goal_id.in_(open_goals),
                                                  Task.status.in_(("queued", "running", "skipped")))):
                for k in {_slug(x) for x in (t.contract or {}).get("skills", [])}:
                    counts[k] = counts.get(k, 0.0) + 1
        for k, n in counts.items():
            required[k] = max(required.get(k, 0.0), n)
        gap = capability_gap(required, s)
    rows = sorted(({"skill": k, **v} for k, v in gap.items()), key=lambda r: (-r["gap"], r["skill"]))
    return {"project_id": project_id, "skills": rows, "missing": [r["skill"] for r in rows if r["gap"] > 0]}


# --- job description and record (S03-R-03) ---------------------------------------------------

def template_for(role: str, writes: bool | None) -> str:
    if role in org.ROLES:
        return role
    return org.FALLBACK_WRITE_ROLE if writes else org.FALLBACK_READ_ROLE


def job_description(s: Session, role: str, skills: list[str], writes: bool | None, task: Task | None,
                    lifecycle: str) -> tuple[dict[str, Any], str, list[str]]:
    """(job description, template, permissions held back for an administrator). Built from the role template and
    the task's contract; no model is called (it runs inside the staffing lock)."""
    template = template_for(role, writes)
    spec = org.ROLES[template]
    perms = [p for p in spec.permissions if writes or p != "write"] if writes is not None else list(spec.permissions)
    held = [p for p in perms if p in GRANTED_BY_ADMIN] if role not in org.ROLES else []
    perms = [p for p in perms if p not in held]
    gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
    lead = s.scalars(select(Agent).where(Agent.role == ("architect" if writes else "research"),
                                         Agent.parent_id.is_(None))).first()
    boss = lead or gm
    contract = dict(task.contract or {}) if task is not None else {}
    label = role.replace("_", " ")
    title = label.title() + " Specialist"
    work = contract.get("title") or f"{label} work"
    accept = [c.get("type", "") + (f": {c.get('cmd') or c.get('pattern') or c.get('path') or ''}".rstrip(": "))
              for c in contract.get("acceptance", []) if isinstance(c, dict)]
    jd = {
        "title": title, "role": role,
        "summary": f"Handle {label} work to a high standard, starting with: {work}"[:400],
        "responsibilities": [f"Deliver '{work}' so that its acceptance conditions pass",
                             *([str(contract["description"])[:300]] if contract.get("description") else []),
                             "Record what you learn so related agents can reuse it"],
        "expertise": skills or [role],
        "tools": (["read files", "change files in the goal's worktree"] if "write" in perms else ["read files"])
                 + (["run commands"] if "shell" in perms else []),
        "permissions": perms,
        "reporting_manager": {"id": boss.id, "name": boss.name} if boss else None,
        "room": "development" if writes else "information_collection",
        "evaluation_criteria": [*accept[:6],
                                f"first {PROBATION_TASKS} tasks are reviewed by QA on another provider",
                                f"{PROBATION_MAX_FAILURES} failed tasks during probation retire the agent"],
        "budget": dict(contract.get("budget") or {}),
        "lifecycle": lifecycle,
    }
    return jd, template, held


def _row(r: Recruitment, names: dict[int, str]) -> dict[str, Any]:
    return {"id": r.id, "goal_id": r.goal_id, "task_id": r.task_id, "requested_role": r.requested_role,
            "required_skills": r.required_skills or [], "preferred_skills": r.preferred_skills or [],
            "job_description": r.job_description or {}, "jd_version": r.jd_version, "history": r.history or [],
            "template": r.template, "gap": r.gap or {}, "decision": r.decision, "reason": r.reason,
            "lifecycle": r.lifecycle, "status": r.status, "pending_permissions": r.pending_permissions or [],
            "agent_id": r.agent_id, "agent": names.get(r.agent_id) if r.agent_id else None,
            "evaluation_cost_usd": r.evaluation_cost_usd, "decided_by": r.decided_by, "note": r.note,
            "created_at": r.created_at.isoformat(), "decided_at": r.decided_at.isoformat() if r.decided_at else None}


def needs_approval(decision: str) -> bool:
    from agent_company.config import settings

    policy = settings().recruit_requires_approval
    return (policy == "always" and decision in {"clone", "recruit"}) or (policy == "permanent_only"
                                                                          and decision == "recruit")


def open_request(s: Session, role: str, skills: list[str], writes: bool | None, goal_id: int | None,
                 task_id: int | None, decision: str, reason: str) -> Recruitment:
    """The goal's record for this role (one per goal and role, so parallel tasks and later iterations share it),
    created when missing. Raises RecruitmentPending / RecruitmentRefused while it is not approved."""
    from agent_company.governance import notify

    rec = s.scalars(select(Recruitment).where(Recruitment.goal_id == goal_id, Recruitment.requested_role == role)
                    .order_by(Recruitment.id.desc())).first() if goal_id is not None else None
    if rec is None:
        task = s.get(Task, task_id) if task_id is not None else None
        lifecycle = "permanent" if decision == "recruit" else "temporary"
        jd, template, held = job_description(s, role, skills, writes, task, lifecycle)
        gap = capability_gap({k: 1.0 for k in skills}, s) if skills else {}
        rec = Recruitment(goal_id=goal_id, task_id=task_id, requested_role=role,
                          required_skills=[k for k, v in gap.items() if v["gap"] > 0],
                          preferred_skills=[k for k, v in gap.items() if v["gap"] <= 0],
                          job_description=jd, template=template,
                          gap={k: {x: v[x] for x in ("required", "available", "gap")} for k, v in gap.items()},
                          decision=decision, reason=reason, lifecycle=lifecycle,
                          pending_permissions=held if decision == "recruit" else [])
        s.add(rec)
        s.flush()
        if needs_approval(decision):
            g = s.get(Goal, goal_id) if goal_id else None
            notify(f"Recruitment #{rec.id} waits for approval: {jd['title']}"[:256],
                   f"Goal #{goal_id}: {decision} for role '{role}' ({', '.join(skills) or role}). {reason}".strip(),
                   level="warning", source="recruitment", goal_id=goal_id, project_id=g.project_id if g else None,
                   admin_only=True, s=s)
            log_event("recruitment", f"recruitment #{rec.id} for '{role}' waits for approval", goal_id=goal_id, s=s,
                      recruitment_id=rec.id)
        else:
            rec.status, rec.decided_by, rec.decided_at = "approved", "policy", now()
    if rec.status == "pending":
        s.commit()  # the record and its notification stay when the caller's session rolls back on the raise
        raise RecruitmentPending(f"recruitment #{rec.id} ({rec.decision} for role '{role}') waits for an "
                                 "administrator's approval")
    if rec.status in {"rejected", "cancelled"}:
        raise RecruitmentRefused(f"recruitment #{rec.id} for role '{role}' was {rec.status} by {rec.decided_by}"
                                 + (f": {rec.note}" if rec.note else "")
                                 + ". Plan the work with the roles the company has.")
    return rec


def on_recruited(s: Session, rec: Recruitment, agent: Agent) -> None:
    from agent_company.governance import notify

    rec.agent_id = agent.id
    if rec.pending_permissions:
        notify(f"{agent.name} needs {', '.join(rec.pending_permissions)}"[:256],
               f"Recruitment #{rec.id} for the new role '{rec.requested_role}' holds back "
               f"{', '.join(rec.pending_permissions)}: an administrator grants it (Evolution → Recruitment).",
               level="warning", source="recruitment", goal_id=rec.goal_id, admin_only=True, s=s)


def recruit_fields(rec: Recruitment) -> dict[str, Any]:
    """The new agent's description from its job description, not a generic sentence."""
    jd = rec.job_description or {}
    boss = (jd.get("reporting_manager") or {}).get("name")
    backstory = (f"You are a newly recruited {jd.get('title', 'specialist').lower()}. Responsibilities: "
                 + "; ".join(jd.get("responsibilities", [])) + f". Expertise: {', '.join(jd.get('expertise', []))}."
                 + (f" You report to {boss}." if boss else "")
                 + f" Your first {PROBATION_TASKS} tasks are reviewed. Draw on the experience of related agents "
                 "shown in your context.")
    return {"goal": jd.get("summary", ""), "backstory": backstory, "permissions": list(jd.get("permissions", ["read"])),
            "room_key": jd.get("room", "development"),
            "superior_id": (jd.get("reporting_manager") or {}).get("id")}


# --- administrator decisions (S03-R-05) -------------------------------------------------------

def list_recruitments(status: str = "", goal_id: int | None = None) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Recruitment).order_by(Recruitment.id.desc())
        if status:
            q = q.where(Recruitment.status == status)
        if goal_id is not None:
            q = q.where(Recruitment.goal_id == goal_id)
        rows = list(s.scalars(q.limit(500)))
        names = dict(s.execute(select(Agent.id, Agent.name).where(
            Agent.id.in_([r.agent_id for r in rows if r.agent_id]))).all())
        return [_row(r, names) for r in rows]


def get(recruitment_id: int) -> dict[str, Any]:
    with session() as s:
        r = s.get(Recruitment, recruitment_id)
        if r is None:
            raise KeyError(recruitment_id)
        names = dict(s.execute(select(Agent.id, Agent.name).where(Agent.id == r.agent_id)).all()) if r.agent_id else {}
        return _row(r, names)


def revise(recruitment_id: int, changes: dict[str, Any], actor: str) -> dict[str, Any]:
    """A new version of a pending record's job description; the earlier one is kept in `history` (AT-005)."""
    allowed = {"summary", "responsibilities", "expertise", "evaluation_criteria", "budget", "title"}
    bad = set(changes) - allowed
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    with session() as s:
        r = s.get(Recruitment, recruitment_id)
        if r is None:
            raise KeyError(recruitment_id)
        if r.status != "pending":
            raise ValueError(f"recruitment #{r.id} is {r.status}; only a pending job description can be revised")
        r.history = [*(r.history or []), {"version": r.jd_version, "job_description": r.job_description,
                                          "by": actor, "at": now().isoformat()}]
        r.job_description = {**(r.job_description or {}), **changes}
        r.jd_version += 1
    return get(recruitment_id)


def _resume_goal(goal_id: int | None, actor: str) -> bool:
    if goal_id is None:
        return False
    from agent_company import loop

    with session() as s:
        g = s.get(Goal, goal_id)
        waiting = g is not None and g.status == "paused" and (g.checkpoint or {}).get("paused_for") == "recruitment"
        others = s.scalars(select(Recruitment.id).where(Recruitment.goal_id == goal_id,
                                                        Recruitment.status == "pending")).first()
    if not waiting or others is not None:
        return False
    try:
        loop.resume(goal_id, actor)
    except ValueError as e:  # e.g. its project was paused meanwhile: an administrator resumes it later
        log_event("recruitment", f"goal #{goal_id} stays paused: {e}", goal_id=goal_id)
        return False
    return True


def approve(recruitment_id: int, actor: str, grant: bool = True) -> dict[str, Any]:
    """Approve a pending record: a recruit is registered now (once: approving again returns the same agent).
    With `grant`, the permissions held back for a new role are given as well. A paused goal resumes."""
    from agent_company import registry

    with registry._staff_lock, session() as s:
        r = s.get(Recruitment, recruitment_id)
        if r is None:
            raise KeyError(recruitment_id)
        if r.status in {"rejected", "cancelled"}:
            raise ValueError(f"recruitment #{r.id} was {r.status}")
        was = r.status
        if r.status == "pending":
            r.status, r.decided_by, r.decided_at = "approved", actor, now()
            if r.decision == "recruit" and r.agent_id is None:
                agent = registry._recruit(s, r)
                on_recruited(s, r, agent)
        if grant and r.pending_permissions and r.agent_id:
            a = s.get(Agent, r.agent_id)
            if a is not None:
                a.permissions = [*(a.permissions or []), *[p for p in r.pending_permissions
                                                           if p not in (a.permissions or [])]]
                registry.record_version(s, a, actor, f"granted {', '.join(r.pending_permissions)} "
                                                     f"(recruitment #{r.id})")
                r.pending_permissions = []
        log_event("recruitment", f"recruitment #{r.id} approved by {actor}" if was == "pending"
                  else f"recruitment #{r.id} was already approved", goal_id=r.goal_id, agent_id=r.agent_id, s=s,
                  recruitment_id=r.id)
        goal_id = r.goal_id
    out = get(recruitment_id)
    out["resumed"] = _resume_goal(goal_id, actor) if was == "pending" else False
    return out


def reject(recruitment_id: int, actor: str, reason: str = "", status: str = "rejected") -> dict[str, Any]:
    """Reject (or cancel) a pending record. The goal resumes; its task fails with the refusal, so the planner
    re-plans with the roles the company has."""
    if status not in {"rejected", "cancelled"}:
        raise ValueError("status is rejected or cancelled")
    with session() as s:
        r = s.get(Recruitment, recruitment_id)
        if r is None:
            raise KeyError(recruitment_id)
        if r.status != "pending":
            raise ValueError(f"recruitment #{r.id} is {r.status}; only a pending one can be {status}")
        r.status, r.decided_by, r.decided_at, r.note = status, actor, now(), reason[:2000]
        log_event("recruitment", f"recruitment #{r.id} {status} by {actor}", goal_id=r.goal_id, s=s,
                  recruitment_id=r.id)
        goal_id = r.goal_id
    out = get(recruitment_id)
    out["resumed"] = _resume_goal(goal_id, actor)
    return out


# --- probation (S03-R-04) ---------------------------------------------------------------------

def on_probation(s: Session, agent_id: int | None) -> bool:
    """True when the agent (or the original it was cloned from) is on probation."""
    a = s.get(Agent, agent_id) if agent_id else None
    if a is None:
        return False
    base = s.get(Agent, a.parent_id) if a.parent_id else a
    return bool(base is not None and (base.probation_tasks_left or 0) > 0)


def after_task(agent_id: int, task_id: int | None, success: bool) -> str:
    """Count a verified task outcome of a probationer. A pass counts down; a failure counts only when the agent's
    own task failed (a gate failure caused by another task is not its failure). Returns what happened."""
    from agent_company import registry

    with session() as s:
        a = s.get(Agent, agent_id)
        base = s.get(Agent, a.parent_id) if a is not None and a.parent_id else a
        if base is None or (base.probation_tasks_left or 0) <= 0 or base.lifecycle == "retired":
            return ""
        t = s.get(Task, task_id) if task_id else None
        if success:
            base.probation_tasks_left -= 1
            if base.probation_tasks_left == 0:
                registry.record_version(s, base, "probation", f"passed probation ({PROBATION_TASKS} tasks)")
                log_event("recruitment", f"{base.name} passed probation", agent_id=base.id, s=s)
                return "passed"
            return "counted"
        if t is None or t.status == "done":
            return ""
        base.probation_failures = (base.probation_failures or 0) + 1
        if base.probation_failures < PROBATION_MAX_FAILURES:
            return "failed"
        # retired directly: `set_lifecycle` refuses a busy agent, and the task that just ended released it
        base.lifecycle, base.enabled, base.probation_tasks_left = "retired", False, 0
        registry.record_version(s, base, "probation", f"retired after {base.probation_failures} failed tasks "
                                                      "during probation")
        log_event("recruitment", f"{base.name} retired: {base.probation_failures} failed tasks during probation; "
                  "its lessons are kept", agent_id=base.id, s=s)
        from agent_company.governance import notify

        notify(f"{base.name} retired during probation"[:256],
               f"{base.probation_failures} of its tasks failed before it finished probation.", level="warning",
               source="recruitment", admin_only=True, s=s)
        return "retired"


def probation_tasks(goal_id: int, iteration: int) -> list[dict[str, Any]]:
    """Writing tasks of this iteration finished by an agent on probation."""
    with session() as s:
        return [{"id": t.id, "key": t.key, "agent_id": t.agent_id, "title": t.title}
                for t in s.scalars(select(Task).where(Task.goal_id == goal_id, Task.iteration == iteration,
                                                      Task.writes, Task.status == "done"))
                if on_probation(s, t.agent_id)]


def effectiveness() -> dict[str, Any]:
    """Recruitment effectiveness (S03-R-04): how recruits fared on probation."""
    with session() as s:
        rows = list(s.scalars(select(Recruitment).where(Recruitment.decision == "recruit",
                                                        Recruitment.agent_id.is_not(None))))
        out = []
        for r in rows:
            a = s.get(Agent, r.agent_id)
            if a is None:
                continue
            left, failed = a.probation_tasks_left or 0, a.probation_failures or 0
            state = ("retired" if failed >= PROBATION_MAX_FAILURES else
                     "withdrawn" if left > 0 and a.lifecycle == "retired" else  # an administrator retired it
                     "on probation" if left > 0 else "passed")
            out.append({"recruitment_id": r.id, "agent_id": a.id, "agent": a.name, "role": r.requested_role,
                        "passed_tasks": PROBATION_TASKS - left if state != "retired" else None,
                        "failed_tasks": failed, "state": state, "evaluation_cost_usd": r.evaluation_cost_usd})
        done = [x for x in out if x["state"] in {"passed", "retired"}]
        passed = [x for x in done if x["state"] == "passed"]
        by_status: dict[str, int] = {}
        for st in s.scalars(select(Recruitment.status)):
            by_status[st] = by_status.get(st, 0) + 1
    return {"recruits": out, "finished": len(done), "passed": len(passed),
            "pass_rate": round(len(passed) / len(done), 3) if done else None, "requests": by_status}
