"""Self-evolution: versioned playbooks, candidate experiments, clone merging.

  observe   every finished task is an outcome for the agent that ran it
  curate    every few new lessons, the playbook is rebuilt from them as a new version
  diagnose  an agent whose recent success rate is low gets a failure-aware candidate
            playbook (warnings first) instead of a direct change
  smoke     with `candidate_smoke` on, the candidate first runs the smoke suite against the current
            playbook, off the goal loop; it reaches users' goals only if it passes at least as many
            runs (S13-D-02), else it is rolled back
  evaluate  the candidate runs for EVAL_WINDOW tasks (the canary); it is promoted to a new version
            only if its success rate beats the baseline by `min_improvement` points
            (a tie keeps the current version), else rolled back, as soon as the rest of the window
            can no longer reach that
  flag      an agent failing more than 70 % of its last 10 tasks is flagged for review
  merge     after a goal, its idle clones fold their XP, skills, counts and lessons
            back into the original and retire, so the registry does not sprawl

Every change is recorded as an evolution event and every playbook as a version, so
any version can be restored; every candidate is an experiment record (experiments.py, S13-R-05).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from agent_company.config import settings
from agent_company.db import (Agent, EvolutionEvent, Goal, Lesson, PlaybookVersion, Task, log_event, now,
                              session)

SMOKE_SUITE = "smoke"
_smoke_lock = threading.Lock()  # one smoke trial at a time: eval goals pick the side under test from the trial
_smoking: set[int] = set()  # agents whose smoke trial is queued or running
_smoking_lock = threading.Lock()


def _spawn(fn: Callable[[], Any]) -> None:  # tests replace it
    threading.Thread(target=fn, daemon=True, name="smoke-trial").start()


EVAL_WINDOW = 3
DIAGNOSE_WINDOW = 6
LOW_SUCCESS = 0.5
CURATE_EVERY = 3
PLAYBOOK_LINES = 10


def _event(s: Session, stage: str, agent_id: int | None, title: str, **detail: Any) -> None:
    s.add(EvolutionEvent(stage=stage, agent_id=agent_id, title=title[:500], detail=detail))


def curate(s: Session, agent_id: int, failures_first: bool = False) -> str:
    """Playbook from the most recent distinct lessons: DO rules from successes, AVOID from failures."""
    seen: set[str] = set()
    rows: list[tuple[int, str]] = []
    # The playbook goes into every prompt of the agent, on any project: only screened, current items that may
    # leave their project feed it (S11-R-05, SPEC-14 S14-R-05). Confidential lessons stay retrievable in their
    # own project only (learning.retrieve).
    cutoff = now().replace(tzinfo=None)
    for lesson in s.scalars(select(Lesson).where(Lesson.agent_id == agent_id, Lesson.outcome != "research",
                                                 Lesson.status == "approved",
                                                 Lesson.classification.in_(["public", "internal"]))
                            .order_by(Lesson.id.desc()).limit(60)):
        if lesson.review_due_at is not None and lesson.review_due_at.replace(tzinfo=None) <= cutoff:
            continue
        key = lesson.text.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        fail = lesson.outcome != "success"
        rows.append((0 if (fail and failures_first) else 1, ("AVOID: " if fail else "DO: ") + lesson.text))
    rows.sort(key=lambda r: r[0])  # stable: keeps recency within each group
    return "\n".join(text for _, text in rows[:PLAYBOOK_LINES])


def _new_version(s: Session, a: Agent, playbook: str, reason: str) -> None:
    a.playbook_version = (a.playbook_version or 0) + 1
    a.playbook = playbook
    s.add(PlaybookVersion(agent_id=a.id, version=a.playbook_version, playbook=playbook, reason=reason))


def success_rate(s: Session, agent_id: int, n: int) -> tuple[float, int]:
    """Share of the agent's last n task outcomes (lessons) on real work that passed the quality gate; benchmark
    runs are measured by the eval harness, not here."""
    evals = select(Goal.id).where(Goal.origin == "eval")
    rows = s.scalars(select(Lesson.outcome).where(Lesson.agent_id == agent_id,
                                                  Lesson.outcome.in_(["success", "failure"]),
                                                  or_(Lesson.goal_id.is_(None), Lesson.goal_id.not_in(evals)))
                     .order_by(Lesson.id.desc()).limit(n)).all()
    return (sum(r == "success" for r in rows) / len(rows) if rows else 1.0), len(rows)


def after_task(agent_id: int, success: bool) -> None:
    """Called after each lesson is recorded (learning.learn)."""
    _after_task(agent_id, success)
    from agent_company import experiments

    experiments.observe(agent_id, success)  # the canary of a route change to the agent's role (S13-R-05)
    start_smoke(agent_id)


def _after_task(agent_id: int, success: bool) -> None:
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            return
        exp = dict(a.experiment or {})
        if exp and _stage(exp) == "smoke":  # users' goals still run the current playbook: nothing to count
            _flag_unreliable(s, a)
            return
        if exp:
            from agent_company import experiments

            exp["outcomes"] = [*exp.get("outcomes", []), success]
            outs, n = exp["outcomes"], len(exp["outcomes"])
            need = min(1.0, exp["baseline"] + settings().min_improvement / 100)
            canary = {"window": EVAL_WINDOW, "baseline": exp["baseline"], "needed": round(min(need, 1), 3),
                      "outcomes": outs}
            # LE-018: the canary ends as soon as the rest of the window cannot reach what promotion needs
            hopeless = (sum(outs) + EVAL_WINDOW - n) / EVAL_WINDOW < need - 1e-9
            if n >= EVAL_WINDOW or hopeless:
                rate = sum(outs) / n
                _event(s, "evaluate", a.id, f"{a.name}: candidate {rate:.0%} vs baseline {exp['baseline']:.0%} "
                       f"(n={n}, needs {min(need, 1):.0%})", candidate=rate, baseline=exp["baseline"], samples=n,
                       needed=need)
                # S13-D-01: strictly better by min_improvement; a tie or a small edge keeps the current version
                if not hopeless and rate >= need - 1e-9 and rate > exp["baseline"] and experiments.may_deploy(
                        "playbook", "system"):
                    _new_version(s, a, exp["playbook"], f"experiment won ({rate:.0%} vs {exp['baseline']:.0%}, n={n})")
                    _event(s, "promote", a.id, f"{a.name}: playbook v{a.playbook_version} promoted")
                    experiments.update(s, exp.get("experiment_id"), status="rolled_out", canary=canary,
                                       decision=f"promoted as v{a.playbook_version}: {rate:.0%} live, n={n}")
                else:
                    _event(s, "rollback", a.id, f"{a.name}: candidate rolled back, v{a.playbook_version} kept")
                    experiments.update(s, exp.get("experiment_id"), status="rolled_back", canary=canary,
                                       decision=f"canary {sum(outs)}/{n} live, needs {min(need, 1):.0%}; "
                                                f"v{a.playbook_version} kept")
                a.experiment = {}
            else:
                a.experiment = exp
                experiments.update(s, exp.get("experiment_id"), canary=canary)
            return
        _flag_unreliable(s, a)
        since = s.scalar(select(func.max(PlaybookVersion.created_at)).where(PlaybookVersion.agent_id == a.id))
        q = select(func.count()).select_from(Lesson).where(Lesson.agent_id == a.id, Lesson.outcome != "research")
        if since is not None:
            q = q.where(Lesson.created_at > since)
        fresh = s.scalar(q) or 0
        if _diagnose(s, a):
            return
        if fresh >= CURATE_EVERY or (a.playbook_version == 0 and fresh >= 1):
            playbook = curate(s, a.id)
            if playbook and playbook != a.playbook:
                _new_version(s, a, playbook, f"curated from {fresh} new lesson(s)")
                _event(s, "generate", a.id, f"{a.name}: playbook v{a.playbook_version}")


FLAG_WINDOW, FLAG_FAILURE_RATE = 10, 0.7


def _flag_unreliable(s: Session, a: Agent) -> None:
    """Behaviour rule (SPEC-14 S14-R-03): an agent that failed more than 70 % of its last 10 tasks is flagged
    for review (evolution and certification), once per window of 10 new outcomes."""
    from agent_company.governance import notify

    rate, n = success_rate(s, a.id, FLAG_WINDOW)
    if n < FLAG_WINDOW or 1 - rate <= FLAG_FAILURE_RATE:
        return
    last = s.scalar(select(func.max(EvolutionEvent.created_at)).where(EvolutionEvent.agent_id == a.id,
                                                                     EvolutionEvent.stage == "flag"))
    if last is not None:
        newer = s.scalar(select(func.count()).select_from(Lesson).where(
            Lesson.agent_id == a.id, Lesson.outcome.in_(["success", "failure"]), Lesson.created_at > last)) or 0
        if newer < FLAG_WINDOW:
            return
    _event(s, "flag", a.id, f"{a.name}: failed {1 - rate:.0%} of its last {n} tasks; flagged for review",
           failure_rate=1 - rate, samples=n)
    notify(f"{a.name} flagged for review", f"It failed {1 - rate:.0%} of its last {n} tasks.", level="warning",
           source="security", admin_only=True, s=s)
    log_event("security", f"{a.name} flagged for review: failed {1 - rate:.0%} of its last {n} tasks",
              agent_id=a.id, s=s, rule="failure_rate")


def _stage(exp: dict[str, Any]) -> str:
    return exp.get("stage") or "live"  # trials started before the smoke stage existed are live


def active_playbook(a: Agent, eval_run: bool = False) -> tuple[str, bool]:
    """(playbook text, is_trial) for prompts. A candidate still in its smoke stage is used only by eval goals,
    and only while the smoke trial runs its candidate side."""
    exp = a.experiment or {}
    if exp.get("playbook") and (_stage(exp) == "live" or (eval_run and exp.get("smoke_side") == "candidate")):
        return exp["playbook"], True
    return a.playbook or "", False


def start_smoke(agent_id: int) -> bool:
    """Start the smoke trial of the agent's candidate in the background, unless it has none or one runs."""
    with session() as s:
        a = s.get(Agent, agent_id)
        pending = a is not None and bool(a.experiment) and _stage(a.experiment) == "smoke"
    with _smoking_lock:
        if not pending or agent_id in _smoking:
            return False
        _smoking.add(agent_id)
    _spawn(lambda: smoke_trial(agent_id))
    return True


