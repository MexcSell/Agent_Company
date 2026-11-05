"""Self-evaluation of orchestration and recommended strategies (docs/requirements/SPEC-04-orchestration.md S04-R-13,
FR-27, FR-28, §20, AT-031..037).

evaluate    a performance report of every workflow template version (strategies.py) from the goals that ran under
            it: acceptance, first-iteration passes, goals that used every iteration, iterations, time, cost per
            accepted goal, failed runs (retries), conflict reviews, plans replaced by the template's own, and the
            bottlenecks (most frequent failed checks, roles with the most failed runs). Every figure carries its
            sample size; there is no combined score. Eval, benchmark and improvement goals are left out.
recommend   deterministic rules on the current version's evidence propose a candidate template: one more iteration
            when goals keep running out of them, one task at a time when parallel tasks keep conflicting, an
            independent review when a template without one is often not accepted. The candidate is recorded as a
            `strategy` experiment (SPEC-13) with the base version, the changes, the reasons and the evidence.
            After every decided goal the kind is looked at again (`after_outcome`); a recommendation is only
            recorded when a rule fires and the kind has no open strategy experiment.
policy      §20.3: a candidate may not weaken what guards quality and authority: it never turns on file changes or
            recruitment, drops the checks, the independent review or protected tests, nor widens the paths a
            template allows. A candidate that does is recorded as `rejected` with the violations (AT-034).
run         both sides on the same eval suite and repeats, in scratch repositories with the L3 sandbox for checks
            (AT-033): eval goals take the side's template from `Goal.trial["strategy"]`, so neither side touches
            the template in force. Better = a pass rate higher by `min_improvement` points; or, §20.5, cheaper or
            faster with the pass rate, compliance and security not lower ("a lower cost or faster result must not
            override quality"). Either way it then waits for an administrator (AT-035).
apply       an administrator saves the candidate as a new version (AT-036); a canary watches the final word
            (approved, rejected, failed) of the next goals of that kind under it. When the window can no longer
            reach the previous version's live acceptance rate (or the suite's baseline without live goals) the
            previous version is restored (AT-037).
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_company import strategies
from agent_company.config import settings
from agent_company.db import Event, Experiment, Goal, GoalTransition, Run, Task, log_event, session

OUTCOME = {"approved": True, "awaiting_approval": True, "rejected": False, "failed": False}
LEFT_OUT = ("eval", "improvement")  # trial and platform-benchmark goals are not live orchestration evidence
MIN_SAMPLES = 5
WINDOW = 20  # the latest decided goals per version a report looks at
CANARY_WINDOW = 3
MAX_ITERATIONS = 10


# --- evaluation (FR-27, AT-031) -----------------------------------------------------------------

def _rate(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def _version(g: Goal) -> Any:
    return (g.strategy or {}).get("version")


def _metrics(s: Session, goals: list[Goal]) -> dict[str, Any]:
    n = len(goals)
    ids = [g.id for g in goals]
    accepted = [g for g in goals if OUTCOME[g.status]]
    out: dict[str, Any] = {"n": n, "accepted": len(accepted), "acceptance_rate": _rate(len(accepted), n)}
    if not n:
        return out
    cost = dict(s.execute(select(Run.goal_id, func.coalesce(func.sum(Run.cost), 0.0))
                          .where(Run.goal_id.in_(ids)).group_by(Run.goal_id)).all())
    failed_runs = dict(s.execute(select(Run.goal_id, func.count()).where(Run.goal_id.in_(ids), Run.ok.is_(False))
                                 .group_by(Run.goal_id)).all())
    decided: dict[int, Any] = {}
    for gid, at in s.execute(select(GoalTransition.goal_id, func.min(GoalTransition.created_at))
                             .where(GoalTransition.goal_id.in_(ids),
                                    GoalTransition.to_status.in_(["awaiting_approval", "approved", "failed"]))
                             .group_by(GoalTransition.goal_id)):
        decided[gid] = at
    conflicts = set(s.scalars(select(Event.goal_id).where(Event.goal_id.in_(ids), Event.kind == "decision",
                                                          Event.message.like("%disagree on%"))))
    fallbacks = set(s.scalars(select(Event.goal_id).where(Event.goal_id.in_(ids), Event.kind == "plan",
                                                          Event.message.like("%using the standard plan%"))))
    minutes = [(decided[g.id] - g.created_at).total_seconds() / 60 for g in goals
               if g.id in decided and decided[g.id] and g.created_at]
    first = sum(1 for g in accepted if g.iteration <= 1)
    at_limit = sum(1 for g in goals if not OUTCOME[g.status] and g.iteration >= g.max_iterations)
    total_cost = sum(float(cost.get(i, 0.0)) for i in ids)
    out.update({
        "first_pass_rate": _rate(first, n), "at_limit": at_limit, "at_limit_rate": _rate(at_limit, n),
        "avg_iterations": round(sum(g.iteration for g in goals) / n, 2),
        "avg_minutes": round(sum(minutes) / len(minutes), 1) if minutes else None, "minutes_n": len(minutes),
        "avg_cost": round(total_cost / n, 6),
        "cost_per_accepted": round(total_cost / len(accepted), 6) if accepted else None,
        "failed_runs_per_goal": round(sum(failed_runs.values()) / n, 2),
        "conflict_rate": _rate(len(conflicts), n), "fallback_rate": _rate(len(fallbacks), n)})
    checks: Counter[str] = Counter()
    for g in goals:
        checks.update({c for h in g.history or [] for c in h.get("failed_checks") or []})
    roles = Counter(dict(s.execute(select(Task.role, func.count()).join(Run, Run.task_id == Task.id)
                                   .where(Run.goal_id.in_(ids), Run.ok.is_(False)).group_by(Task.role)).all()))
    out["bottlenecks"] = {"failed_checks": [{"check": c, "goals": k} for c, k in checks.most_common(3)],
                          "failing_roles": [{"role": r, "failed_runs": k} for r, k in roles.most_common(3)]}
    return out


def _decided(s: Session, kind: str) -> list[Goal]:
    """Live goals that ran under the kind's rules: an inferred kind is only a label (strategies.enforced)."""
    rows = s.scalars(select(Goal).where(Goal.kind == kind, Goal.status.in_(list(OUTCOME)),
                                        Goal.origin.not_in(list(LEFT_OUT))).order_by(Goal.id.desc()))
    return [g for g in rows if strategies.enforced(g)]


