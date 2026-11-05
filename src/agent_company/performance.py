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

Calibration (ASEAO07 §11, S07-R-04): the planner's estimates are predictions. Each finished task's
`estimated_minutes` is compared with the time its runs took, per role; each finished goal's estimated cost with
its model cost. Reported with sample sizes; nothing is concluded from fewer than CALIBRATION_MIN samples.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from agent_company.db import Agent, AgentVersion, Defect, EvalRun, Event, Goal, Lesson, Relationship, Room, Run, Task, now, session

# --- defects ----------------------------------------------------------------------

OPEN = ("detected", "reopened", "assigned", "fix_submitted")
SET_BY_ADMIN = ("accepted_risk", "deferred", "duplicate", "not_reproducible")
PRIORITY = {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 5}  # the default priority of a severity


def severity_of(check: str) -> str:
    if check in {"secret scan"} or "refused by the command policy" in check:
        return "critical"
    if check.startswith(("command", "stage ", "dependency audit")) or check == "syntax":
        return "high"
    if check == "scope" or check.startswith("review"):
        return "medium"
    if check.startswith("task"):
        return "low"
    return "info"


def _move(d: Defect, to: str, by: str = "system", note: str = "") -> None:
    """One lifecycle step, kept in the defect's history (S09-R-03)."""
    if d.status == to:
        return
    d.history = [*(d.history or []), {"at": now().isoformat(), "from": d.status, "to": to, "by": by,
                                      "note": note[:500]}][-50:]
    d.status = to


def _evidence(c: dict[str, Any], iteration: int, how: str) -> dict[str, Any]:
    """§12: what proves the check's state is the platform's own run of it, never an agent's report."""
    return {"how": how, "iteration": iteration, "passed": bool(c["passed"]), "at": now().isoformat(),
            **{k: c[k] for k in ("command", "commit", "exit_code", "duration_s", "isolation") if k in c}}


def track_defects(goal_id: int, iteration: int, checks: list[dict[str, Any]]) -> dict[str, int]:
    """Open, reopen or retest the goal's defects from one gate report. The gate ran every check again in a fresh
    process, so a passing check is a verified fix (`retested`); the merge closes it (`on_merged`). A check failing
    again reopens a fixed, submitted or unreproducible defect; one an administrator deferred or accepted stays so."""
    opened = closed = 0
    with session() as s:
        existing = {d.check: d for d in s.scalars(select(Defect).where(Defect.goal_id == goal_id))}
        for c in checks:
            name = c["check"][:256]
            d = existing.get(name)
            if not c["passed"]:
                if d is None:
                    sev = severity_of(name)
                    s.add(Defect(goal_id=goal_id, check=name, severity=sev,
                                 detail=c.get("detail", "")[-2000:], first_iteration=iteration,
                                 last_iteration=iteration, verification=_evidence(c, iteration, "gate"),
                                 history=[{"at": now().isoformat(), "from": "", "to": "detected", "by": "gate",
                                           "note": f"iteration {iteration}"}]))
                    opened += 1
                    continue
                if d.status in ("closed", "retested", "fix_submitted", "not_reproducible"):
                    _move(d, "reopened", "gate", f"failed again in iteration {iteration}")
                    d.closed_iteration = None
                    opened += 1
                d.last_iteration, d.detail = iteration, c.get("detail", "")[-2000:]
                d.verification = _evidence(c, iteration, "gate")
            elif d is not None and d.status in OPEN:
                _move(d, "retested", "gate", f"passed in iteration {iteration}")
                d.closed_iteration, d.verification = iteration, _evidence(c, iteration, "gate")
                closed += 1
    return {"opened": opened, "closed": closed}


def assign(goal_id: int, iteration: int, agent_id: int) -> int:
    """A writing task of a later iteration started: the goal's open defects from earlier iterations are its work."""
    with session() as s:
        n = 0
        for d in s.scalars(select(Defect).where(Defect.goal_id == goal_id, Defect.first_iteration < iteration,
                                                Defect.status.in_(("detected", "reopened")))):
            d.assignee_agent_id = agent_id
            _move(d, "assigned", "loop", f"iteration {iteration}")
            n += 1
        return n