def _set_side(agent_id: int, side: str) -> bool:
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None or not a.experiment or _stage(a.experiment) != "smoke":
            return False  # the trial ended meanwhile (restored, rolled back)
        a.experiment = {**a.experiment, "smoke_side": side}
        return True


def smoke_trial(agent_id: int, repeats: int = 1) -> dict[str, float]:
    """Run the smoke suite with the current playbook, then with the candidate (S13-D-02, S13-R-05: same suite, same
    repeats). The candidate goes on to the live trial if it passes at least as many runs, else it is rolled back."""
    from agent_company import evaluation, experiments

    results: dict[str, float] = {}
    try:
        with session() as s:
            a = s.get(Agent, agent_id)
            exp = dict(a.experiment or {}) if a else {}
        suite, exp_id = exp.get("suite") or SMOKE_SUITE, exp.get("experiment_id")
        repeats = int(exp.get("repeats") or repeats)
        with _smoke_lock:
            for side in ("baseline", "candidate"):
                if not _set_side(agent_id, side):
                    return {}
                with session() as s:
                    experiments.update(s, exp_id, status=side)
                label = f"smoke-agent{agent_id}-{side}-{int(time.time() * 1000)}"
                # the agent under trial does the work (registry: the trial's trainee), both sides the same suite
                trial = {"trainee_id": agent_id, "side": side} | ({"experiment_id": exp_id} if exp_id else {})
                summary = evaluation.run_suite(suite, label, repeats=repeats, trial=trial)
                results[side] = summary["pass_rate"]
                with session() as s:
                    experiments.update(s, exp_id, **{side: experiments.side_metrics(summary)})
        with session() as s:
            a = s.get(Agent, agent_id)
            exp = dict(a.experiment or {}) if a else {}
            if not exp or _stage(exp) != "smoke":
                return {}
            passed = results["candidate"] >= results["baseline"]
            _event(s, "smoke", a.id, f"{a.name}: candidate {results['candidate']:.0%} vs current "
                   f"{results['baseline']:.0%} on the {suite} suite", passed=passed, **results)
            cmp = {"pass_rate_change": round(results["candidate"] - results["baseline"], 3), "passed": passed}
            if passed:
                exp.pop("smoke_side", None)
                a.experiment = {**exp, "stage": "live", "smoke": results}
                _event(s, "experiment", a.id, f"{a.name}: trying the candidate playbook for {EVAL_WINDOW} tasks")
                experiments.update(s, exp_id, status="canary", comparison=cmp,
                                   decision=f"passed the {suite} suite; on live work for {EVAL_WINDOW} tasks")
            else:
                a.experiment = {}
                _event(s, "rollback", a.id, f"{a.name}: candidate rolled back after the {suite} suite, "
                       f"v{a.playbook_version} kept")
                experiments.update(s, exp_id, status="rolled_back", comparison=cmp,
                                   decision=f"worse on the {suite} suite; v{a.playbook_version} kept")
        return results
    except Exception as e:  # noqa: BLE001 - a broken suite must not leave a candidate waiting forever
        log_event("error", f"smoke trial of agent {agent_id} failed: {type(e).__name__}: {e}", agent_id=agent_id)
        with session() as s:
            a = s.get(Agent, agent_id)
            if a is not None and a.experiment and _stage(a.experiment) == "smoke":
                experiments.update(s, a.experiment.get("experiment_id"), status="failed",
                                   decision=f"the smoke trial failed: {type(e).__name__}: {e}")
                a.experiment = {}
                _event(s, "rollback", a.id, f"{a.name}: candidate rolled back, the smoke trial failed: {e}"[:500])
        return {}
    finally:
        with _smoking_lock:
            _smoking.discard(agent_id)