def evaluate_kind(s: Session, kind: str) -> dict[str, Any]:
    kind = strategies.check_kind(kind)
    current = strategies.effective(kind, s)
    by_version: dict[Any, list[Goal]] = {}
    for g in _decided(s, kind):
        bucket = by_version.setdefault(_version(g), [])
        if len(bucket) < WINDOW:
            bucket.append(g)
    versions = [{"version": v, "current": v == current["version"], **_metrics(s, gs)}
                for v, gs in sorted(by_version.items(), key=lambda kv: kv[0] if isinstance(kv[0], int) else -1,
                                       reverse=True)]
    cur = next((v for v in versions if v["current"]), {"version": current["version"], "current": True, "n": 0})
    return {"kind": kind, "version": current["version"], "template": current, "current": cur, "versions": versions,
            "enough": cur.get("n", 0) >= MIN_SAMPLES, "min_samples": MIN_SAMPLES}


def evaluate(kind: str = "") -> list[dict[str, Any]]:
    """AT-031: the performance report of each kind's template versions."""
    with session() as s:
        report = [evaluate_kind(s, k) for k in ([kind] if kind else strategies.KINDS)]
        for r in report:
            r["recommendation"] = _rules(r)
            r["open_experiment"] = _open_for(s, r["kind"])
        return report


# --- recommendation (FR-28, AT-032) -------------------------------------------------------------

