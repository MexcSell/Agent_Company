"""Experiments: a candidate against the baseline on the same fixed suite (SPEC-13 S13-R-05, S13-R-06).

  propose   a target (`playbook` of one agent, the `route` of one role, the `strategy`: the workflow template
            of one goal kind, orchestration.py, or the `retrieval` ranking weights), the candidate (playbook text,
            a catalog model, the template fields or the weights to change), a hypothesis and the dataset (eval
            suite + repeats; a retrieval experiment always uses the retrieval evals and the fixed benchmark)
  baseline  the suite runs with the current version
  candidate the same suite, the same repeats, with the candidate: eval goals carry the side in `Goal.trial`
  compare   pass rate, iterations (rework), seconds and cost per passed run of both sides, with sample sizes;
            the candidate is better only if it beats the baseline by `min_improvement` points
  review    who may deploy is the self-improvement policy (S13-R-04, `AUTONOMY`): a playbook goes on by the
            automatic rule (S13-D-01), a route, a strategy or ranking weights wait for an administrator
  canary    the deployed candidate is watched on live work for a window of outcomes; when its success rate
            drops below the baseline the previous version is restored automatically (LE-018)
  rollout   the window ends at or above the baseline

Ranking weights have no canary: live work carries no relevance labels, so the two retrieval evals and the fixed
benchmark (benchmarks/retrieval.py, run with the weights of each side) are the whole measure. Applied weights are
`rolled_out` at once and keep the previous values, which an administrator's `rollback` restores.

Playbook candidates of the evolution loop (evolution.py) are recorded here as well; their smoke trial is the
baseline and candidate runs, their live trial the canary.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.config import settings
from agent_company.db import Agent, Experiment, Goal, Lesson, ModelEntry, Route, log_event, session

TARGETS = ("playbook", "route", "strategy", "retrieval")
# §12.2: what the platform never changes on its own; the proposal path rejects them (SPEC-14 S14-R-02)
RESTRICTED = ("governance", "security", "permissions", "budgets", "approvals", "policy")
# S13-R-04: the self-improvement levels and who controls them
AUTONOMY = {
    "E0": ("observe outcomes", "automatic"),
    "E1": ("identify capability gaps", "automatic"),
    "E2": ("propose a learning plan or an experiment", "automatic"),
    "E3": ("test in the sandbox (eval suite in scratch repositories)", "automatic within budget"),
    "E4": ("recommend deployment", "automatic rule for playbooks (S13-D-01), an administrator for routes, "
           "workflow templates and ranking weights"),
    "E5": ("deploy", "automatic only for playbooks; routes, models, workflow templates and ranking weights need an "
           "administrator"),
    "E6": ("change governance, security, permissions, budgets or approvals", "never autonomous"),
}
DEPLOY_BY = {"playbook": "system", "route": "administrator", "strategy": "administrator", "retrieval": "administrator"}
CANARY_WINDOW = 3
FINAL = {"rolled_out", "rolled_back", "rejected", "failed"}
_running: set[int] = set()
_running_lock = threading.Lock()


def _spawn(fn: Callable[[], Any]) -> None:  # tests replace it
    threading.Thread(target=fn, daemon=True, name="experiment").start()


def check_target(target: str) -> str:
    t = (target or "").strip().lower()
    if t in RESTRICTED:
        raise PermissionError(f"'{t}' is level E6: governance, security, permissions, budgets and approvals are "
                              "never changed by an experiment (SPEC-13 §12.2)")
    if t not in TARGETS:
        raise ValueError(f"target is one of {', '.join(TARGETS)}")
    return t


def may_deploy(target: str, actor: str) -> bool:
    """E5: a playbook may be deployed by the system's own rule; a route, a workflow template or ranking weights only
    by a person."""
    return DEPLOY_BY[check_target(target)] == "system" or actor.startswith("user:")


def to_dict(e: Experiment, names: dict[int, str] | None = None) -> dict[str, Any]:
    return {"id": e.id, "target": e.target, "agent_id": e.agent_id, "agent": (names or {}).get(e.agent_id or 0),
            "role": e.role, "baseline_version": e.baseline_version, "candidate": e.candidate,
            "hypothesis": e.hypothesis, "dataset": e.dataset, "results": e.results, "status": e.status,
            "origin": e.origin, "decision": e.decision, "reviewed_by": e.reviewed_by, "created_by": e.created_by,
            "deploy_by": DEPLOY_BY.get(e.target, "administrator"),
            "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat()}


def source_commit() -> str:
    """The platform's own commit the experiment was set up on (ASEAO10 S10-R-02: reproducible); "" outside git."""
    from agent_company import worktrees
    from agent_company.config import REPO_ROOT

    try:
        return worktrees.git(REPO_ROOT, "rev-parse", "HEAD")
    except Exception:  # noqa: BLE001 - not a git checkout
        return ""


