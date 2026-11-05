"""Capability gaps, learning plans and independent evaluation (SPEC-13 S13-R-01..R-03, S13-R-07).

  gaps      per agent and skill, readiness from verified knowledge, practical performance (gate outcomes of
            tasks with that skill), independent evaluation (eval goals) and recency; gaps are `missing_capability`
            (its role's tasks ask for a skill it lacks), `insufficient_proficiency`, `outdated` (no success in
            90 days), `poor_performance` (3 of its last 4 tasks on the skill failed) and `low_reliability` (runs
            that fail); `raise_gaps` (evolution.cycle) records each once a week and proposes a plan
  plans     a target level, objectives, knowledge to study, practice exercises and a separate final
            evaluation: eval suites (`evals/*.json`, `kind` practice|final, `skills`) run as eval goals in scratch
            repositories, never in a user project; the trainee is staffed for its role (Goal.trial). The final
            set's checks never reach a prompt (evaluation.run_suite hides them and gives one iteration)
  certify   only the harness's verdict on the final set changes a certified level: passing raises it to the
            target and the skill's evidence, failing recommends retraining; no agent or mentor report counts
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.db import (Agent, EvolutionEvent, Event, Goal, LearningPlan, Lesson, Run, Task, log_event, now,
                              session)

CATEGORIES = ("missing_capability", "insufficient_proficiency", "outdated", "poor_performance", "low_reliability")
OUTDATED_DAYS = 90
POOR_WINDOW, POOR_FAILURES = 4, 3
PROFICIENT = 0.6
RELIABLE, RELIABILITY_RUNS = 0.8, 5
MISSING_MIN_TASKS = 2
RAISE_EVERY_DAYS = 7
OPEN = ("planned", "running")
_running: set[int] = set()
_running_lock = threading.Lock()


def _spawn(fn: Callable[[], Any]) -> None:  # tests replace it
    threading.Thread(target=fn, daemon=True, name="learning-plan").start()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:64]


def _naive(dt):  # noqa: ANN001, ANN202 - SQLite hands back naive datetimes
    return dt.replace(tzinfo=None) if dt is not None else None


# --- gaps (S13-R-01) ---------------------------------------------------------------------

def _readiness(knowledge: int, practical: float | None, independent: float | None, last_success) -> float:  # noqa: ANN001
    age = (_naive(now()) - _naive(last_success)).days if last_success else None
    recency = 0.0 if age is None else max(0.0, min(1.0, (OUTDATED_DAYS - age) / (OUTDATED_DAYS - 30)))
    return round(0.2 * min(1.0, knowledge / 5) + 0.4 * (practical or 0.0) + 0.25 * (independent or 0.0)
                 + 0.15 * recency, 3)


def gaps(agent_id: int | None = None) -> list[dict[str, Any]]:
    """Every capability gap of active original agents (clones merge back into them), worst readiness first."""
    with session() as s:
        q = select(Agent).where(Agent.lifecycle == "active", Agent.enabled, Agent.parent_id.is_(None))
        if agent_id is not None:
            q = q.where(Agent.id == agent_id)
        agents = s.scalars(q).all()
        if not agents:
            return []
        ids = [a.id for a in agents]
        evals = set(s.scalars(select(Goal.id).where(Goal.origin == "eval")))
        outcomes: dict[tuple[int, str], list[Lesson]] = defaultdict(list)  # newest first
        tested: dict[tuple[int, str], list[bool]] = defaultdict(list)
        known: dict[tuple[int, str], int] = defaultdict(int)
        for les in s.scalars(select(Lesson).where(Lesson.agent_id.in_(ids)).order_by(Lesson.id.desc())):
            for sk in {_slug(x) for x in les.technologies or [] if _slug(x)}:
                key = (les.agent_id, sk)
                if les.status == "approved":
                    known[key] += 1
                if les.outcome not in {"success", "failure"}:
                    continue
                if les.goal_id in evals:
                    tested[key].append(les.outcome == "success")
                else:
                    outcomes[key].append(les)
        plans = {(p.agent_id, p.capability): p.id for p in s.scalars(select(LearningPlan).where(
            LearningPlan.agent_id.in_(ids), LearningPlan.status.in_(OPEN)))}
        # what each role's real tasks asked for
        asked: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for role, contract, gid in s.execute(select(Task.role, Task.contract, Task.goal_id)
                                             .order_by(Task.id.desc()).limit(500)):
            if gid in evals:
                continue
            for sk in {_slug(x) for x in (contract or {}).get("skills", []) if _slug(x)}:
                asked[role][sk] += 1
        runs: dict[int, list[bool]] = defaultdict(list)
        for aid, ok in s.execute(select(Run.agent_id, Run.ok).where(Run.agent_id.in_(ids)).order_by(Run.id.desc())):
            if len(runs[aid]) < 20:
                runs[aid].append(bool(ok))
        out: list[dict[str, Any]] = []
        cutoff = _naive(now() - timedelta(days=OUTDATED_DAYS))
        for a in agents:
            have = {_slug(k) for k in (a.skills or {})}
            skills = have | {sk for (aid, sk) in outcomes if aid == a.id}

            def gap(skill: str, category: str, detail: str, **evidence: Any) -> None:
                key = (a.id, skill)  # noqa: B023
                outs = outcomes.get(key, [])
                practical = sum(x.outcome == "success" for x in outs[:10]) / len(outs[:10]) if outs else None
                ev = tested.get(key, [])
                last_ok = next((x.created_at for x in outs if x.outcome == "success"), None)
                out.append({"agent_id": a.id, "agent": a.name, "role": a.role, "skill": skill, "category": category,  # noqa: B023
                            "detail": detail, "plan_id": plans.get(key),
                            "readiness": _readiness(known.get(key, 0), practical, sum(ev) / len(ev) if ev else None,
                                                    last_ok),
                            "evidence": {"outcomes": len(outs), "practical": None if practical is None
                                         else round(practical, 3), "evaluations": len(ev),
                                         "knowledge": known.get(key, 0),
                                         "last_success": last_ok.isoformat() if last_ok else None, **evidence}})

            for sk in sorted(skills):
                outs = outcomes.get((a.id, sk), [])
                if not outs:
                    continue
                recent = outs[:POOR_WINDOW]
                failed = sum(x.outcome != "success" for x in recent)
                rate = sum(x.outcome == "success" for x in outs[:10]) / len(outs[:10])
                last_ok = next((x.created_at for x in outs if x.outcome == "success"), None)
                if len(recent) >= POOR_FAILURES and failed >= POOR_FAILURES:
                    gap(sk, "poor_performance", f"failed {failed} of its last {len(recent)} '{sk}' tasks")
                elif len(outs) >= 3 and rate < PROFICIENT:
                    gap(sk, "insufficient_proficiency", f"passed {rate:.0%} of its last {len(outs[:10])} '{sk}' tasks")
                elif last_ok is not None and _naive(last_ok) < cutoff:
                    gap(sk, "outdated", f"no '{sk}' success in {OUTDATED_DAYS} days")
            for sk, n in sorted(asked.get(a.role, {}).items()):
                if sk not in have and sk != _slug(a.role) and n >= MISSING_MIN_TASKS and (a.id, sk) not in outcomes:
                    gap(sk, "missing_capability", f"{n} {a.role} task(s) asked for '{sk}', which it does not have",
                        requested=n)
            r = runs.get(a.id, [])
            if len(r) >= RELIABILITY_RUNS and sum(r) / len(r) < RELIABLE:
                gap("*", "low_reliability", f"{len(r) - sum(r)} of its last {len(r)} model runs failed",
                    runs=len(r))
    return sorted(out, key=lambda g: (g["readiness"], g["agent"], g["skill"]))


def raise_gaps() -> dict[str, int]:
    """E1/E2 (S13-R-04, automatic): record each gap once a week as an evolution event and propose a learning plan
    when a final evaluation suite covers the skill. Plans are started by a person (E3 costs model money)."""
    from agent_company.governance import notify

    raised = proposed = 0
    found = gaps()
    since = now() - timedelta(days=RAISE_EVERY_DAYS)
    with session() as s:
        recent = {(e.agent_id, (e.detail or {}).get("key")) for e in s.scalars(select(EvolutionEvent).where(
            EvolutionEvent.stage == "gap", EvolutionEvent.created_at > since))}
        fresh = []
        for g in found:
            key = f"{g['skill']}:{g['category']}"
            if (g["agent_id"], key) in recent:
                continue
            s.add(EvolutionEvent(stage="gap", agent_id=g["agent_id"], title=f"{g['agent']}: {g['category']} — "
                                 f"{g['detail']}"[:500], detail={"key": key, **g}))
            fresh.append(g)
        raised = len(fresh)
        if fresh:
            notify(f"{raised} capability gap(s) found", "; ".join(f"{g['agent']}: {g['detail']}" for g in fresh[:8]),
                   level="info", source="evolution", admin_only=True, s=s)
    for g in fresh:
        if g["plan_id"] or g["skill"] == "*":
            continue
        practice, final = suites_for(g["skill"])
        if not final:
            continue
        try:
            create_plan(g["agent_id"], g["skill"], gap=g["category"], practice_suite=practice, final_suite=final)
            proposed += 1
        except ValueError:
            continue
    return {"raised": raised, "plans_proposed": proposed}


# --- learning plans (S13-R-02) -------------------------------------------------------------

def suites_for(skill: str) -> tuple[str, str]:
    """(practice suite, final suite) that name the skill; empty strings when none does."""
    from agent_company import evaluation

    sk = _slug(skill)
    practice = final = ""
    for x in evaluation.list_suites():
        if sk not in {_slug(k) for k in x["skills"]}:
            continue
        if x["kind"] == "final" and not final:
            final = x["name"]
        elif x["kind"] == "practice" and not practice:
            practice = x["name"]
    return practice, final


def _kind(name: str) -> str:
    from agent_company import evaluation

    return evaluation.load_suite(name).get("kind", "benchmark")


def plan_dict(p: LearningPlan, names: dict[int, str] | None = None) -> dict[str, Any]:
    return {"id": p.id, "agent_id": p.agent_id, "agent": (names or {}).get(p.agent_id), "capability": p.capability,
            "gap": p.gap, "current_level": p.current_level, "target_level": p.target_level,
            "objectives": p.objectives or [], "resources": p.resources or [], "practice_suite": p.practice_suite,
            "final_suite": p.final_suite, "repeats": p.repeats, "pass_mark": p.pass_mark, "status": p.status,
            "results": p.results or {}, "recommendation": p.recommendation, "created_by": p.created_by,
            "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat()}


def create_plan(agent_id: int, capability: str, target_level: int | None = None, practice_suite: str = "",
                final_suite: str = "", gap: str = "", repeats: int = 1, pass_mark: float = 0.8,
                created_by: str = "system") -> dict[str, Any]:
    from agent_company import learning

    cap = _slug(capability)
    if not cap:
        raise ValueError("a capability is required")
    if not practice_suite and not final_suite:
        practice_suite, final_suite = suites_for(cap)
    if not final_suite:
        raise ValueError(f"no final evaluation suite covers '{cap}': add evals/<name>.json with \"kind\": \"final\" "
                         f"and \"skills\": [\"{cap}\"]")
    if _kind(final_suite) != "final":
        raise ValueError(f"'{final_suite}' is not a final evaluation set (kind \"final\")")
    if practice_suite and _kind(practice_suite) != "practice":
        raise ValueError(f"'{practice_suite}' is not a practice suite (kind \"practice\"): exercises and the final "
                         "evaluation must be separate sets")
    if not 1 <= int(repeats) <= 10 or not 0 < float(pass_mark) <= 1:
        raise ValueError("repeats is 1-10 and pass_mark is above 0 and at most 1")
    current = certified(agent_id).get(cap, {}).get("level", 0)
    target = int(target_level if target_level is not None else min(5, current + 1))
    if not current < target <= 5:
        raise ValueError(f"the target level must be above the current L{current} and at most L5")
    resources = [x["id"] for x in learning.retrieve(agent_id, query=cap.replace("_", " "), limit=5)]
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            raise KeyError(f"agent {agent_id}")
        if s.scalars(select(LearningPlan).where(LearningPlan.agent_id == agent_id, LearningPlan.capability == cap,
                                                LearningPlan.status.in_(OPEN))).first():
            raise ValueError(f"{a.name} already has an open learning plan for '{cap}'")
        objectives = [f"Reach L{target} in '{cap}' (now L{current})",
                      f"Pass at least {float(pass_mark):.0%} of the final evaluation '{final_suite}'"]
        if practice_suite:
            objectives.insert(1, f"Practise on '{practice_suite}'")
        if gap:
            objectives.append(f"Close the {gap.replace('_', ' ')} gap")
        p = LearningPlan(agent_id=agent_id, capability=cap, gap=gap, current_level=current, target_level=target,
                         objectives=objectives, resources=resources, practice_suite=practice_suite,
                         final_suite=final_suite, repeats=int(repeats), pass_mark=float(pass_mark),
                         created_by=created_by)
        s.add(p)
        s.flush()
        s.add(EvolutionEvent(stage="plan", agent_id=agent_id, title=f"{a.name}: learning plan #{p.id} for '{cap}' "
                             f"(L{current} → L{target})", detail={"plan_id": p.id}))
        return plan_dict(p, {a.id: a.name})


def list_plans(agent_id: int | None = None, status: str = "") -> list[dict[str, Any]]:
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        q = select(LearningPlan)
        if agent_id is not None:
            q = q.where(LearningPlan.agent_id == agent_id)
        if status:
            q = q.where(LearningPlan.status == status)
        return [plan_dict(p, names) for p in s.scalars(q.order_by(LearningPlan.id.desc()).limit(300))]


def get_plan(plan_id: int) -> dict[str, Any]:
    with session() as s:
        p = s.get(LearningPlan, plan_id)
        if p is None:
            raise KeyError(plan_id)
        return plan_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))


def start_plan(plan_id: int) -> dict[str, Any]:
    from agent_company.governance import emergency

    if emergency():
        raise PermissionError("an emergency stop is active (E5)")
    with session() as s:
        p = s.get(LearningPlan, plan_id)
        if p is None:
            raise KeyError(plan_id)
        if p.status not in {"planned", "running"}:
            raise ValueError(f"plan #{plan_id} is {p.status}")
        p.status = "running"
    _launch(plan_id)
    return get_plan(plan_id)


def _launch(plan_id: int) -> bool:
    with _running_lock:
        if plan_id in _running:
            return False
        _running.add(plan_id)
    _spawn(lambda: run_plan(plan_id))
    return True


def cancel_plan(plan_id: int) -> dict[str, Any]:
    with session() as s:
        p = s.get(LearningPlan, plan_id)
        if p is None:
            raise KeyError(plan_id)
        if p.status not in OPEN:
            raise ValueError(f"plan #{plan_id} is {p.status}")
        p.status = "cancelled"
    return get_plan(plan_id)


def resume_plans() -> int:
    """Plans a restart cut off run again from the start (evolution.cycle)."""
    with session() as s:
        ids = list(s.scalars(select(LearningPlan.id).where(LearningPlan.status == "running")))
    return sum(_launch(i) for i in ids)


def run_plan(plan_id: int) -> dict[str, Any]:
    """Practice, then the final evaluation; the harness's pass rate on the final set decides (S13-R-03)."""
    from agent_company import evaluation
    from agent_company.governance import notify

    try:
        with session() as s:
            p = s.get(LearningPlan, plan_id)
            if p is None or p.status != "running":
                return {}
            agent_id, practice, final, repeats = p.agent_id, p.practice_suite, p.final_suite, p.repeats
        trial = {"trainee_id": agent_id, "plan_id": plan_id}
        results: dict[str, Any] = {}
        for side, suite in (("practice", practice), ("final", final)):
            if not suite:
                continue
            if not _still_running(plan_id):
                return {}
            label = f"plan{plan_id}-{side}-{int(time.time() * 1000)}"
            summary = evaluation.run_suite(suite, label, repeats=repeats, trial=trial | {"side": side})
            results[side] = {k: summary.get(k) for k in ("label", "runs", "passed", "pass_rate", "avg_iterations",
                                                         "total_cost")}
            with session() as s:
                p = s.get(LearningPlan, plan_id)
                p.results = {**(p.results or {}), side: results[side]}
        with session() as s:
            p = s.get(LearningPlan, plan_id)
            a = s.get(Agent, agent_id)
            if p is None or p.status != "running":
                return {}
            rate = float(results["final"]["pass_rate"] or 0)
            name = a.name if a else f"agent {agent_id}"
            if rate >= p.pass_mark - 1e-9:
                p.status = "passed"
                p.recommendation = (f"certified L{p.target_level} in '{p.capability}': {rate:.0%} of the final "
                                    f"evaluation '{final}' (n={results['final']['runs']})")
                if a is not None:  # LE-011: the skill's evidence follows the harness's verdict
                    skills = dict(a.skills or {})
                    skills[p.capability] = max(int(skills.get(p.capability, 0)), min(10, 2 * p.target_level))
                    a.skills = skills
                stage = "certify"
            else:
                p.status = "failed"
                p.recommendation = (f"retrain '{p.capability}': {rate:.0%} of the final evaluation '{final}', "
                                    f"needs {p.pass_mark:.0%}. Study the plan's knowledge items and repeat the practice "
                                    "suite before another final evaluation")
                stage = "retrain"
            s.add(EvolutionEvent(stage=stage, agent_id=agent_id, title=f"{name}: {p.recommendation}"[:500],
                                 detail={"plan_id": plan_id, "final": results["final"]}))
            notify(f"Learning plan #{plan_id} {p.status}", f"{name}: {p.recommendation}",
                   level="info" if p.status == "passed" else "warning", source="evolution", admin_only=True, s=s)
            log_event("learning", f"plan #{plan_id} {p.status}: {name} {p.recommendation}", agent_id=agent_id, s=s)
        return get_plan(plan_id)
    except Exception as e:  # noqa: BLE001 - a broken suite must not leave the plan running forever
        with session() as s:
            p = s.get(LearningPlan, plan_id)
            if p is not None and p.status == "running":
                p.status, p.recommendation = "failed", f"the evaluation run failed: {type(e).__name__}: {e}"
        log_event("error", f"learning plan #{plan_id} failed: {type(e).__name__}: {e}")
        return {}
    finally:
        with _running_lock:
            _running.discard(plan_id)