def _rules(report: dict[str, Any]) -> dict[str, Any]:
    """The changes the evidence supports, with a reason each. Nothing without MIN_SAMPLES decided goals."""
    t, m = report["template"], report["current"]
    if not report["enough"]:
        return {"changes": {}, "reasons": [f"{m.get('n', 0)} decided goal(s) under v{t['version']}; "
                                           f"{MIN_SAMPLES} are needed before a recommendation"]}
    changes: dict[str, Any] = {}
    reasons: list[str] = []
    if (m.get("at_limit_rate") or 0) >= 0.3 and t["max_iterations"] < MAX_ITERATIONS:
        changes["max_iterations"] = t["max_iterations"] + 1
        reasons.append(f"{m['at_limit']} of {m['n']} goals used all {t['max_iterations']} iterations without "
                       "passing: one more iteration")
    if t["execution"] == "parallel" and (m.get("conflict_rate") or 0) >= 0.3:
        changes["execution"] = "sequential"
        reasons.append(f"conflict reviews in {m['conflict_rate']:.0%} of {m['n']} goals: one task at a time")
    if t["review"] == "none" and (m.get("acceptance_rate") or 0) < 0.8:
        changes["review"] = "independent_qa"
        reasons.append(f"{m['acceptance_rate']:.0%} of {m['n']} goals accepted without an independent review: "
                       "add one")
    return {"changes": changes, "reasons": reasons or ["no rule fires on the evidence"]}


def _open_for(s: Session, kind: str) -> int | None:
    from agent_company.experiments import FINAL

    for e in s.scalars(select(Experiment).where(Experiment.target == "strategy",
                                                Experiment.status.not_in(list(FINAL)))):
        if (e.candidate or {}).get("kind") == kind:
            return e.id
    return None