def record(s: Session, target: str, *, agent_id: int | None = None, role: str = "", baseline_version: str = "",
           candidate: dict[str, Any] | None = None, hypothesis: str = "", suite: str = "smoke", repeats: int = 1,
           status: str = "proposed", origin: str = "evolution", created_by: str = "system",
           budget_usd: float = 0.0) -> Experiment:
    """`dataset` keeps what reproduces it: suite, repeats, the platform's commit and the budget (0 = none) its eval
    runs may cost; the configuration compared is `baseline_version` against `candidate`."""
    if budget_usd < 0:
        raise ValueError("the budget cannot be negative")
    e = Experiment(target=check_target(target), agent_id=agent_id, role=role, baseline_version=baseline_version,
                   candidate=dict(candidate or {}), hypothesis=hypothesis[:2000],
                   dataset={"suite": suite, "repeats": repeats, "source_commit": source_commit(),
                            "budget_usd": round(float(budget_usd), 4)},
                   status=status, origin=origin, created_by=created_by)
    s.add(e)
    s.flush()
    from agent_company import hypotheses  # ASEAO07 S07-R-03: the experiment's hypothesis, under test

    hypotheses.for_experiment(s, e.id, hypothesis, f"{target} experiment #{e.id}: baseline vs candidate on the "
                              f"'{suite}' suite, {repeats} repeat(s)",
                              f"the candidate beats the baseline by at least {settings().min_improvement} points",
                              agent_id=agent_id)
    return e


def update(s: Session, exp_id: int | None, status: str | None = None, decision: str | None = None,
           **results: Any) -> None:
    e = s.get(Experiment, exp_id) if exp_id else None
    if e is None:
        return
    if results:
        e.results = {**(e.results or {}), **results}
    if status:
        e.status = status
    if decision is not None:
        e.decision = decision[:2000]
    if isinstance(results.get("comparison"), dict):  # the comparison is the hypothesis's test result (S07-R-03)
        from agent_company import hypotheses

        hypotheses.conclude_experiment(s, e.id, results["comparison"], decision or "")


def side_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    """What a side is compared on (§10): success rate, rework (iterations), time, cost per verified run."""
    keys = ("label", "runs", "passed", "pass_rate", "avg_iterations", "avg_seconds", "total_cost", "cost_per_passed")
    out = {k: summary.get(k) for k in keys if k in summary}
    if "by_task" in summary:  # per task, for the regression guard (G-R-11)
        out["tasks_run"] = sorted(t["task"] for t in summary["by_task"])
        out["solved_tasks"] = sorted(t["task"] for t in summary["by_task"] if t["solved"])
    return out


def regressions(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str] | None:
    """G-R-11: the tasks the baseline solved in every repeat that the candidate ran and did not solve in every
    repeat. A candidate that breaks one is not better, whatever its pass rate. None when a side has no per-task
    record (recorded before it existed)."""
    if "solved_tasks" not in baseline or "solved_tasks" not in candidate:
        return None
    ran = set(candidate.get("tasks_run") or [])
    return sorted((set(baseline["solved_tasks"]) & ran) - set(candidate["solved_tasks"]))


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    b, c = float(baseline.get("pass_rate") or 0), float(candidate.get("pass_rate") or 0)
    need = b + settings().min_improvement / 100
    broken = regressions(baseline, candidate)
    solved = baseline.get("solved_tasks") or []
    out = {"pass_rate_change": round(c - b, 3), "samples": {"baseline": baseline.get("runs"),
                                                             "candidate": candidate.get("runs")},
           "needed": round(min(need, 1.0), 3), "better": c > b and c >= need - 1e-9 and not broken,
           "regressions": broken or [],
           "regression_rate": round(len(broken) / len(solved), 3) if broken is not None and solved else None}
    for k in ("avg_iterations", "avg_seconds", "cost_per_passed"):
        if baseline.get(k) is not None and candidate.get(k) is not None:
            out[f"{k}_change"] = round(candidate[k] - baseline[k], 6)
    return out


