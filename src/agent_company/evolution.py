"""Self-evolution: versioned playbooks, candidate experiments, clone merging.

  observe   every finished task is an outcome for the agent that ran it
  curate    every few new lessons, the playbook is rebuilt from them as a new version
  diagnose  an agent whose recent success rate is low gets a failure-aware candidate
            playbook (warnings first) instead of a direct change
  evaluate  the candidate runs for EVAL_WINDOW tasks; it is promoted to a new version
            only if its success rate beats the baseline by `min_improvement` points
            (a tie keeps the current version), else rolled back
  flag      an agent failing more than 70 % of its last 10 tasks is flagged for review
  merge     after a goal, its idle clones fold their XP, skills, counts and lessons
            back into the original and retire, so the registry does not sprawl

Every change is recorded as an evolution event and every playbook as a version, so
any version can be restored.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_company.config import settings
from agent_company.db import Agent, EvolutionEvent, Lesson, PlaybookVersion, Task, log_event, session

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
    for lesson in s.scalars(select(Lesson).where(Lesson.agent_id == agent_id, Lesson.outcome != "research")
                            .order_by(Lesson.id.desc()).limit(60)):
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
    """Share of the agent's last n task outcomes (lessons) that passed the quality gate."""
    rows = s.scalars(select(Lesson.outcome).where(Lesson.agent_id == agent_id,
                                                  Lesson.outcome.in_(["success", "failure"]))
                     .order_by(Lesson.id.desc()).limit(n)).all()
    return (sum(r == "success" for r in rows) / len(rows) if rows else 1.0), len(rows)


def after_task(agent_id: int, success: bool) -> None:
    """Called after each lesson is recorded (learning.learn)."""
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            return
        exp = dict(a.experiment or {})
        if exp:
            exp["outcomes"] = [*exp.get("outcomes", []), success]
            if len(exp["outcomes"]) >= EVAL_WINDOW:
                rate = sum(exp["outcomes"]) / len(exp["outcomes"])
                need = exp["baseline"] + settings().min_improvement / 100
                n = len(exp["outcomes"])
                _event(s, "evaluate", a.id, f"{a.name}: candidate {rate:.0%} vs baseline {exp['baseline']:.0%} "
                       f"(n={n}, needs {min(need, 1):.0%})", candidate=rate, baseline=exp["baseline"], samples=n,
                       needed=need)
                # S13-D-01: strictly better by min_improvement; a tie or a small edge keeps the current version
                if rate >= need - 1e-9 and rate > exp["baseline"]:
                    _new_version(s, a, exp["playbook"], f"experiment won ({rate:.0%} vs {exp['baseline']:.0%}, n={n})")
                    _event(s, "promote", a.id, f"{a.name}: playbook v{a.playbook_version} promoted")
                else:
                    _event(s, "rollback", a.id, f"{a.name}: candidate rolled back, v{a.playbook_version} kept")
                a.experiment = {}
            else:
                a.experiment = exp
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


def active_playbook(a: Agent) -> tuple[str, bool]:
    """(playbook text, is_trial) for prompts."""
    if a.experiment and a.experiment.get("playbook"):
        return a.experiment["playbook"], True
    return a.playbook or "", False


def restore(agent_id: int, version: int) -> dict[str, Any]:
    with session() as s:
        a = s.get(Agent, agent_id)
        v = s.scalars(select(PlaybookVersion).where(PlaybookVersion.agent_id == agent_id,
                                                    PlaybookVersion.version == version)).first()
        if a is None or v is None:
            raise KeyError(f"agent {agent_id} version {version}")
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
                   "outcomes": a.experiment.get("outcomes", [])}
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
        a.experiment = {"playbook": candidate, "baseline": rate, "outcomes": []}
        _event(s, "diagnose", a.id, f"{a.name}: success rate {rate:.0%} over the last {n} tasks")
        _event(s, "experiment", a.id, f"{a.name}: trying a failure-aware playbook for {EVAL_WINDOW} tasks")
    return True


def cycle() -> int:
    """Diagnose every active agent now (normally this happens after each task). Returns trials started."""
    from agent_company.governance import emergency

    if emergency():  # E5 stops the evolution cycle too
        return 0
    started = 0
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.lifecycle == "active")):
            had = bool(a.experiment)
            _diagnose(s, a)
            started += bool(a.experiment) and not had
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