def violations(base: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    """§20.3 and §20.5: what a candidate may not do on its own (a person saves such a version directly)."""
    out = []
    if cand["writes"] and not base["writes"]:
        out.append("turns on file changes")
    if cand["recruit"] and not base["recruit"]:
        out.append("turns on recruitment of new agents")
    if cand["checks"] == "none" and base["checks"] != "none":
        out.append("drops the quality checks")
    if cand["review"] == "none" and base["review"] != "none":
        out.append("drops the independent review")
    if base["protect_tests"] and not cand["protect_tests"]:
        out.append("stops protecting the existing tests")
    if base["allowed_paths"] and (not cand["allowed_paths"] or set(cand["allowed_paths"]) - set(base["allowed_paths"])):
        out.append("widens the paths the template allows")
    if strategies.ANY in cand["roles"] and strategies.ANY not in base["roles"]:
        out.append("opens the template to any role")
    return out


def candidate_body(kind: str, changes: dict[str, Any], s: Session) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the base template in force, the candidate: the base with the changes), the candidate validated."""
    base = strategies.effective(kind, s)
    unknown = set(changes) - strategies.FIELDS
    if unknown:
        raise strategies.StrategyError(f"unknown template field(s) {', '.join(sorted(unknown))}")
    body = {k: copy.deepcopy(base[k]) for k in strategies.FIELDS} | copy.deepcopy(changes)
    return base, strategies.validate(body)


def record(s: Session, kind: str, changes: dict[str, Any], *, reasons: list[str], evidence: dict[str, Any] | None,
           hypothesis: str = "", suite: str = "smoke", repeats: int = 1, origin: str = "evolution",
           created_by: str = "system") -> Experiment:
    """AT-032/034: the candidate as a strategy experiment, `proposed`, or `rejected` with the policy violations."""
    from agent_company import experiments

    kind = strategies.check_kind(kind)
    try:
        base, body = candidate_body(kind, changes, s)
        bad = violations(base, body)
    except strategies.StrategyError as e:
        base, body, bad = strategies.effective(kind, s), {}, [str(e)]
    changes = {k: v for k, v in changes.items() if body and v != base.get(k)}
    if not bad and not changes:
        raise ValueError(f"the candidate is the {kind} template v{base['version']} itself")
    text = hypothesis or (f"{kind} template v{base['version']} with " + ", ".join(f"{k}={v}" for k, v in
                                                                                   changes.items())
                          + " is accepted more often (or as often, cheaper or faster) on the same suite")
    e = experiments.record(s, "strategy", role=kind, baseline_version=f"v{base['version']}",
                           candidate={"kind": kind, "base_version": base["version"], "changes": changes,
                                      "body": body, "reasons": reasons, "evidence": evidence or {}},
                           hypothesis=text, suite=suite, repeats=repeats, origin=origin, created_by=created_by)
    if bad:
        experiments.update(s, e.id, status="rejected", decision="policy (§20.3): " + "; ".join(bad))
        log_event("evolution", f"strategy experiment #{e.id} rejected: {'; '.join(bad)}", s=s)
    else:
        log_event("evolution", f"strategy experiment #{e.id} proposed for {kind} v{base['version']}: "
                  + "; ".join(reasons)[:400], s=s)
    return e


def parse_changes(candidate: str) -> dict[str, Any]:
    try:
        changes = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"a strategy candidate is a JSON object of template fields to change: {e}") from e
    if not isinstance(changes, dict) or not changes:
        raise ValueError("a strategy candidate is a JSON object of template fields to change")
    return changes


def recommend(kind: str, actor: str = "system", suite: str = "smoke", repeats: int = 1,
              s: Session | None = None) -> dict[str, Any]:
    """FR-28: record the candidate the evidence supports, if a rule fires and no experiment of the kind is open."""
    if s is None:
        with session() as own:
            out = recommend(kind, actor, suite, repeats, own)
        return out
    report = evaluate_kind(s, kind)
    rec = _rules(report)
    if not rec["changes"]:
        return {"kind": report["kind"], "recorded": None, **rec}
    open_id = _open_for(s, report["kind"])
    if open_id:
        return {"kind": report["kind"], "recorded": None, "open_experiment": open_id, **rec}
    for e in s.scalars(select(Experiment).where(Experiment.target == "strategy", Experiment.status.in_(
            ["rejected", "rolled_back"]))):  # a candidate turned down for this version is not proposed again
        c = e.candidate or {}
        if c.get("kind") == report["kind"] and c.get("base_version") == report["version"] \
                and c.get("changes") == rec["changes"]:
            return {"kind": report["kind"], "recorded": None, "turned_down": e.id, **rec}
    e = record(s, report["kind"], rec["changes"], reasons=rec["reasons"],
               evidence={k: v for k, v in report["current"].items() if k != "bottlenecks"} | {
                   "bottlenecks": report["current"].get("bottlenecks", {})},
               suite=suite, repeats=repeats, origin="evolution" if actor == "system" else "user", created_by=actor)
    if e.status == "proposed":
        from agent_company.governance import notify

        notify(f"Strategy experiment #{e.id}: {report['kind']} template",
               "; ".join(rec["reasons"]) + ". Run both sides on Evolution → Experiments.", level="info",
               source="evolution", admin_only=True, s=s)
    return {"kind": report["kind"], "recorded": e.id, "status": e.status, **rec}


# --- the experiment (AT-033, AT-035) ------------------------------------------------------------

def side_trial(e: Experiment, side: str) -> dict[str, Any]:
    c = e.candidate or {}
    if side == "baseline":
        with session() as s:  # the version the candidate was written against, as it was then
            from agent_company.db import Strategy

            row = s.scalars(select(Strategy).where(Strategy.kind == c["kind"],
                                                   Strategy.version == c["base_version"])).first()
            body = copy.deepcopy(row.body) if row else strategies.effective(c["kind"], s)
        version: Any = c["base_version"]
    else:
        body, version = copy.deepcopy(c["body"]), f"exp{e.id}"
    body = {k: body[k] for k in strategies.FIELDS}
    return {"kind": c["kind"], "version": version, "body": body}


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """§20.5: better by `min_improvement` points, or not worse in quality and cheaper or faster by that much."""
    from agent_company import experiments

    out = experiments.compare(baseline, candidate)
    if out["better"]:
        out["why"] = "pass rate"
        return out
    step = settings().min_improvement / 100
    guards = []
    for dim, key in (("completion", "pass_rate"), ("compliance", "compliant_rate"), ("security", "secure_rate")):
        b = ((baseline.get("dimensions") or {}).get(dim) or {}).get(key)
        c = ((candidate.get("dimensions") or {}).get(dim) or {}).get(key)
        if dim == "completion":
            b, c = baseline.get("pass_rate"), candidate.get("pass_rate")
        if b is not None and (c is None or c < b - 1e-9):
            guards.append(f"{dim} {c} < {b}")
    gains = []
    for key in ("cost_per_passed", "avg_seconds"):
        b, c = baseline.get(key), candidate.get(key)
        if b and c is not None and c <= b * (1 - step) + 1e-12:
            gains.append(key)
    out["quality_guards"] = guards
    out["efficiency_gains"] = gains
    if not guards and gains and candidate.get("passed"):
        out["better"], out["why"] = True, "efficiency with quality not lower: " + ", ".join(gains)
    return out


def run(exp_id: int) -> dict[str, Any]:
    """Both sides of a strategy experiment; a better candidate only waits for an administrator."""
    from agent_company import evaluation, experiments
    from agent_company.governance import notify

    try:
        with session() as s:
            e = s.get(Experiment, exp_id)
            sides_trial = {side: side_trial(e, side) for side in ("baseline", "candidate")}
            kind, suite, repeats = e.candidate["kind"], e.dataset.get("suite", "smoke"), int(e.dataset.get("repeats", 1))
        sides: dict[str, dict[str, Any]] = {}
        for side in ("baseline", "candidate"):
            with session() as s:
                experiments.update(s, exp_id, status=side)
            label = f"exp{exp_id}-{side}"
            trial = {"experiment_id": exp_id, "side": side, "strategy": sides_trial[side]}
            summary = evaluation.run_suite(suite, f"{label}-{_stamp()}", repeats=repeats, trial=trial)
            sides[side] = experiments.side_metrics(summary) | {"dimensions": summary.get("dimensions") or {}}
            with session() as s:
                experiments.update(s, exp_id, **{side: sides[side]})
        cmp = compare(sides["baseline"], sides["candidate"])
        b, c = sides["baseline"]["pass_rate"] or 0, sides["candidate"]["pass_rate"] or 0
        verdict = (f"recommend the candidate {kind} template ({cmp['why']}): {c:.0%} vs {b:.0%} "
                   f"(n={cmp['samples']['candidate']}/{cmp['samples']['baseline']}); an administrator applies it"
                   if cmp["better"] else
                   f"keep {kind} v{sides_trial['baseline']['version']}: candidate {c:.0%} vs {b:.0%}, needs "
                   f"{cmp['needed']:.0%}" + (f" (quality lower: {'; '.join(cmp['quality_guards'])})"
                                             if cmp.get("quality_guards") else ""))
        with session() as s:
            experiments.update(s, exp_id, status="review", decision=verdict, comparison=cmp)
            notify(f"Experiment #{exp_id}: {kind} template", verdict + ".", level="info", source="evolution",
                   admin_only=True, s=s)
            log_event("evolution", f"experiment #{exp_id}: {verdict}", s=s)
        return experiments.get(exp_id)
    except Exception as e:  # noqa: BLE001 - a broken suite must not leave the experiment running forever
        with session() as s:
            experiments.update(s, exp_id, status="failed", decision=f"{type(e).__name__}: {e}")
        log_event("error", f"experiment #{exp_id} failed: {type(e).__name__}: {e}")
        return {}


def _stamp() -> int:
    import time

    return int(time.time() * 1000)


# --- deployment and canary (AT-036, AT-037) -----------------------------------------------------

def apply(s: Session, e: Experiment, actor: str) -> None:
    """An administrator's approval: the candidate becomes the kind's newest version, watched by a canary."""
    from agent_company import experiments

    c = e.candidate or {}
    kind = c["kind"]
    if not (e.results or {}).get("comparison", {}).get("better"):
        raise ValueError(f"experiment #{e.id} did not show a better candidate; it cannot be applied")
    current = strategies.effective(kind, s)
    if current["version"] != c["base_version"]:
        raise ValueError(f"the {kind} template is v{current['version']} now, the candidate was measured against "
                         f"v{c['base_version']}: propose it again")
    report = evaluate_kind(s, kind)
    rate, n, source = report["current"].get("acceptance_rate"), report["current"].get("n", 0), "live"
    if not n:
        rate, source = float(((e.results or {}).get("baseline") or {}).get("pass_rate") or 0.0), "suite"
    row = strategies.save(kind, c["body"], actor, f"experiment #{e.id}: " + "; ".join(c.get("reasons") or [])[:300], s)
    e.reviewed_by = actor
    experiments.update(s, e.id, status="canary", decision=f"applied by {actor} as {kind} v{row['version']}",
                       canary={"window": CANARY_WINDOW, "baseline": float(rate or 0.0), "baseline_samples": n,
                               "baseline_source": source, "outcomes": {}, "previous": current["version"],
                               "deployed": row["version"]})
    log_event("evolution", f"experiment #{e.id}: {kind} template v{row['version']} in force "
                           f"(canary of {CANARY_WINDOW} goals)", s=s)


def observe(s: Session, g: Goal, outcome: bool) -> None:
    """A goal's final word (approved, rejected, failed): it counts for the canary of the template version it ran
    under; a goal waiting for approval does not count yet, as the person's rejection is the quality signal (§20.5).
    The previous version comes back as soon as the window can no longer reach the baseline (AT-037)."""
    from agent_company import experiments
    from agent_company.governance import notify

    for e in s.scalars(select(Experiment).where(Experiment.target == "strategy", Experiment.status == "canary")):
        c = dict((e.results or {}).get("canary") or {})
        if (e.candidate or {}).get("kind") != g.kind or c.get("deployed") != _version(g):
            continue
        outs = {**(c.get("outcomes") or {}), str(g.id): bool(outcome)}  # a goal counts once, its last word
        c["outcomes"] = outs
        window, base, kind = int(c.get("window", CANARY_WINDOW)), float(c.get("baseline", 0)), g.kind
        passed = sum(outs.values())
        best = (passed + max(0, window - len(outs))) / max(window, len(outs))
        if best < base - 1e-9:
            current = strategies.effective(kind, s)["version"]
            if current == c.get("deployed"):
                strategies.restore(kind, int(c["previous"]), "system", s)
                what = f"v{c['previous']} restored"
            else:
                what = f"v{current} is in force now; nothing restored"
            experiments.update(s, e.id, status="rolled_back", canary=c,
                               decision=f"canary {passed}/{len(outs)} cannot reach the baseline {base:.0%}; {what}")
            notify(f"Experiment #{e.id} rolled back", f"The {kind} template: {what}.", level="warning",
                   source="evolution", admin_only=True, s=s)
            log_event("evolution", f"experiment #{e.id} rolled back: {what}", s=s)
        elif len(outs) >= window:
            experiments.update(s, e.id, status="rolled_out", canary=c,
                               decision=f"canary {passed}/{len(outs)} at or above the baseline {base:.0%}")
        else:
            experiments.update(s, e.id, canary=c)


def after_outcome(s: Session, g: Goal, to: str) -> None:
    """workflow.set_status: a live goal of a kind was decided."""
    if to not in OUTCOME or not g.kind or g.origin in LEFT_OUT or not strategies.enforced(g):
        return
    if to != "awaiting_approval":
        observe(s, g, OUTCOME[to])
    s.flush()
    recommend(g.kind, s=s)