def list_experiments(status: str = "", target: str = "", limit: int = 200) -> list[dict[str, Any]]:
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        q = select(Experiment)
        if status == "open":
            q = q.where(Experiment.status.not_in(list(FINAL)))
        elif status:
            q = q.where(Experiment.status == status)
        if target:
            q = q.where(Experiment.target == target)
        return [to_dict(e, names) for e in s.scalars(q.order_by(Experiment.id.desc()).limit(limit))]


def get(exp_id: int) -> dict[str, Any]:
    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise KeyError(exp_id)
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        return to_dict(e, names)


def propose(target: str, candidate: str, *, agent_id: int | None = None, role: str = "", hypothesis: str = "",
            suite: str = "smoke", repeats: int = 1, created_by: str = "system", budget_usd: float = 0.0
            ) -> dict[str, Any]:
    """A user's experiment (E2). It runs nothing until it is started."""
    from agent_company import evaluation

    target = check_target(target)
    if target == "retrieval":
        suite, repeats = RETRIEVAL_SUITE, 1  # deterministic: one run of each eval and of the benchmark per side
    else:
        evaluation.load_suite(suite)  # validated now, not when it runs
    if not 1 <= int(repeats) <= 10:
        raise ValueError("repeats must be between 1 and 10")
    candidate = (candidate or "").strip()
    if not candidate:
        raise ValueError("a candidate is required: the playbook text, the model for the route, the template "
                         "fields or the ranking weights to change")
    with session() as s:
        if target == "retrieval":
            base = _current_weights()
            changes = parse_weights(candidate, base)
            e = record(s, target, role="knowledge", baseline_version=_weights_text(base),
                       candidate={"changes": changes, "weights": base | changes, "base": base,
                                  "strategies": _strategy_lines()},
                       hypothesis=hypothesis, suite=suite, repeats=1, origin="user", created_by=created_by)
        elif target == "strategy":  # S04-R-13: rejected at once when it breaks the §20.3 policy
            from agent_company import orchestration

            e = orchestration.record(s, role, orchestration.parse_changes(candidate),
                                     reasons=[f"proposed by {created_by}"], evidence=None, hypothesis=hypothesis,
                                     suite=suite, repeats=int(repeats), origin="user", created_by=created_by, budget_usd=budget_usd)
        elif target == "playbook":
            a = s.get(Agent, agent_id) if agent_id else None
            if a is None:
                raise KeyError(f"agent {agent_id}")
            e = record(s, target, agent_id=a.id, role=a.role, baseline_version=f"v{a.playbook_version}",
                       candidate={"playbook": candidate[:8000]}, hypothesis=hypothesis, suite=suite,
                       repeats=int(repeats), origin="user", created_by=created_by, budget_usd=budget_usd)
        else:
            role = (role or "").strip()
            if not role:
                raise ValueError("a route experiment names the role")
            if not s.scalars(select(ModelEntry).where(ModelEntry.name == candidate)).first():
                raise ValueError(f"unknown model {candidate!r}")
            r = s.get(Route, role)
            e = record(s, target, role=role, baseline_version=r.model_name if r else "",
                       candidate={"model": candidate}, hypothesis=hypothesis, suite=suite, repeats=int(repeats),
                       origin="user", created_by=created_by, budget_usd=budget_usd)
        out = e.id
    log_event("evolution", f"experiment #{out} proposed: {target} {role or agent_id} → {candidate[:60]}")
    return get(out)


