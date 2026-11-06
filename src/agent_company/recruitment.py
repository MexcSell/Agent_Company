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
             PROBATION_MAX_FAILURES failed tasks of its own (its task failed, or a review of it failed) the recruit
             is retired (its lessons stay).
  qualify    S03-R-04: with `qualify_recruits` on, a new recruit of a writing role is first registered as a
             `candidate` (lifecycle: never staffed for users' work, not counted as available) and runs an eval suite
             in scratch repositories (a practice/benchmark suite naming one of its skills, else `smoke`); the eval
             goals give it their writing tasks whatever its role, reviews stay with others. Passing at
             QUALIFY_PASS_RATE makes the record pending (the approval policy) or approved, and the agent active on
             probation; failing rejects the record and retires the candidate. The run's cost is the record's
             evaluation cost. The goal waits (`paused_for = "recruitment"`) until then.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company import org
from agent_company.config import settings
from agent_company.db import Agent, Goal, Recruitment, Task, log_event, now, session

PROBATION_TASKS = 3
PROBATION_MAX_FAILURES = 2
SELF_ASSIGNED = 0.5  # verification factor of a recruit's uncertified skill
PROFICIENT = 5  # the skill level that counts as one unit (a recruit claims it for each skill of its task)
GRANTED_BY_ADMIN = {"shell", "network"}  # never given to a role outside the catalog without an administrator
QUALIFY_SUITE = "smoke"  # the qualification suite of a writing role when no suite names one of its skills
QUALIFY_KINDS = frozenset({"practice", "benchmark"})  # suite kinds a qualification run may use
QUALIFY_PASS_RATE = 2 / 3
WAITING = ("pending", "qualifying")  # a goal with such a record waits for it
_qualifying: set[int] = set()  # records whose qualification run is queued or running in this process
_qualifying_lock = threading.Lock()


def _spawn(fn: Callable[[], Any]) -> None:  # tests replace it
    threading.Thread(target=fn, daemon=True, name="qualification").start()


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
            "evaluation_cost_usd": r.evaluation_cost_usd, "qualification": r.qualification or {},
            "decided_by": r.decided_by, "note": r.note,
            "created_at": r.created_at.isoformat(), "decided_at": r.decided_at.isoformat() if r.decided_at else None}


def needs_approval(decision: str, goal_id: int | None = None, s: Session | None = None) -> bool:
    """By `recruit_requires_approval`, made stricter by the goal's project's own rule (policies.effective)."""
    from agent_company.policies import effective

    project_id = None
    if goal_id is not None and s is not None:
        g = s.get(Goal, goal_id)
        project_id = g.project_id if g else None
    policy = effective("recruit_requires_approval", project_id, s)
    return (policy == "always" and decision in {"clone", "recruit"}) or (policy == "permanent_only"
                                                                          and decision == "recruit")


def open_request(s: Session, role: str, skills: list[str], writes: bool | None, goal_id: int | None,
                 task_id: int | None, decision: str, reason: str) -> Recruitment:
    """The goal's record for this role (one per goal and role, so parallel tasks and later iterations share it),
    created when missing. Raises RecruitmentPending / RecruitmentRefused while it is not approved."""
    from agent_company.governance import notify

    rec = s.scalars(select(Recruitment).where(Recruitment.goal_id == goal_id, Recruitment.requested_role == role)
                    .order_by(Recruitment.id.desc())).first() if goal_id is not None else None
    if rec is not None and rec.agent_id is not None and rec.status == "approved":
        # its recruit was retired or disabled since: ask again (a rejected one, e.g. not qualified, stays refused)
        a = s.get(Agent, rec.agent_id)
        if a is None or a.lifecycle != "active" or not a.enabled:
            rec = None
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
        suite = qualification_suite(skills) if decision == "recruit" and writes and settings().qualify_recruits else ""
        if suite:  # S03-R-04: a candidate first, approval (by policy or a person) after its run
            rec.status, rec.qualification = "qualifying", {"suite": suite, "status": "queued"}
            log_event("recruitment", f"recruitment #{rec.id} for '{role}': the candidate runs the {suite} suite "
                      "first", goal_id=goal_id, s=s, recruitment_id=rec.id)
        else:
            if decision == "recruit" and settings().qualify_recruits:
                rec.qualification = {"status": "skipped", "reason": "no eval suite matches the role" if writes
                                     else "the suites measure writing work; a reading role starts on probation"}
            _ask_or_approve(s, rec, notify)
    if rec.status == "qualifying":
        s.commit()  # the record stays when the caller's session rolls back on the raise
        start_qualification(rec.id)
        raise RecruitmentPending(f"recruitment #{rec.id}: the candidate for role '{role}' takes its qualification "
                                 f"run ({(rec.qualification or {}).get('suite', QUALIFY_SUITE)} suite) first")
    if rec.status == "pending":
        s.commit()  # the record and its notification stay when the caller's session rolls back on the raise
        raise RecruitmentPending(f"recruitment #{rec.id} ({rec.decision} for role '{role}') waits for an "
                                 "administrator's approval")
    if rec.status in {"rejected", "cancelled"}:
        raise RecruitmentRefused(f"recruitment #{rec.id} for role '{role}' was {rec.status} by {rec.decided_by}"
                                 + (f": {rec.note}" if rec.note else "")
                                 + ". Plan the work with the roles the company has.")
    return rec