def fix_submitted(goal_id: int, iteration: int) -> int:
    """The iteration's writing tasks finished: their change is submitted as the fix. Only the gate's own run of the
    check moves it on (retested or reopened); a developer's report never does."""
    with session() as s:
        wrote = s.scalar(select(func.count()).select_from(Task).where(
            Task.goal_id == goal_id, Task.iteration == iteration, Task.writes.is_(True), Task.status == "done"))
        if not wrote:
            return 0
        rows = s.scalars(select(Defect).where(Defect.goal_id == goal_id, Defect.status == "assigned")).all()
        for d in rows:
            _move(d, "fix_submitted", "loop", f"iteration {iteration}")
        return len(rows)


def on_merged(goal_id: int, sha: str) -> int:
    """The goal reached its base branch: its verified fixes are closed."""
    with session() as s:
        rows = s.scalars(select(Defect).where(Defect.goal_id == goal_id, Defect.status == "retested")).all()
        for d in rows:
            _move(d, "closed", "merge", f"merged {sha[:10]}")
        return len(rows)


def defect_dict(d: Defect, names: dict[int, str] | None = None) -> dict[str, Any]:
    return {"id": d.id, "goal_id": d.goal_id, "check": d.check, "severity": d.severity, "status": d.status,
            "open": d.status in OPEN, "priority": d.priority or PRIORITY.get(d.severity, 3), "assignee_agent_id": d.assignee_agent_id,
            "assignee": (names or {}).get(d.assignee_agent_id or 0), "duplicate_of": d.duplicate_of,
            "verification": d.verification or {}, "resolution": d.resolution or "", "history": d.history or [],
            "retestable": d.check.startswith("command "),
            "detail": d.detail, "first_iteration": d.first_iteration, "last_iteration": d.last_iteration,
            "closed_iteration": d.closed_iteration, "updated_at": d.updated_at.isoformat()}


def list_defects(status: str = "", goal_id: int | None = None) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Defect)
        if status == "open":
            q = q.where(Defect.status.in_(OPEN))
        elif status:
            q = q.where(Defect.status == status)
        if goal_id is not None:
            q = q.where(Defect.goal_id == goal_id)
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        rows = [defect_dict(d, names) for d in s.scalars(q.order_by(Defect.id.desc()).limit(500))]
    return sorted(rows, key=lambda d: (not d["open"], d["priority"], PRIORITY.get(d["severity"], 9), -d["id"]))


def set_defect_status(defect_id: int, status: str, note: str = "", duplicate_of: int | None = None,
                      actor: str = "admin") -> dict[str, Any]:
    """An administrator's decision (S09-R-03): accepted_risk, deferred, duplicate (of another defect of the goal),
    not_reproducible (only after a passing retest of the current code), or detected (open it again). Closing is not
    a decision: only a merged, retested fix closes a defect."""
    if status not in (*SET_BY_ADMIN, "detected"):
        raise ValueError("status is " + ", ".join(SET_BY_ADMIN) + " or detected")
    with session() as s:
        d = s.get(Defect, defect_id)
        if d is None:
            raise KeyError(defect_id)
        if status == "duplicate":
            other = s.get(Defect, duplicate_of) if duplicate_of else None
            if other is None or other.id == d.id or other.goal_id != d.goal_id:
                raise ValueError("a duplicate names another defect of the same goal")
            if other.duplicate_of == d.id:
                raise ValueError(f"defect #{other.id} is already a duplicate of this one")
            d.duplicate_of = other.id
        else:
            d.duplicate_of = None
        if status == "not_reproducible":
            v = d.verification or {}
            if not (v.get("how") == "retest" and v.get("passed")):
                raise ValueError("retest the defect first: not reproducible needs a passing fresh run of its check")
        d.resolution = note[:2000] if status in SET_BY_ADMIN else ""
        if status in SET_BY_ADMIN:
            d.assignee_agent_id = None
        if status == "detected":
            d.closed_iteration = None
        _move(d, status, actor, note)
        return defect_dict(d)