def start(exp_id: int) -> dict[str, Any]:
    """E3: run the baseline and the candidate on the suite (in the background)."""
    from agent_company import evolution
    from agent_company.governance import emergency

    if emergency():
        raise PermissionError("an emergency stop is active (E5)")

    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise KeyError(exp_id)
        if e.status != "proposed":
            raise ValueError(f"experiment #{exp_id} is {e.status}")
        if e.target == "playbook":
            a = s.get(Agent, e.agent_id)
            if a is None:
                raise KeyError(f"agent {e.agent_id}")
            if a.experiment:
                raise ValueError(f"{a.name} already has a candidate on trial")
            rate, n = evolution.success_rate(s, a.id, evolution.DIAGNOSE_WINDOW)
            room = 1 - settings().min_improvement / 100
            if n == 0 or rate > room + 1e-9:  # the canary could never show the improvement promotion needs
                raise ValueError(f"{a.name} has " + ("no live outcomes yet" if n == 0 else
                                 f"a live success rate of {rate:.0%} over its last {n} tasks") +
                                 f": a candidate cannot beat it by min_improvement ({settings().min_improvement} "
                                 "points). Wait for live outcomes or lower min_improvement")
            a.experiment = {"playbook": e.candidate["playbook"], "baseline": rate, "outcomes": [], "stage": "smoke",
                            "experiment_id": e.id, "suite": e.dataset.get("suite", "smoke"),
                            "repeats": int(e.dataset.get("repeats", 1))}
            e.baseline_version = f"v{a.playbook_version}"
            agent_id = a.id
        e.status = "baseline"
        target = e.target
    if target == "playbook":
        evolution.start_smoke(agent_id)
    else:
        with _running_lock:
            if exp_id in _running:
                return get(exp_id)
            _running.add(exp_id)
        _spawn(lambda: run_route(exp_id) if target == "route" else run_retrieval(exp_id) if target == "retrieval"
               else _run_strategy(exp_id))
    return get(exp_id)


def _run_strategy(exp_id: int) -> dict[str, Any]:
    from agent_company import orchestration

    try:
        return orchestration.run(exp_id)
    finally:
        with _running_lock:
            _running.discard(exp_id)


class OverBudget(RuntimeError):
    pass


def run_sides(exp_id: int, trials: dict[str, dict[str, Any]], label: Callable[[str], str]) -> dict[str, dict[str, Any]]:
    """Baseline, then candidate, on the experiment's suite and repeats, within its budget (S10-R-02): the candidate
    gets what the baseline left. A side stopped on the budget stops the experiment (`OverBudget`): an incomplete
    side cannot be compared."""
    from agent_company import evaluation

    with session() as s:
        e = s.get(Experiment, exp_id)
        suite, repeats = e.dataset.get("suite", "smoke"), int(e.dataset.get("repeats", 1))
        budget = float(e.dataset.get("budget_usd") or 0.0)
    sides: dict[str, dict[str, Any]] = {}
    spent = 0.0
    for side in ("baseline", "candidate"):
        with session() as s:
            update(s, exp_id, status=side)
        left = budget - spent
        if budget > 0 and left <= 0:
            raise OverBudget(f"stopped on its budget: ${spent:.4f} of ${budget:.2f} spent before the {side}")
        summary = evaluation.run_suite(suite, label(side), repeats=repeats, trial=trials[side],
                                       **({"budget_usd": left} if budget > 0 else {}))
        spent += float(summary.get("total_cost") or 0.0)
        sides[side] = side_metrics(summary) | {"dimensions": summary.get("dimensions") or {}}
        with session() as s:
            update(s, exp_id, **{side: sides[side]})
        if summary.get("stopped_on_budget"):
            raise OverBudget(f"stopped on its budget: ${spent:.4f} of ${budget:.2f} spent during the {side} "
                             f"({summary.get('runs', 0)} of {repeats} repeat(s) per task)")
    return sides


def _stopped(exp_id: int, why: str) -> dict[str, Any]:
    from agent_company.governance import notify

    with session() as s:
        update(s, exp_id, status="failed", decision=why)
        notify(f"Experiment #{exp_id} stopped", why, level="warning", source="evolution", admin_only=True, s=s)
        log_event("evolution", f"experiment #{exp_id} {why}", s=s)
    return get(exp_id)