def _still_running(plan_id: int) -> bool:
    with session() as s:
        p = s.get(LearningPlan, plan_id)
        return p is not None and p.status == "running"


def certified(agent_id: int, s: Session | None = None) -> dict[str, dict[str, Any]]:
    """Certified level per capability, from final evaluations only (S13-R-03, LE-022): the newest finished plan
    of a capability decides; a failed one keeps the level the agent had when it started."""
    def run(s: Session) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for p in s.scalars(select(LearningPlan).where(LearningPlan.agent_id == agent_id,
                                                      LearningPlan.status.in_(["passed", "failed"]))
                           .order_by(LearningPlan.id.desc())):
            if p.capability in out:
                continue
            out[p.capability] = {"level": p.target_level if p.status == "passed" else p.current_level,
                                 "plan_id": p.id, "status": p.status, "at": p.updated_at.isoformat(),
                                 "pass_rate": ((p.results or {}).get("final") or {}).get("pass_rate")}
        return out

    if s is not None:
        return run(s)
    with session() as s2:
        return run(s2)


# --- learning metrics (S13-R-07) -------------------------------------------------------------

def metrics() -> dict[str, Any]:
    """First-pass success, rework, knowledge reuse and benchmark results per playbook version."""
    from agent_company.db import Experiment

    with session() as s:
        goals = [g for g in s.scalars(select(Goal).where(Goal.origin != "eval"))
                 if g.status in {"approved", "awaiting_approval", "failed", "rejected"}]
        passed = {g.id for g in goals if g.status in {"approved", "awaiting_approval"}}
        first = [g for g in goals if g.id in passed and g.iteration <= 1]
        retrieved: dict[int, set[int]] = defaultdict(set)
        for gid, data in s.execute(select(Event.goal_id, Event.data).where(Event.kind == "retrieval",
                                                                          Event.goal_id.is_not(None))):
            retrieved[gid] |= {int(x) for x in (data or {}).get("ids", [])}
        finished = {g.id for g in goals}
        items = set().union(*(v for k, v in retrieved.items() if k in finished)) if retrieved else set()
        reused = set().union(*(v for k, v in retrieved.items() if k in passed)) if retrieved else set()
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        versions = []
        for e in s.scalars(select(Experiment).where(Experiment.target == "playbook").order_by(Experiment.id.desc())
                           .limit(50)):
            r = e.results or {}
            if "baseline" in r or "candidate" in r:
                versions.append({"experiment_id": e.id, "agent": names.get(e.agent_id or 0), "agent_id": e.agent_id,
                                 "baseline_version": e.baseline_version,
                                 "baseline": (r.get("baseline") or {}).get("pass_rate"),
                                 "candidate": (r.get("candidate") or {}).get("pass_rate"), "status": e.status})
    return {"finished_goals": len(goals),
            "first_pass_success_rate": round(len(first) / len(goals), 3) if goals else None,
            "rework_frequency": round(sum(g.iteration for g in goals) / len(goals), 2) if goals else None,
            "knowledge_items_retrieved": len(items),
            "knowledge_reuse_rate": round(len(reused) / len(items), 3) if items else None,
            "benchmark_by_playbook_version": versions}