def set_defect_priority(defect_id: int, priority: int, actor: str = "admin") -> dict[str, Any]:
    if priority not in range(1, 6):
        raise ValueError("priority is 1 (first) to 5")
    with session() as s:
        d = s.get(Defect, defect_id)
        if d is None:
            raise KeyError(defect_id)
        if d.priority != priority:
            d.history = [*(d.history or []), {"at": now().isoformat(), "from": d.status, "to": d.status, "by": actor,
                                              "note": f"priority {d.priority or '-'} -> {priority}"}][-50:]
            d.priority = priority
        return defect_dict(d)


def retest(defect_id: int, actor: str = "admin") -> dict[str, Any]:
    """§12: run a command check again in a fresh process (the L3 sandbox, like the gate) on the goal's current code.
    A pass verifies an open defect's fix (`retested`); a failure reopens a fixed or unreproducible one."""
    from agent_company import contracts, sandbox, worktrees
    from agent_company.config import settings

    with session() as s:
        d = s.get(Defect, defect_id)
        if d is None:
            raise KeyError(defect_id)
        if not d.check.startswith("command "):
            raise ValueError("only command checks can be retested alone; the next gate run retests the others")
        g = s.get(Goal, d.goal_id)
        if g is None or not g.worktree or not Path(g.worktree).is_dir():
            raise ValueError("the goal's worktree is gone; nothing to retest")
        if g.status in ("planning", "running", "recovering"):
            raise ValueError(f"goal is {g.status}: its own gate retests the defect")
        # the gate's own record of the command (the check name is cut at 256 characters)
        run = (d.verification or {}).get("command") or d.check[len("command "):]
        worktree, mode = Path(g.worktree), sandbox.mode_for(g, s=s)
    info: dict[str, Any] = {}
    ok, detail = contracts.run_command(run, worktree, settings().command_timeout, mode=mode, info=info)
    row = {"passed": ok, "command": run, "commit": worktrees.git(worktree, "rev-parse", "HEAD"),
           "exit_code": info.get("exit_code"), "duration_s": info.get("duration_s", 0.0),
           "isolation": info.get("isolation", "")}
    with session() as s:
        d = s.get(Defect, defect_id)
        assert d is not None
        d.verification = _evidence(row, d.last_iteration, "retest") | {"by": actor,
                                                                        "output_tail": info.get("output_tail", "")}
        if ok and d.status in OPEN:
            _move(d, "retested", actor, "passed a retest")
        elif not ok:
            d.detail = detail[-2000:]
            if d.status in ("retested", "closed", "not_reproducible"):
                _move(d, "reopened", actor, "failed a retest")
                d.closed_iteration = None
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
        from agent_company.training import certified

        return {"agent_id": a.id, "agent": a.name, "skill_family": a.role, "level": level,
                "skills": certified(a.id, s),  # per capability, from final evaluations only (S13-R-03)
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


def _retries_and_cost(s) -> dict[str, Any]:  # noqa: ANN001
    """Retries per model run, and the model cost per accepted (merged, `approved`) goal of real work. Shared by the
    enablement gate (G-R-04) and the operational metrics (G-R-02)."""
    total_runs = s.scalar(select(func.count()).select_from(Run)) or 0
    retries = s.scalar(select(func.count()).select_from(Event).where(Event.kind == "retry")) or 0
    accepted = [g.id for g in s.scalars(select(Goal).where(Goal.status == "approved", Goal.origin != "eval"))]
    cost = s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(Run.goal_id.in_(accepted))) if accepted else 0.0
    return {"runs": total_runs, "retries": retries, "retry_rate": round(retries / total_runs, 3) if total_runs else None,
            "accepted_goals": len(accepted),
            "cost_per_accepted_goal": round(cost / len(accepted), 4) if accepted else None}


# --- operational metrics (guides G-R-02) -----------------------------------------------------------

SECURITY_CHECKS = ("secret scan", "stage security", "dependency audit")
ENDED = ("approved", "rejected", "cancelled", "failed")


def _rate(hits: int, n: int) -> dict[str, Any]:
    return {"rate": round(hits / n, 3) if n else None, "hits": hits, "n": n}


def _median(values: list[float]) -> dict[str, Any]:
    return {"median_seconds": round(statistics.median(values), 1) if values else None, "n": len(values)}


