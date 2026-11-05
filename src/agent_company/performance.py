"""Defects, capability certification and performance intelligence.

Defects (ASEAO09 §11): every failed quality-gate check is a defect of the goal,
with a severity. It closes when a later iteration passes that check, reopens if it
fails again, and administrators can accept the risk or defer it.

Certification (SPEC13 §9): a proficiency level L0-L5 per agent and skill family
(the agent's role), computed from evidence: gate outcomes and eval-harness runs.
Levels need repeated, varied evidence; one hard task never makes an expert. A
level drops by one when the newest evidence is older than CERT_TTL_DAYS.

Performance (ASEAO07 §15): agent completion, review-failure, rework and reliability
rates, duration, cost and collaboration effectiveness; the same per room; and
company-level goal outcomes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from agent_company.db import Agent, Defect, EvalRun, Event, Goal, Lesson, Relationship, Room, Run, Task, now, session

# --- defects ----------------------------------------------------------------------

def severity_of(check: str) -> str:
    if check in {"secret scan"} or "refused by the command policy" in check:
        return "critical"
    if check.startswith("command") or check == "syntax":
        return "high"
    if check == "scope" or check.startswith("review"):
        return "medium"
    if check.startswith("task"):
        return "low"
    return "info"


def track_defects(goal_id: int, iteration: int, checks: list[dict[str, Any]]) -> dict[str, int]:
    """Open, reopen or close the goal's defects from one gate report."""
    opened = closed = 0
    with session() as s:
        existing = {d.check: d for d in s.scalars(select(Defect).where(Defect.goal_id == goal_id))}
        for c in checks:
            name = c["check"][:256]
            d = existing.get(name)
            if not c["passed"]:
                if d is None:
                    s.add(Defect(goal_id=goal_id, check=name, severity=severity_of(name),
                                 detail=c.get("detail", "")[-2000:], first_iteration=iteration,
                                 last_iteration=iteration))
                    opened += 1
                else:
                    if d.status == "closed":
                        d.status, d.closed_iteration = "reopened", None
                        opened += 1
                    d.last_iteration, d.detail = iteration, c.get("detail", "")[-2000:]
            elif d is not None and d.status in {"detected", "reopened"}:
                d.status, d.closed_iteration = "closed", iteration
                closed += 1
    return {"opened": opened, "closed": closed}


def defect_dict(d: Defect) -> dict[str, Any]:
    return {"id": d.id, "goal_id": d.goal_id, "check": d.check, "severity": d.severity, "status": d.status,
            "detail": d.detail, "first_iteration": d.first_iteration, "last_iteration": d.last_iteration,
            "closed_iteration": d.closed_iteration, "updated_at": d.updated_at.isoformat()}


def list_defects(status: str = "", goal_id: int | None = None) -> list[dict[str, Any]]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    with session() as s:
        q = select(Defect)
        if status == "open":
            q = q.where(Defect.status.in_(["detected", "reopened"]))
        elif status:
            q = q.where(Defect.status == status)
        if goal_id is not None:
            q = q.where(Defect.goal_id == goal_id)
        rows = [defect_dict(d) for d in s.scalars(q.order_by(Defect.id.desc()).limit(500))]
    return sorted(rows, key=lambda d: (d["status"] == "closed", order.get(d["severity"], 9), -d["id"]))


def set_defect_status(defect_id: int, status: str) -> dict[str, Any]:
    if status not in {"accepted_risk", "deferred", "detected"}:
        raise ValueError("status is accepted_risk, deferred or detected")
    with session() as s:
        d = s.get(Defect, defect_id)
        if d is None:
            raise KeyError(defect_id)
        d.status = status
        return defect_dict(d)


# --- certification ----------------------------------------------------------------

CERT_TTL_DAYS = 90
LEVELS = {0: "Not evaluated", 1: "Basic understanding", 2: "Can complete simple tasks with support",
          3: "Can independently complete ordinary tasks", 4: "Advanced specialist capability",
          5: "Expert-level capability demonstrated across demanding evaluations"}


def _level(passes: int, total: int, goals: int, eval_passes: int) -> int:
    rate = passes / total if total else 0.0
    if total == 0:
        return 0
    if passes >= 25 and total >= 20 and rate >= 0.85 and goals >= 5 and eval_passes >= 5:
        return 5
    if passes >= 10 and total >= 10 and rate >= 0.75 and goals >= 2:
        return 4
    if passes >= 3 and total >= 5 and rate >= 0.6:
        return 3
    if passes >= 1:
        return 2
    return 1