def _ask_or_approve(s: Session, rec: Recruitment, notify: Callable[..., Any]) -> None:
    """The approval policy (S03-R-05): the record waits for an administrator, or is approved by policy."""
    if needs_approval(rec.decision, rec.goal_id, s):
        g = s.get(Goal, rec.goal_id) if rec.goal_id else None
        jd = rec.job_description or {}
        rec.status = "pending"
        done = (rec.qualification or {}).get("status") == "passed"
        notify(f"Recruitment #{rec.id} waits for approval: {jd.get('title', rec.requested_role)}"[:256],
               f"Goal #{rec.goal_id}: {rec.decision} for role '{rec.requested_role}' "
               f"({', '.join(jd.get('expertise') or []) or rec.requested_role}). {rec.reason}".strip()
               + (f" The candidate passed its qualification run ({rec.qualification.get('pass_rate', 0):.0%})."
                  if done else ""),
               level="warning", source="recruitment", goal_id=rec.goal_id, project_id=g.project_id if g else None,
               admin_only=True, s=s)
        log_event("recruitment", f"recruitment #{rec.id} for '{rec.requested_role}' waits for approval",
                  goal_id=rec.goal_id, s=s, recruitment_id=rec.id)
    else:
        rec.status, rec.decided_by, rec.decided_at = "approved", "policy", now()


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
                                                        Recruitment.status.in_(WAITING))).first()
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
        if r.status == "qualifying":
            raise ValueError(f"recruitment #{r.id} waits for its candidate's qualification run")
        was = r.status
        if r.status == "pending":
            r.status, r.decided_by, r.decided_at = "approved", actor, now()
            if r.decision == "recruit" and r.agent_id is None:
                agent = registry._recruit(s, r)
                on_recruited(s, r, agent)
            elif r.agent_id is not None:  # a candidate that passed its qualification run
                _activate(s, r, actor)
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
    """Reject (or cancel) a pending record, or one whose candidate is still qualifying (the candidate is retired).
    The goal resumes; its task fails with the refusal, so the planner re-plans with the roles the company has."""
    if status not in {"rejected", "cancelled"}:
        raise ValueError("status is rejected or cancelled")
    with session() as s:
        r = s.get(Recruitment, recruitment_id)
        if r is None:
            raise KeyError(recruitment_id)
        if r.status not in WAITING:
            raise ValueError(f"recruitment #{r.id} is {r.status}; only a pending one can be {status}")
        r.status, r.decided_by, r.decided_at, r.note = status, actor, now(), reason[:2000]
        _retire_candidate(s, r, f"recruitment #{r.id} {status} by {actor}")
        log_event("recruitment", f"recruitment #{r.id} {status} by {actor}", goal_id=r.goal_id, s=s,
                  recruitment_id=r.id)
        goal_id = r.goal_id
    out = get(recruitment_id)
    out["resumed"] = _resume_goal(goal_id, actor)
    return out


# --- qualification (S03-R-04) ------------------------------------------------------------------