def run_route(exp_id: int) -> dict[str, Any]:
    """Both sides of a route experiment. A better candidate is only recommended: an administrator applies it."""
    from agent_company.governance import notify

    try:
        with session() as s:
            e = s.get(Experiment, exp_id)
            role, base, cand = e.role, e.baseline_version, e.candidate["model"]
        trials = {side: {"experiment_id": exp_id, "side": side} | ({"models": {role: model}} if model else {})
                  for side, model in (("baseline", base), ("candidate", cand))}
        try:
            sides = run_sides(exp_id, trials, lambda side: f"exp{exp_id}-{side}-{int(time.time() * 1000)}")
        except OverBudget as stop:
            return _stopped(exp_id, str(stop))
        cmp = compare(sides["baseline"], sides["candidate"])
        verdict = (f"recommend {cand} for {role}: {sides['candidate']['pass_rate']:.0%} vs "
                   f"{sides['baseline']['pass_rate']:.0%} (n={cmp['samples']['candidate']}/{cmp['samples']['baseline']})"
                   if cmp["better"] else
                   f"keep {base or 'the current route'} for {role}: {cand} {sides['candidate']['pass_rate']:.0%} vs "
                   f"{sides['baseline']['pass_rate']:.0%}, needs {cmp['needed']:.0%}"
                   + (f" and breaks {', '.join(cmp['regressions'])}" if cmp["regressions"] else ""))
        with session() as s:
            update(s, exp_id, status="review", decision=verdict, comparison=cmp)
            notify(f"Experiment #{exp_id}: {role} route", verdict + (". Apply it on Evolution → Experiments."
                                                                     if cmp["better"] else "."),
                   level="info", source="evolution", admin_only=True, s=s)
            log_event("evolution", f"experiment #{exp_id}: {verdict}", s=s)
        return get(exp_id)
    except Exception as e:  # noqa: BLE001 - a broken suite must not leave the experiment running forever
        with session() as s:
            update(s, exp_id, status="failed", decision=f"{type(e).__name__}: {e}")
        log_event("error", f"experiment #{exp_id} failed: {type(e).__name__}: {e}")
        return {}
    finally:
        with _running_lock:
            _running.discard(exp_id)


def role_success_rate(s: Session, role: str, n: int = 10) -> tuple[float, int]:
    """Live success rate of the role's agents over their last n task outcomes on real work."""
    evals = select(Goal.id).where(Goal.origin == "eval")
    ids = select(Agent.id).where(Agent.role == role)
    rows = s.scalars(select(Lesson.outcome).where(Lesson.agent_id.in_(ids), Lesson.outcome.in_(["success", "failure"]),
                                                  (Lesson.goal_id.is_(None)) | (Lesson.goal_id.not_in(evals)))
                     .order_by(Lesson.id.desc()).limit(n)).all()
    return (sum(r == "success" for r in rows) / len(rows) if rows else 1.0), len(rows)


def apply(exp_id: int, actor: str) -> dict[str, Any]:
    """E5 for a route, a workflow template or ranking weights: an administrator deploys the candidate; the canary
    then watches live outcomes (ranking weights have none, see the module's docstring)."""
    from agent_company import router

    weights: dict[str, float] | None = None
    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise KeyError(exp_id)
        if e.status != "review":
            raise ValueError(f"experiment #{exp_id} is {e.status}, not waiting for review")
        if not may_deploy(e.target, actor):
            raise PermissionError(f"a {e.target} change is deployed by an {DEPLOY_BY[e.target]} (S13-R-04)")
        if e.target == "strategy":
            from agent_company import orchestration

            orchestration.apply(s, e, actor)
            return to_dict(e)
        if e.target == "retrieval":
            c = e.candidate or {}
            if not (e.results or {}).get("comparison", {}).get("better"):
                raise ValueError(f"experiment #{exp_id} did not show better weights; it cannot be applied")
            if _current_weights() != c["base"]:
                raise ValueError(f"the ranking weights changed since experiment #{exp_id} measured them: "
                                 "propose it again")
            weights = dict(c["weights"])
        else:
            if e.target != "route":
                raise ValueError("playbook candidates are deployed by the evolution loop's own rule")
            role, model, previous = e.role, e.candidate["model"], e.baseline_version
            rate, n = role_success_rate(s, role)
            source = "live"
            if n == 0:  # no live history of the role: the suite's measurement of the current route stands in
                rate, source = float(((e.results or {}).get("baseline") or {}).get("pass_rate") or 0.0), "suite"
    if weights is not None:
        return _apply_retrieval(exp_id, weights, actor)
    router.set_route(role, model)
    with session() as s:
        e = s.get(Experiment, exp_id)
        e.reviewed_by = actor
        update(s, exp_id, status="canary", decision=f"applied by {actor}",
               canary={"window": CANARY_WINDOW, "baseline": rate, "baseline_samples": n, "baseline_source": source,
                       "outcomes": [], "previous": previous})
        log_event("evolution", f"experiment #{exp_id}: {model} is the {role} route (canary of {CANARY_WINDOW})", s=s)
    return get(exp_id)