def certification(agent_id: int) -> dict[str, Any]:
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(agent_id)
        lessons = s.scalars(select(Lesson).where(Lesson.agent_id == agent_id,
                                                 Lesson.outcome.in_(["success", "failure"]))).all()
        eval_goals = set(s.scalars(select(Goal.id).where(Goal.origin == "eval")))
        passes = sum(lesson.outcome == "success" for lesson in lessons)
        goals = len({lesson.goal_id for lesson in lessons if lesson.goal_id})
        eval_passes = sum(lesson.outcome == "success" and lesson.goal_id in eval_goals for lesson in lessons)
        level = _level(passes, len(lessons), goals, eval_passes)
        newest = max((lesson.created_at for lesson in lessons), default=None)
        expired = bool(newest and newest.replace(tzinfo=None) < (now() - timedelta(days=CERT_TTL_DAYS)).replace(tzinfo=None))
        if expired and level > 0:
            level -= 1
        return {"agent_id": a.id, "agent": a.name, "skill_family": a.role, "level": level,
                "meaning": LEVELS[level], "evidence": {"outcomes": len(lessons), "passed": passes,
                                                        "goals": goals, "eval_passed": eval_passes},
                "expired": expired, "newest_evidence": newest.isoformat() if newest else None}


def certifications() -> list[dict[str, Any]]:
    with session() as s:
        ids = [a.id for a in s.scalars(select(Agent).where(Agent.lifecycle != "retired").order_by(Agent.id))]
    return [certification(i) for i in ids]


# --- performance --------------------------------------------------------------------

def _agent_metrics(s, a: Agent, tasks: list[Task], runs: list[Run], goal_passed: dict[int, bool]  # noqa: ANN001
                   ) -> dict[str, Any]:
    finished = [t for t in tasks if t.status in {"done", "failed"}]
    done = [t for t in finished if t.status == "done"]
    keys = defaultdict(set)
    for t in tasks:
        keys[(t.goal_id, t.key)].add(t.iteration)
    rework = sum(1 for its in keys.values() if len(its) > 1)
    review_fail = sum(1 for t in tasks if t.role in {"qa", "security"} and "VERDICT: FAIL" in (t.output or "").upper())
    secs = [(t.finished_at - t.started_at).total_seconds() for t in finished if t.started_at and t.finished_at]
    collab = s.scalars(select(Relationship).where(Relationship.kind == "collaborated",
                                                  (Relationship.a_id == a.id) | (Relationship.b_id == a.id))).all()
    goals = {t.goal_id for t in tasks}
    return {
        "agent_id": a.id, "agent": a.name, "role": a.role, "room": a.room_key,
        "tasks": len(tasks), "completion_rate": round(len(done) / len(finished), 3) if finished else None,
        "review_failures": review_fail,
        "rework_rate": round(rework / len(keys), 3) if keys else None,
        "reliability": round(sum(r.ok for r in runs) / len(runs), 3) if runs else None,
        "avg_task_seconds": round(sum(secs) / len(secs), 1) if secs else None,
        "tokens": sum(r.input_tokens + r.output_tokens for r in runs), "cost": round(sum(r.cost for r in runs), 6),
        "collaborators": len(collab),
        "goal_pass_rate": round(sum(goal_passed.get(g, False) for g in goals) / len(goals), 3) if goals else None,
    }


# --- measured gate for enabling more roles (guides G-R-04) ------------------------------------------

GATE_SUITE, GATE_PASS_RATE, GATE_REPEATS = "smoke", 0.8, 3