def qualification_suite(skills: list[str]) -> str:
    """A suite of a QUALIFY_KINDS kind naming one of the skills, else QUALIFY_SUITE; '' when there is none. Every
    other kind is skipped: run_suite refuses or misuses research, room and retrieval suites (a room suite asks for
    a gated room's trial, a research or retrieval one is no task suite for a writing candidate), and final and
    adversarial suites stay for certification (SPEC-13 S13-R-03)."""
    from agent_company import evaluation
    from agent_company.training import _slug

    suites = [x for x in evaluation.list_suites() if x["kind"] in QUALIFY_KINDS]
    wanted = {_slug(k) for k in skills}
    named = next((x["name"] for x in suites if wanted & {_slug(k) for k in x["skills"]}), "")
    return named or next((x["name"] for x in suites if x["name"] == QUALIFY_SUITE), "")


def start_qualification(recruitment_id: int) -> bool:
    """Run the record's qualification in the background, unless it runs already in this process."""
    with _qualifying_lock:
        if recruitment_id in _qualifying:
            return False
        _qualifying.add(recruitment_id)
    _spawn(lambda: qualify(recruitment_id))
    return True


def resume_qualifications() -> int:
    """Qualification runs a restart cut off start again (evolution.cycle)."""
    with session() as s:
        ids = list(s.scalars(select(Recruitment.id).where(Recruitment.status == "qualifying")))
    return sum(start_qualification(i) for i in ids)


def qualify(recruitment_id: int) -> dict[str, Any]:
    """The candidate (registered now if it is not yet) runs the qualification suite; its verdict decides."""
    from agent_company import evaluation, registry

    try:
        with registry._staff_lock, session() as s:
            r = s.get(Recruitment, recruitment_id)
            if r is None or r.status != "qualifying":
                return {}
            if r.agent_id is None:
                agent = registry._recruit(s, r)
                agent.lifecycle = "candidate"  # never staffed for users' work until it passes (AT-007)
                registry.record_version(s, agent, "qualification", f"candidate for recruitment #{r.id}")
                r.agent_id = agent.id
            agent_id, suite = r.agent_id, (r.qualification or {}).get("suite") or QUALIFY_SUITE
            label = f"qualify{r.id}-{int(time.time() * 1000)}"
            r.qualification = {**(r.qualification or {}), "suite": suite, "status": "running", "label": label,
                               "started_at": now().isoformat()}
            log_event("recruitment", f"recruitment #{r.id}: candidate runs the {suite} suite", goal_id=r.goal_id,
                      agent_id=agent_id, s=s, recruitment_id=r.id)
        summary = evaluation.run_suite(suite, label, repeats=1, trial={"trainee_id": agent_id,
                                                                       "qualify": recruitment_id})
        return _finish(recruitment_id, summary)
    except Exception as e:  # noqa: BLE001 - a broken suite must not leave the record qualifying forever
        log_event("error", f"qualification of recruitment #{recruitment_id} failed: {type(e).__name__}: {e}")
        return _finish(recruitment_id, None, f"the qualification run failed: {type(e).__name__}: {e}")
    finally:
        with _qualifying_lock:
            _qualifying.discard(recruitment_id)


def _finish(recruitment_id: int, summary: dict[str, Any] | None, error: str = "") -> dict[str, Any]:
    from agent_company.governance import notify

    with session() as s:
        r = s.get(Recruitment, recruitment_id)
        if r is None or r.status != "qualifying":  # rejected or cancelled meanwhile
            return {}
        a = s.get(Agent, r.agent_id) if r.agent_id else None
        rate = float((summary or {}).get("pass_rate") or 0.0)
        cost = float((summary or {}).get("total_cost") or 0.0)
        r.evaluation_cost_usd = round((r.evaluation_cost_usd or 0.0) + cost, 6)
        if a is None or a.lifecycle != "candidate":
            error = error or "the candidate was retired during its qualification run"
        passed = not error and rate >= QUALIFY_PASS_RATE - 1e-9
        r.qualification = {**(r.qualification or {}), "status": "passed" if passed else "failed",
                           "pass_rate": round(rate, 3), "runs": (summary or {}).get("runs", 0),
                           "passed_runs": (summary or {}).get("passed", 0), "cost_usd": round(cost, 6),
                           "needed": round(QUALIFY_PASS_RATE, 3), "finished_at": now().isoformat(),
                           **({"error": error[:500]} if error else {})}
        suite = r.qualification.get("suite", QUALIFY_SUITE)
        name = a.name if a else f"agent {r.agent_id}"
        if passed:
            _ask_or_approve(s, r, notify)
            if r.status == "approved":
                _activate(s, r, "qualification")
            verdict = f"passed its qualification run: {rate:.0%} of the {suite} suite"
        else:
            r.status, r.decided_by, r.decided_at = "rejected", "qualification", now()
            r.note = (error or f"{rate:.0%} of the {suite} suite, needs {QUALIFY_PASS_RATE:.0%}")[:2000]
            _retire_candidate(s, r, f"not qualified: {r.note}")
            verdict = f"failed its qualification run: {r.note}"
        log_event("recruitment", f"recruitment #{r.id}: {name} {verdict}", goal_id=r.goal_id, agent_id=r.agent_id,
                  s=s, recruitment_id=r.id)
        if not passed or r.status == "approved":  # a pending record notifies through the approval request
            notify(f"Recruitment #{r.id}: {name} {'qualified' if passed else 'not qualified'}"[:256],
                   f"{name} {verdict} (${cost:.2f})." + ("" if passed else " The goal re-plans without it."),
                   level="info" if passed else "warning", source="recruitment", goal_id=r.goal_id, admin_only=True,
                   s=s)
        goal_id, waiting = r.goal_id, r.status == "pending"
    out = get(recruitment_id)
    out["resumed"] = False if waiting else _resume_goal(goal_id, "qualification")
    return out