def _aware(dt):  # noqa: ANN001, ANN202 - SQLite hands datetimes back without their zone
    return dt if dt.tzinfo else dt.replace(tzinfo=now().tzinfo)


def operations(s) -> dict[str, Any]:  # noqa: ANN001
    """Outcome metrics of the delivery pipeline (guides G-R-02: optimise for verified outcomes, not activity), over
    real work (benchmark goals left out). Every rate carries its sample size `n` and is None without samples."""
    from agent_company.db import GoalTransition, ImprovementContract, Incident
    from agent_company.quality import REVIEW_ROLES

    goals = {g.id: g for g in s.scalars(select(Goal).where(Goal.origin != "eval"))}
    gates = [h for g in goals.values() for h in (g.history or []) if "passed" in h]
    judged = [g for g in goals.values() if any("passed" in h for h in g.history or [])]
    first = sum(1 for g in judged if next(h for h in g.history if "passed" in h)["passed"])
    reviews = [t for t in s.scalars(select(Task).where(Task.role.in_(REVIEW_ROLES), Task.status.in_(("done", "failed"))))
               if t.goal_id in goals]
    rejected = sum(1 for t in reviews if "VERDICT: FAIL" in (t.output or "").upper())
    # cycle time, blocked time and the time a person took to decide, from each goal's workflow history (S04-R-04)
    steps: dict[int, list[GoalTransition]] = defaultdict(list)
    for t in s.scalars(select(GoalTransition).order_by(GoalTransition.id)):
        if t.goal_id in goals:
            steps[t.goal_id].append(t)
    cycles: list[float] = []
    waits: list[float] = []
    blocked = 0.0
    for gid, g in goals.items():
        start, status, paused = _aware(g.created_at), "queued", 0.0
        ended: float | None = None
        for t in steps[gid]:
            at = _aware(t.created_at)
            if status == "paused":
                paused += max(0.0, (at - start).total_seconds())
            if (status == "awaiting_approval" and t.to_status in ("approved", "rejected")
                    and (t.actor or "").startswith("user:")):
                waits.append(max(0.0, (at - start).total_seconds()))
            if t.to_status in ENDED:
                ended = (at - _aware(g.created_at)).total_seconds()
            start, status = at, t.to_status
        if g.status in ENDED and ended is not None:
            cycles.append(max(0.0, ended))
            blocked += paused
    merged = escaped = 0
    findings = {"secret_scan": 0, "security_stage": 0, "dependency_audit": 0}
    for d in s.scalars(select(Defect).where(Defect.goal_id.in_(list(goals)))):
        moves = d.history or []
        at_merge = next((i for i, m in enumerate(moves) if m.get("to") == "closed" and m.get("by") == "merge"), None)
        if at_merge is not None:
            merged += 1
            escaped += any(m.get("to") == "reopened" for m in moves[at_merge + 1:])
        if d.check.startswith(SECURITY_CHECKS):
            findings["secret_scan" if d.check == "secret scan" else "security_stage" if d.check == "stage security"
                     else "dependency_audit"] += 1
    findings["refused_commands"] = s.scalar(select(func.count()).select_from(Event).where(
        Event.kind == "command_refused", Event.goal_id.in_(list(goals)))) or 0
    findings["incidents"] = s.scalar(select(func.count()).select_from(Incident)) or 0
    deployed = s.scalar(select(func.count()).select_from(ImprovementContract).where(
        ImprovementContract.status.in_(("monitoring", "stable", "rolled_back")))) or 0
    rolled = s.scalar(select(func.count()).select_from(ImprovementContract).where(
        ImprovementContract.status == "rolled_back")) or 0
    resets = s.scalar(select(func.count()).select_from(Event).where(
        Event.kind == "gate", Event.message.like("Kept iteration %"), Event.goal_id.in_(list(goals)))) or 0
    rc = _retries_and_cost(s)
    total_cycle = sum(cycles)
    return {
        "gate_pass_rate": _rate(sum(bool(h["passed"]) for h in gates), len(gates)),
        "first_iteration_pass_rate": _rate(first, len(judged)),
        "review_rejection_rate": _rate(rejected, len(reviews)),
        "escaped_defects": _rate(escaped, merged),
        "cycle_time": _median(cycles),
        "blocked_time": {"seconds": round(blocked, 1), "share": round(blocked / total_cycle, 3) if total_cycle else None,
                         "n": len(cycles)},
        "approval_latency": _median(waits),
        "retry_rate": {"rate": rc["retry_rate"], "hits": rc["retries"], "n": rc["runs"]},
        "cost_per_accepted_goal": {"usd": rc["cost_per_accepted_goal"], "n": rc["accepted_goals"]},
        "rollback_rate": _rate(rolled, deployed),
        "best_iteration_resets": resets,
        "security_findings": findings | {"total": sum(findings.values())},
    }