def restore(agent_id: int, version: int) -> dict[str, Any]:
    with session() as s:
        a = s.get(Agent, agent_id)
        v = s.scalars(select(PlaybookVersion).where(PlaybookVersion.agent_id == agent_id,
                                                    PlaybookVersion.version == version)).first()
        if a is None or v is None:
            raise KeyError(f"agent {agent_id} version {version}")
        if a.experiment:
            from agent_company import experiments

            experiments.update(s, a.experiment.get("experiment_id"), status="rolled_back",
                               decision=f"ended by restoring v{version}")
        a.experiment = {}
        _new_version(s, a, v.playbook, f"restored v{version}")
        _event(s, "rollback", a.id, f"{a.name}: restored playbook v{version} as v{a.playbook_version}")
        return {"agent_id": a.id, "version": a.playbook_version}


def versions(agent_id: int) -> list[dict[str, Any]]:
    with session() as s:
        return [{"version": v.version, "playbook": v.playbook, "reason": v.reason, "at": v.created_at.isoformat()}
                for v in s.scalars(select(PlaybookVersion).where(PlaybookVersion.agent_id == agent_id)
                                   .order_by(PlaybookVersion.version.desc()))]


def overview(limit: int = 100) -> dict[str, Any]:
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        events = [{"stage": e.stage, "agent": names.get(e.agent_id), "agent_id": e.agent_id, "title": e.title,
                   "detail": e.detail, "at": e.created_at.isoformat()}
                  for e in s.scalars(select(EvolutionEvent).order_by(EvolutionEvent.id.desc()).limit(limit))]
        trials = [{"agent_id": a.id, "agent": a.name, "baseline": a.experiment.get("baseline"),
                   "outcomes": a.experiment.get("outcomes", []), "stage": _stage(a.experiment),
                   "experiment_id": a.experiment.get("experiment_id")}
                  for a in s.scalars(select(Agent)) if a.experiment]
    return {"events": events, "experiments": trials}