def reject(exp_id: int, actor: str, note: str = "") -> dict[str, Any]:
    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise KeyError(exp_id)
        if e.status not in {"proposed", "review"}:
            raise ValueError(f"experiment #{exp_id} is {e.status}")
        e.reviewed_by = actor
        update(s, exp_id, status="rejected", decision=note or f"rejected by {actor}")
    return get(exp_id)


def observe(agent_id: int, success: bool) -> None:
    """A live outcome (evolution.after_task): it counts for the canary of a route applied to the agent's role.
    The previous route comes back as soon as the window can no longer reach the baseline."""
    from agent_company import router
    from agent_company.governance import notify

    restore: tuple[str, str] | None = None
    with session() as s:
        a = s.get(Agent, agent_id)
        role = a.role if a else ""
        for e in s.scalars(select(Experiment).where(Experiment.target == "route", Experiment.status == "canary",
                                                    Experiment.role == role)):
            c = dict((e.results or {}).get("canary") or {})
            outs = [*c.get("outcomes", []), bool(success)]
            c["outcomes"] = outs
            window, base = int(c.get("window", CANARY_WINDOW)), float(c.get("baseline", 0))
            best = (sum(outs) + window - len(outs)) / window  # every outcome still to come passes
            if best < base - 1e-9:
                update(s, e.id, status="rolled_back", canary=c,
                       decision=f"canary {sum(outs)}/{len(outs)} cannot reach the live baseline {base:.0%}; "
                                f"{c.get('previous') or 'the previous route'} restored")
                restore = (role, c.get("previous") or "")
                notify(f"Experiment #{e.id} rolled back", f"The {role} route went back to {c.get('previous')}.",
                       level="warning", source="evolution", admin_only=True, s=s)
            elif len(outs) >= window:
                update(s, e.id, status="rolled_out", canary=c,
                       decision=f"canary {sum(outs)}/{len(outs)} at or above the live baseline {base:.0%}")
            else:
                update(s, e.id, canary=c)
    if restore and restore[1]:
        router.set_route(*restore)
    elif restore:
        with session() as s:  # there was no route before: the role falls back to MODEL again
            r = s.get(Route, restore[0])
            if r is not None:
                s.delete(r)



# --- ranking weights (S13-R-05 `retrieval` target; SPEC-11 S11-R-08 guard) ---------------------------------------

RETRIEVAL_SUITE = "retrieval"  # evals/retrieval.json + evals/retrieval-classes.json + benchmarks/retrieval.py


def _current_weights() -> dict[str, float]:
    from agent_company import learning

    return {k: round(float(v), 6) for k, v in learning.weights().items()}


def _weights_text(w: dict[str, float]) -> str:
    return ", ".join(f"{k}={v:g}" for k, v in w.items())


def _strategy_lines() -> list[str]:
    from agent_company import learning

    return [f"{c}={s}" for c, s in sorted(learning.strategy_map().items())]


def parse_weights(text: str, base: dict[str, float]) -> dict[str, float]:
    """The weights a candidate changes: a JSON object or `name=value` pairs (commas, spaces or lines between them),
    names of `learning.WEIGHTS`, values within the settings' limits. Unchanged values are left out."""
    import json
    import re

    from agent_company import prefs

    text = (text or "").strip()
    try:
        raw = json.loads(text) if text.startswith("{") else None
    except ValueError as e:
        raise ValueError(f"the candidate is not a JSON object: {e}") from e
    if raw is None:
        raw = {}
        for pair in re.split(r"[\s,;]+", text):
            if pair:
                name, eq, value = pair.partition("=")
                if not eq:
                    raise ValueError(f"{pair!r} is not name=value")
                raw[name.strip()] = value.strip()
    if not isinstance(raw, dict):
        raise ValueError("the candidate names ranking weights and their values")
    if unknown := sorted(set(raw) - set(base)):
        raise ValueError(f"unknown ranking weights: {', '.join(unknown)} (known: {', '.join(base)})")
    changes = {}
    for name, value in raw.items():
        v = round(float(prefs._coerce(f"retrieval_w_{name}", value)), 6)
        if v != base[name]:
            changes[name] = v
    if not changes:
        raise ValueError("the candidate changes no ranking weight")
    return changes