# --- measured gate for enabling more roles (guides G-R-04) ------------------------------------------

GATE_SUITE, GATE_PASS_RATE, GATE_REPEATS = "smoke", 0.8, 3


def _trial_rooms(s: Any, runs: list[EvalRun]) -> dict[int, str | None]:
    """goal id -> the room its eval goal measured (G-R-04 room trial), None for an ordinary eval goal."""
    ids = [r.goal_id for r in runs if r.goal_id]
    return {gid: ((trial or {}).get("room") or None)
            for gid, trial in s.execute(select(Goal.id, Goal.trial).where(Goal.id.in_(ids)))} if ids else {}


def _latest_label(s: Any, suite: str, room: str | None) -> list[EvalRun]:
    """The runs of the newest label of `suite` whose goals measured `room` (None: no room trial)."""
    runs = list(s.scalars(select(EvalRun).where(EvalRun.suite == suite).order_by(EvalRun.id.desc())))
    rooms = _trial_rooms(s, runs)
    label = next((r.label for r in runs if rooms.get(r.goal_id) == room), None)
    return [r for r in runs if r.label == label and rooms.get(r.goal_id) == room] if label else []


def _measure(s: Any, runs: list[EvalRun], what: str) -> tuple[dict[str, Any], list[str]]:
    """Pass rate, repeats per task and executors of one label's runs, with what keeps them from counting."""
    reasons: list[str] = []
    per_task: dict[str, int] = defaultdict(int)
    for r in runs:
        per_task[r.task] += 1
    rate = sum(r.passed for r in runs) / len(runs)
    kinds = {k for (k,) in s.execute(select(Run.kind).where(Run.goal_id.in_([r.goal_id for r in runs if r.goal_id])))}
    label = runs[0].label
    metrics = {"label": label, "runs": len(runs), "pass_rate": round(rate, 3), "min_repeats": min(per_task.values()),
               "executors": sorted(kinds), "last_at": max(r.created_at for r in runs).isoformat()}
    if min(per_task.values()) < GATE_REPEATS:
        reasons.append(f"{what} label '{label}' has fewer than {GATE_REPEATS} repeats of some task")
    if rate < GATE_PASS_RATE:
        reasons.append(f"{what} label '{label}' passed {rate:.0%}, below {GATE_PASS_RATE:.0%}")
    if not kinds or kinds <= {"offline"}:
        reasons.append(f"{what} label '{label}' ran on the offline simulator, not the configured models")
    return metrics, reasons


def enablement_gate() -> dict[str, Any]:
    """'Pipeline reliable' (CLAUDE.md) as a measurement: the latest smoke-suite label (not a room trial) passed at
    least 80 % over at least 3 repeats per task with real models (not the simulator), and no critical defect is
    open. Retry rate and cost per accepted goal are reported alongside."""
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    with session() as s:
        runs = _latest_label(s, GATE_SUITE, None)
        if not runs:
            reasons.append(f"no {GATE_SUITE} suite run yet: python -m agent_company eval --suite {GATE_SUITE} "
                           f"--label <name> --repeats {GATE_REPEATS}")
        else:
            m, r = _measure(s, runs, GATE_SUITE)
            metrics.update(m)
            reasons += r
        critical = s.scalar(select(func.count()).select_from(Defect).where(
            Defect.severity == "critical", Defect.status.in_(OPEN))) or 0
        if critical:
            reasons.append(f"{critical} critical defect(s) open")
        rc = _retries_and_cost(s)
        metrics.update(critical_defects=critical, retry_rate=rc["retry_rate"] or 0.0,
                       accepted_goals=rc["accepted_goals"], cost_per_accepted_goal=rc["cost_per_accepted_goal"])
    return {"met": not reasons, "reasons": reasons, "metrics": metrics,
            "rule": f"{GATE_SUITE} suite ≥ {GATE_PASS_RATE:.0%} over ≥ {GATE_REPEATS} repeats with real models, "
                    "no open critical defect"}