def _diagnose(s: Session, a: Agent) -> bool:
    """Start a failure-aware candidate playbook when the recent success rate is low. True if handled."""
    rate, n = success_rate(s, a.id, DIAGNOSE_WINDOW)
    if a.experiment or n < 4 or rate >= LOW_SUCCESS:
        return False
    last_rollback = s.scalar(select(func.max(EvolutionEvent.created_at)).where(
        EvolutionEvent.agent_id == a.id, EvolutionEvent.stage == "rollback"))
    if last_rollback is not None:  # cool down: judge a fresh window of outcomes first
        since = s.scalar(select(func.count()).select_from(Lesson).where(
            Lesson.agent_id == a.id, Lesson.outcome.in_(["success", "failure"]), Lesson.created_at > last_rollback))
        if (since or 0) < DIAGNOSE_WINDOW:
            return True
    candidate = curate(s, a.id, failures_first=True)
    if candidate and candidate != a.playbook:
        from agent_company import experiments

        smoke = settings().candidate_smoke
        row = experiments.record(s, "playbook", agent_id=a.id, role=a.role, baseline_version=f"v{a.playbook_version}",
                                 candidate={"playbook": candidate},
                                 hypothesis=f"Warnings from recent failures first lift {a.name}'s success rate "
                                            f"above {rate:.0%} (last {n} tasks)", suite=SMOKE_SUITE,
                                 status="baseline" if smoke else "canary")
        if not smoke:
            experiments.update(s, row.id, decision="candidate_smoke is off: straight to the live trial")
        a.experiment = {"playbook": candidate, "baseline": rate, "outcomes": [], "stage": "smoke" if smoke else "live",
                        "experiment_id": row.id}
        _event(s, "diagnose", a.id, f"{a.name}: success rate {rate:.0%} over the last {n} tasks")
        _event(s, "experiment", a.id, f"{a.name}: failure-aware playbook first runs the {SMOKE_SUITE} suite" if smoke
               else f"{a.name}: trying a failure-aware playbook for {EVAL_WINDOW} tasks")
    return True