def _benchmark(exp_id: int, w: dict[str, float], lines: list[str]) -> dict[str, Any]:
    """The fixed retrieval benchmark on the platform's code with these weights (env, its own fresh DATA_DIR, the
    L3 sandbox as for improvement contracts). A failure is recorded, and holds the candidate back."""
    from agent_company import selfevolve
    from agent_company.config import REPO_ROOT

    try:
        bench = selfevolve.load_benchmark(REPO_ROOT, "retrieval")
        work = settings().data_dir / "experiments" / str(exp_id)
        work.mkdir(parents=True, exist_ok=True)
        env = {f"RETRIEVAL_W_{k.upper()}": repr(float(v)) for k, v in w.items()} | {
            "RETRIEVAL_STRATEGIES": ",".join(lines)}
        out = selfevolve.run_benchmark(REPO_ROOT, bench, work, env)
        return {"metrics": out.get("metrics") or {}, "guard": bench["guard"], "sha256": bench["sha256"][:12]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:400]}


def retrieval_side(exp_id: int, w: dict[str, float], lines: list[str]) -> dict[str, Any]:
    """One side: recall@5 and MRR on evals/retrieval.json (the S11-R-08 guard) and on the class eval, MRR over all
    their cases, and the fixed benchmark."""
    from agent_company import learning

    strategies = learning.parse_strategies(lines)
    main = learning.evaluate_retrieval(strategies=strategies, **w)
    cls = learning.evaluate_retrieval(learning.RETRIEVAL_CLASS_EVAL, strategies=strategies, **w)
    n_main, n_cls = len(main["cases"]), len(cls["cases"])
    n = n_main + n_cls
    return {"label": _weights_text(w), "runs": n, "pass_rate": main["value"], "recall": main["value"],
            "mrr": main["mrr"], "class_recall": cls["value"], "class_mrr": cls["mrr"],
            "mrr_all": round((main["mrr"] * n_main + cls["mrr"] * n_cls) / n, 4) if n else 0.0,
            "benchmark": _benchmark(exp_id, w, lines)}