def room_suite(room: str) -> str:
    return f"room-{room}"


def gated_rooms() -> list[str]:
    """The rooms of the gated roles (one eval suite each, `evals/room-<key>.json`)."""
    from agent_company import org

    return sorted({spec.room for spec in org.ROLES.values() if not spec.enabled})


def room_gate(room: str) -> dict[str, Any]:
    """G-R-04 for one gated room, enabled one at a time: the pipeline gate, then the room's own suite
    (`room-<key>`, run as a room trial: the room's disabled agents are staffed for those eval goals only) at the
    same pass rate and repeats, with the room's agents doing work in every run; a smoke label run with the room in
    the pipeline that does not pass less often than the pipeline's own (`eval --compare` before/after); and no
    other gated room's agent enabled or changed since the pipeline's label, so each room is measured alone."""
    from agent_company import evaluation, org

    base = enablement_gate()
    reasons = list(base["reasons"])
    out: dict[str, Any] = {"room": room, "pipeline": base["metrics"]}
    suite = room_suite(room)
    with session() as s:
        try:
            evaluation.load_suite(suite)
        except (OSError, ValueError):
            reasons.append(f"no eval suite for room {room}: write evals/{suite}.json (kind room)")
        else:
            runs = _latest_label(s, suite, room)
            if not runs:
                reasons.append(f"no {suite} run yet: python -m agent_company eval --suite {suite} --label <name> "
                               f"--repeats {GATE_REPEATS}")
            else:
                m, r = _measure(s, runs, suite)
                reasons += r
                ids = [x.goal_id for x in runs if x.goal_id]
                gated_roles = {r for r, spec in org.ROLES.items() if not spec.enabled and spec.room == room}
                worked = set(s.scalars(select(Task.goal_id).join(Agent, Agent.id == Task.agent_id).where(
                    Task.goal_id.in_(ids), Agent.room_key == room, Agent.role.in_(gated_roles)))) if ids else set()
                idle = sum(1 for x in runs if x.goal_id not in worked)
                m["runs_without_room"] = idle
                if idle:
                    reasons.append(f"{suite} label '{m['label']}': the room's gated roles "
                                   f"({', '.join(sorted(gated_roles))}) did no task in {idle} run(s)")
                out["suite"] = m
        after = _latest_label(s, GATE_SUITE, room)
        if not after:
            reasons.append(f"no {GATE_SUITE} run with room {room} in the pipeline yet: python -m agent_company eval "
                           f"--suite {GATE_SUITE} --room {room} --label <name> --repeats {GATE_REPEATS}")
        else:
            m, r = _measure(s, after, f"{GATE_SUITE} with {room}")
            reasons += r
            before = base["metrics"].get("pass_rate")
            m["change"] = None if before is None else round(m["pass_rate"] - before, 3)
            if before is not None and m["pass_rate"] < before:
                reasons.append(f"{GATE_SUITE} with room {room} passed {m['pass_rate']:.0%}, less than the pipeline's "
                               f"{before:.0%} (label '{base['metrics']['label']}')")
            out["with_room"] = m
        if base["metrics"].get("last_at"):
            since = max(r.created_at for r in _latest_label(s, GATE_SUITE, None))
            others = {spec.role for spec in org.ROLES.values() if not spec.enabled}
            changed = sorted({a.room_key for v, a in s.execute(
                select(AgentVersion, Agent).join(Agent, Agent.id == AgentVersion.agent_id).where(
                    Agent.role.in_(others), Agent.room_key != room, Agent.parent_id.is_(None),
                    AgentVersion.created_at > since)) if (v.snapshot or {}).get("enabled")})
            if changed:
                reasons.append(f"room(s) {', '.join(changed)} enabled or changed after the pipeline's label "
                               f"'{base['metrics']['label']}': measure the pipeline with them first (one room at a time)")
    return out | {"met": not reasons, "reasons": reasons,
                  "rule": base["rule"] + f"; then {suite} ≥ {GATE_PASS_RATE:.0%} over ≥ {GATE_REPEATS} repeats with "
                          f"the room's agents working, {GATE_SUITE} with the room not below the pipeline, one room "
                          "at a time"}