def enablement_gate() -> dict[str, Any]:
    """'Pipeline reliable' (CLAUDE.md) as a measurement: the latest smoke-suite label passed at least 80 % over
    at least 3 repeats per task with real models (not the simulator), and no critical defect is open. Retry
    rate and cost per accepted goal are reported alongside."""
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    with session() as s:
        last = s.scalars(select(EvalRun).where(EvalRun.suite == GATE_SUITE).order_by(EvalRun.id.desc())).first()
        if last is None:
            reasons.append(f"no {GATE_SUITE} suite run yet: python -m agent_company eval --suite {GATE_SUITE} "
                           f"--label <name> --repeats {GATE_REPEATS}")
        else:
            runs = s.scalars(select(EvalRun).where(EvalRun.label == last.label, EvalRun.suite == GATE_SUITE)).all()
            per_task: dict[str, int] = defaultdict(int)
            for r in runs:
                per_task[r.task] += 1
            rate = sum(r.passed for r in runs) / len(runs)
            kinds = {k for (k,) in s.execute(select(Run.kind).where(Run.goal_id.in_([r.goal_id for r in runs if r.goal_id])))}
            metrics.update(label=last.label, runs=len(runs), pass_rate=round(rate, 3),
                           min_repeats=min(per_task.values()), executors=sorted(kinds))
            if min(per_task.values()) < GATE_REPEATS:
                reasons.append(f"label '{last.label}' has fewer than {GATE_REPEATS} repeats of some task")
            if rate < GATE_PASS_RATE:
                reasons.append(f"label '{last.label}' passed {rate:.0%}, below {GATE_PASS_RATE:.0%}")
            if not kinds or kinds <= {"offline"}:
                reasons.append(f"label '{last.label}' ran on the offline simulator, not the configured models")
        critical = s.scalar(select(func.count()).select_from(Defect).where(
            Defect.severity == "critical", Defect.status.in_(["detected", "reopened"]))) or 0
        if critical:
            reasons.append(f"{critical} critical defect(s) open")
        total_runs = s.scalar(select(func.count()).select_from(Run)) or 0
        retries = s.scalar(select(func.count()).select_from(Event).where(Event.kind == "retry")) or 0
        accepted = [g.id for g in s.scalars(select(Goal).where(Goal.status == "approved", Goal.origin != "eval"))]
        cost = s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(Run.goal_id.in_(accepted))) if accepted else 0.0
        metrics.update(critical_defects=critical, retry_rate=round(retries / total_runs, 3) if total_runs else 0.0,
                       accepted_goals=len(accepted), cost_per_accepted_goal=round(cost / len(accepted), 4) if accepted else None)
    return {"met": not reasons, "reasons": reasons, "metrics": metrics,
            "rule": f"{GATE_SUITE} suite ≥ {GATE_PASS_RATE:.0%} over ≥ {GATE_REPEATS} repeats with real models, "
                    "no open critical defect"}


def gated(role: str) -> bool:
    """A registered role that is off until the pipeline is reliable (CLAUDE.md)."""
    from agent_company import org

    spec = org.ROLES.get(role)
    return spec is not None and not spec.enabled


def report() -> dict[str, Any]:
    with session() as s:
        all_goals = s.scalars(select(Goal)).all()
        goal_passed = {g.id: g.status in {"approved", "awaiting_approval"} for g in all_goals}
        goals = [g for g in all_goals if g.origin != "eval"]  # company figures: real work only
        tasks_by = defaultdict(list)
        for t in s.scalars(select(Task).where(Task.agent_id.is_not(None))):
            tasks_by[t.agent_id].append(t)
        runs_by = defaultdict(list)
        for r in s.scalars(select(Run).where(Run.agent_id.is_not(None))):
            runs_by[r.agent_id].append(r)
        agents = [_agent_metrics(s, a, tasks_by[a.id], runs_by[a.id], goal_passed)
                  for a in s.scalars(select(Agent).order_by(Agent.id)) if tasks_by[a.id] or runs_by[a.id]]
        from agent_company.selfevolve import agent_score

        for m in agents:  # the guide's §7 weighted Agent Score, weights by role
            m["score"] = agent_score(m)
        rooms = []
        for room in s.scalars(select(Room).order_by(Room.id)):
            members = [m for m in agents if m["room"] == room.key]
            if not members:
                continue
            rates = [m["completion_rate"] for m in members if m["completion_rate"] is not None]
            rooms.append({"room": room.key, "name": room.name, "agents": len(members),
                          "tasks": sum(m["tasks"] for m in members),
                          "completion_rate": round(sum(rates) / len(rates), 3) if rates else None,
                          "cost": round(sum(m["cost"] for m in members), 6)})
        finished = [g for g in goals if g.status in {"approved", "awaiting_approval", "failed", "rejected"}]
        passed = [g for g in finished if goal_passed[g.id]]
        spend = dict(s.execute(select(Run.goal_id, func.sum(Run.cost)).where(Run.goal_id.is_not(None))
                               .group_by(Run.goal_id)).all())
        open_defects = s.scalar(select(func.count()).select_from(Defect)
                                .where(Defect.status.in_(["detected", "reopened"]))) or 0
        company = {
            "goals": len(goals), "finished": len(finished), "passed": len(passed),
            "pass_rate": round(len(passed) / len(finished), 3) if finished else None,
            "avg_iterations_to_pass": round(sum(g.iteration for g in passed) / len(passed), 2) if passed else None,
            "cost_per_passed_goal": round(sum(spend.get(g.id, 0) or 0 for g in passed) / len(passed), 6) if passed else None,
            "open_defects": open_defects,
        }
    return {"company": company, "rooms": rooms, "agents": agents}