def _activate(s: Session, r: Recruitment, actor: str) -> None:
    """The candidate becomes an active agent on probation."""
    from agent_company import registry

    a = s.get(Agent, r.agent_id) if r.agent_id else None
    if a is None or a.lifecycle != "candidate":
        return
    a.lifecycle, a.enabled = "active", True
    registry.record_version(s, a, actor, f"qualified for recruitment #{r.id}")
    on_recruited(s, r, a)


def _retire_candidate(s: Session, r: Recruitment, why: str) -> None:
    from agent_company import registry

    a = s.get(Agent, r.agent_id) if r.agent_id else None
    if a is None or a.lifecycle != "candidate":
        return
    a.lifecycle, a.enabled, a.probation_tasks_left = "retired", False, 0
    registry.record_version(s, a, "qualification", why[:500])


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
        if t is None or (t.status == "done" and not _review_failed(s, t)):
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


def _review_failed(s: Session, t: Task) -> bool:
    """A review of this iteration that depends on the task failed on the gate: its finished work was rejected."""
    from agent_company.quality import REVIEW_ROLES

    g = s.get(Goal, t.goal_id)
    hist = next((h for h in (g.history or []) if h.get("iteration") == t.iteration), None) if g else None
    failed = set((hist or {}).get("failed_checks") or [])
    return any(f"review {r.key}" in failed and t.key in (r.depends_on or [])
               for r in s.scalars(select(Task).where(Task.goal_id == t.goal_id, Task.iteration == t.iteration,
                                                     Task.role.in_(REVIEW_ROLES))))


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
            q = (r.qualification or {}).get("status", "")
            state = ("qualifying" if r.status == "qualifying" else
                     "not qualified" if q == "failed" else
                     "cancelled" if r.status in {"rejected", "cancelled"} else  # withdrawn while qualifying
                     "awaiting approval" if r.status == "pending" else
                     "retired" if failed >= PROBATION_MAX_FAILURES else
                     "withdrawn" if left > 0 and a.lifecycle == "retired" else  # an administrator retired it
                     "on probation" if left > 0 else "passed")
            out.append({"recruitment_id": r.id, "agent_id": a.id, "agent": a.name, "role": r.requested_role,
                        "passed_tasks": PROBATION_TASKS - left if state in {"on probation", "passed", "withdrawn"} else None,
                        "failed_tasks": failed, "state": state, "qualification": q,
                        "evaluation_cost_usd": r.evaluation_cost_usd})
        done = [x for x in out if x["state"] in {"passed", "retired"}]
        passed = [x for x in done if x["state"] == "passed"]
        by_status: dict[str, int] = {}
        for st in s.scalars(select(Recruitment.status)):
            by_status[st] = by_status.get(st, 0) + 1
        qualified = [x for x in out if x["qualification"] in {"passed", "failed"}]
    return {"recruits": out, "finished": len(done), "passed": len(passed),
            "pass_rate": round(len(passed) / len(done), 3) if done else None, "requests": by_status,
            "qualified": sum(x["qualification"] == "passed" for x in qualified), "qualification_runs": len(qualified),
            "evaluation_cost_usd": round(sum(x["evaluation_cost_usd"] or 0 for x in out), 6)}