def gated(role: str) -> bool:
    """A registered role that is off until the pipeline is reliable (CLAUDE.md)."""
    from agent_company import org

    spec = org.ROLES.get(role)
    return spec is not None and not spec.enabled


CALIBRATION_MIN = 5  # fewer samples: shown, but not labelled as over- or underestimating


def _accuracy(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    """(estimate, actual) pairs: the median actual/estimate ratio (above 1 = it took longer than predicted), the mean
    absolute percentage error, the share within ±50% of the estimate, and a label once there are enough samples."""
    ratios = [a / e for e, a in pairs]
    med = statistics.median(ratios)
    bias = ("too few samples" if len(pairs) < CALIBRATION_MIN else "underestimates" if med > 1.5
            else "overestimates" if med < 1 / 1.5 else "calibrated")
    return {"samples": len(pairs), "median_ratio": round(med, 2),
            "mape": round(statistics.fmean(abs(a - e) / e for e, a in pairs), 3),
            "within_50": round(sum(0.5 <= r <= 1.5 for r in ratios) / len(ratios), 3), "bias": bias,
            "estimated": round(sum(e for e, _ in pairs), 2), "actual": round(sum(a for _, a in pairs), 2)}


def calibration(s: Any = None) -> dict[str, Any]:
    """Estimate accuracy (S07-R-04): per role, the finished tasks' estimated minutes against the minutes their runs
    took; and the finished goals' estimated cost against their model cost. Benchmark goals are left out."""
    if s is None:
        with session() as s2:
            return calibration(s2)
    real = select(Goal.id).where(Goal.origin != "eval")
    minutes = dict(s.execute(select(Run.task_id, func.sum(Run.duration)).where(Run.task_id.is_not(None))
                             .group_by(Run.task_id)).all())
    by_role: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for t in s.scalars(select(Task).where(Task.status == "done", Task.goal_id.in_(real))):
        try:
            est = float((t.contract or {}).get("estimated_minutes") or 0)
        except (TypeError, ValueError):
            continue
        if est > 0 and minutes.get(t.id):
            by_role[t.role].append((est, float(minutes[t.id]) / 60.0))
    spend = dict(s.execute(select(Run.goal_id, func.sum(Run.cost)).where(Run.goal_id.is_not(None))
                           .group_by(Run.goal_id)).all())
    costs = [(float(g.estimated_cost_usd), float(spend[g.id])) for g in s.scalars(
        select(Goal).where(Goal.origin != "eval", Goal.estimated_cost_usd > 0,
                           Goal.status.in_(("approved", "awaiting_approval", "failed", "rejected"))))
             if spend.get(g.id)]
    return {"time_by_role": [{"role": role} | _accuracy(pairs) for role, pairs in sorted(by_role.items())],
            "goal_cost": _accuracy(costs) if costs else None, "min_samples": CALIBRATION_MIN}


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
                                .where(Defect.status.in_(OPEN))) or 0
        company = {
            "goals": len(goals), "finished": len(finished), "passed": len(passed),
            "pass_rate": round(len(passed) / len(finished), 3) if finished else None,
            "avg_iterations_to_pass": round(sum(g.iteration for g in passed) / len(passed), 2) if passed else None,
            "cost_per_passed_goal": round(sum(spend.get(g.id, 0) or 0 for g in passed) / len(passed), 6) if passed else None,
            "open_defects": open_defects,
        }
        calibrated = calibration(s)
        ops = operations(s)
    return {"company": company, "rooms": rooms, "agents": agents, "calibration": calibrated, "operations": ops}