def cycle() -> int:
    """Diagnose every active agent now (normally this happens after each task). Returns trials started."""
    from agent_company.governance import emergency

    if emergency():  # E5 stops the evolution cycle too
        return 0
    from agent_company.learning import expire_due

    expire_due()
    started = 0
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.lifecycle == "active")):
            had = bool(a.experiment)
            _diagnose(s, a)
            started += bool(a.experiment) and not had
        waiting = [a.id for a in s.scalars(select(Agent)) if a.experiment and _stage(a.experiment) == "smoke"]
    for agent_id in waiting:  # new candidates, and smoke trials a restart cut off
        start_smoke(agent_id)
    from agent_company import training

    try:  # capability gaps (S13-R-01) and learning plans a restart cut off
        training.raise_gaps()
        training.resume_plans()
    except Exception as e:  # noqa: BLE001 - the diagnosis above already happened
        log_event("error", f"capability gaps: {type(e).__name__}: {e}")
    return started


# --- clones -------------------------------------------------------------------------

def merge_clone(clone_id: int) -> dict[str, Any] | None:
    """Fold an idle clone's experience into its original and retire the clone."""
    with session() as s:
        c = s.get(Agent, clone_id)
        if c is None or c.parent_id is None or c.lifecycle == "retired" or c.status == "busy":
            return None
        o = s.get(Agent, c.parent_id)
        o.xp += c.xp
        o.level = 1 + o.xp // 50
        o.tasks_done += c.tasks_done
        o.tasks_failed += c.tasks_failed
        skills = dict(o.skills or {})
        for k, v in (c.skills or {}).items():
            skills[k] = max(int(skills.get(k, 0)), int(v))
        o.skills = skills
        moved = 0
        for lesson in s.scalars(select(Lesson).where(Lesson.agent_id == c.id)):
            lesson.agent_id = o.id
            moved += 1
        c.lifecycle, c.enabled, c.xp, c.tasks_done, c.tasks_failed = "retired", False, 0, 0, 0
        from agent_company.registry import record_version

        record_version(s, o, "system", f"merged clone {c.name}")
        record_version(s, c, "system", f"merged into {o.name} and retired")
        _event(s, "merge", o.id, f"{c.name} merged into {o.name} ({moved} lesson(s)) and retired")
        out = {"clone": c.name, "into": o.name, "lessons": moved}
    log_event("staff", f"{out['clone']} merged into {out['into']}")
    return out


def retire_goal_clones(goal_id: int) -> list[dict[str, Any]]:
    with session() as s:
        ids = set(s.scalars(select(Task.agent_id).where(Task.goal_id == goal_id, Task.agent_id.is_not(None))))
        clones = [a.id for a in s.scalars(select(Agent).where(Agent.id.in_(ids), Agent.parent_id.is_not(None)))]
    return [m for m in (merge_clone(c) for c in clones) if m]


def consolidate(role: str) -> list[dict[str, Any]]:
    with session() as s:
        clones = [a.id for a in s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_not(None),
                                                              Agent.lifecycle != "retired"))]
    return [m for m in (merge_clone(c) for c in clones) if m]