def compare_retrieval(b: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    """Better = MRR over all eval cases up by `min_improvement` points, recall@5 not lower on either eval, and the
    fixed benchmark's guard metrics not lower; any of them missing holds the candidate back."""
    need = b["mrr_all"] + settings().min_improvement / 100
    held = []
    if not (c["mrr_all"] > b["mrr_all"] and c["mrr_all"] >= need - 1e-9):
        held.append(f"MRR {c['mrr_all']:.3f} vs {b['mrr_all']:.3f}, needs {min(need, 1.0):.3f}")
    for key, name in (("recall", "evals/retrieval.json"), ("class_recall", "evals/retrieval-classes.json")):
        if c[key] < b[key] - 1e-9:
            held.append(f"recall@5 of {name} drops from {b[key]:.2f} to {c[key]:.2f}")
    bb, cb = b.get("benchmark") or {}, c.get("benchmark") or {}
    bench_change = {}
    if bb.get("error") or cb.get("error"):
        held.append(f"the fixed benchmark did not run: {bb.get('error') or cb.get('error')}")
    else:
        for g in cb.get("guard") or []:
            bv, cv = float(bb["metrics"].get(g, 0)), float(cb["metrics"].get(g, 0))
            bench_change[g] = round(cv - bv, 4)
            if cv < bv - 1e-9:
                held.append(f"benchmark {g} drops from {bv:.3f} to {cv:.3f}")
    return {"mrr_change": round(c["mrr_all"] - b["mrr_all"], 4), "recall_change": round(c["recall"] - b["recall"], 4),
            "class_recall_change": round(c["class_recall"] - b["class_recall"], 4), "benchmark_change": bench_change,
            "needed": round(min(need, 1.0), 4), "samples": {"baseline": b["runs"], "candidate": c["runs"]},
            "held_back": held, "better": not held}


def run_retrieval(exp_id: int) -> dict[str, Any]:
    """Both sides of a ranking-weight experiment, with the procedure per query class in force for both. Better
    weights are only recommended: an administrator applies them."""
    from agent_company.governance import notify

    try:
        with session() as s:
            e = s.get(Experiment, exp_id)
            base, cand = dict(e.candidate["base"]), dict(e.candidate["weights"])
            changes = _weights_text(e.candidate["changes"])
        if _current_weights() != base:
            return _stopped(exp_id, "the ranking weights changed after it was proposed: propose it again")
        lines = _strategy_lines()
        sides: dict[str, dict[str, Any]] = {}
        for side, w in (("baseline", base), ("candidate", cand)):
            with session() as s:
                update(s, exp_id, status=side)
            sides[side] = retrieval_side(exp_id, w, lines)
            with session() as s:
                update(s, exp_id, **{side: sides[side]})
        cmp = compare_retrieval(sides["baseline"], sides["candidate"])
        verdict = (f"recommend {changes}: MRR {sides['candidate']['mrr_all']:.3f} vs {sides['baseline']['mrr_all']:.3f} "
                   f"(n={cmp['samples']['candidate']})" if cmp["better"] else
                   f"keep the current ranking weights: {'; '.join(cmp['held_back'])}")
        with session() as s:
            update(s, exp_id, status="review", decision=verdict, comparison=cmp, strategies=lines)
            notify(f"Experiment #{exp_id}: ranking weights", verdict + (". Apply them on Evolution → Experiments."
                                                                       if cmp["better"] else "."),
                   level="info", source="evolution", admin_only=True, s=s)
            log_event("evolution", f"experiment #{exp_id}: {verdict}", s=s)
        return get(exp_id)
    except Exception as e:  # noqa: BLE001 - a broken eval file must not leave the experiment running forever
        with session() as s:
            update(s, exp_id, status="failed", decision=f"{type(e).__name__}: {e}")
        log_event("error", f"experiment #{exp_id} failed: {type(e).__name__}: {e}")
        return {}
    finally:
        with _running_lock:
            _running.discard(exp_id)


def _apply_retrieval(exp_id: int, weights: dict[str, float], actor: str) -> dict[str, Any]:
    from agent_company import prefs

    with session() as s:
        changes = dict(s.get(Experiment, exp_id).candidate["changes"])
    stored = prefs.stored_weights()
    previous = {k: stored.get(k) for k in changes}
    prefs.set_weights(changes, actor)  # recall@5 of the retrieval eval must hold for the change as a whole
    with session() as s:
        e = s.get(Experiment, exp_id)
        e.reviewed_by = actor
        update(s, exp_id, status="rolled_out", deployed={"weights": weights, "previous": previous},
               decision=f"applied by {actor}: {_weights_text(changes)} (no live canary: live work has no relevance "
                        "labels; roll back restores the previous weights)")
        log_event("evolution", f"experiment #{exp_id}: ranking weights {_weights_text(changes)} in force", s=s)
    return get(exp_id)


def rollback(exp_id: int, actor: str) -> dict[str, Any]:
    """An administrator restores the weights an applied ranking-weight experiment replaced (stored overrides back,
    or the .env values where there were none), unless they were changed again since."""
    from agent_company import prefs
    from agent_company.governance import notify

    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise KeyError(exp_id)
        deployed = (e.results or {}).get("deployed")
        if e.target != "retrieval" or e.status != "rolled_out" or not deployed:
            raise ValueError(f"experiment #{exp_id} has no applied ranking weights to roll back")
    if _current_weights() != {k: round(float(v), 6) for k, v in deployed["weights"].items()}:
        raise ValueError("the ranking weights were changed after this experiment applied them; set them on "
                         "Settings instead")
    prefs.set_weights(deployed["previous"], actor, check=False)  # restoring a known state is never refused
    with session() as s:
        e = s.get(Experiment, exp_id)
        update(s, exp_id, status="rolled_back", decision=f"rolled back by {actor}: the previous ranking weights "
                                                         "are in force again")
        notify(f"Experiment #{exp_id} rolled back", f"{actor} restored the previous ranking weights.",
               level="warning", source="evolution", admin_only=True, s=s)
    return get(exp_id)
