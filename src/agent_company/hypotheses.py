"""Hypotheses (docs/requirements/ASEAO07-analysis.md S07-R-03, AT-008).

A hypothesis is a statement to test: why it is believed (`reason`), the evidence for and against it, how it is
tested, what was expected and what actually happened. Its life:

    proposed -> investigating -> testing -> supported | rejected | inconclusive -> archived

plus two exits the spec leaves implicit: a proposed or investigating hypothesis may be archived (dropped), and an
inconclusive one may go back to investigating. Nothing else is allowed.

An agent cannot mark a hypothesis supported without a recorded test result (`record_result`); a person may, with
a note (the API audits it). An experiment (SPEC-13) that states a hypothesis gets one here in `testing`, and its
comparison concludes it: better than the baseline -> supported, not better -> rejected, a side without runs ->
inconclusive.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.db import Hypothesis, log_event, now, session

STATUSES = ("proposed", "investigating", "testing", "supported", "rejected", "inconclusive", "archived")
CONCLUSIONS = ("supported", "rejected", "inconclusive")
TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"investigating", "archived"},
    "investigating": {"testing", "archived"},
    "testing": set(CONCLUSIONS),
    "supported": {"archived"},
    "rejected": {"archived"},
    "inconclusive": {"archived", "investigating"},
    "archived": set(),
}
SIDES = ("supporting", "contradicting")


def _row(h: Hypothesis) -> dict[str, Any]:
    return {"id": h.id, "statement": h.statement, "reason": h.reason, "supporting": h.supporting or [],
            "contradicting": h.contradicting or [], "test_method": h.test_method,
            "expected_outcome": h.expected_outcome, "actual_outcome": h.actual_outcome,
            "test_result": h.test_result or {}, "status": h.status, "project_id": h.project_id, "goal_id": h.goal_id,
            "experiment_id": h.experiment_id, "agent_id": h.agent_id, "created_by": h.created_by, "note": h.note,
            "next": sorted(TRANSITIONS.get(h.status, set())), "created_at": h.created_at.isoformat(),
            "updated_at": h.updated_at.isoformat() if h.updated_at else None}


def _is_agent(actor: str) -> bool:
    return actor.startswith("agent:") or actor == "system"


def create(statement: str, reason: str = "", *, actor: str, test_method: str = "", expected_outcome: str = "",
           project_id: int | None = None, goal_id: int | None = None, experiment_id: int | None = None,
           agent_id: int | None = None, status: str = "proposed", s: Session | None = None) -> dict[str, Any]:
    statement = (statement or "").strip()
    if len(statement) < 5:
        raise ValueError("state the hypothesis")
    if status not in ("proposed", "testing"):
        raise ValueError("a hypothesis starts as proposed (or testing, for an experiment)")
    if s is None:
        with session() as own:
            return create(statement, reason, actor=actor, test_method=test_method, expected_outcome=expected_outcome,
                          project_id=project_id, goal_id=goal_id, experiment_id=experiment_id, agent_id=agent_id,
                          status=status, s=own)
    h = Hypothesis(statement=statement[:2000], reason=(reason or "")[:4000], test_method=(test_method or "")[:2000],
                   expected_outcome=(expected_outcome or "")[:2000], project_id=project_id, goal_id=goal_id,
                   experiment_id=experiment_id, agent_id=agent_id, created_by=actor[:128], status=status)
    s.add(h)
    s.flush()
    log_event("hypothesis", f"hypothesis #{h.id} {status}: {statement[:200]}", goal_id=goal_id, agent_id=agent_id,
              s=s, hypothesis_id=h.id, actor=actor)
    return _row(h)


def _get(s: Session, hypothesis_id: int) -> Hypothesis:
    h = s.get(Hypothesis, hypothesis_id)
    if h is None:
        raise KeyError(f"hypothesis {hypothesis_id}")
    return h


def add_evidence(hypothesis_id: int, side: str, ref: dict[str, Any], actor: str) -> dict[str, Any]:
    """Cite evidence for (`supporting`) or against (`contradicting`) it: {"type": "task|goal|knowledge|document|
    experiment|url|note", "id": ..., "note": "..."}. Archived hypotheses take no more evidence."""
    if side not in SIDES:
        raise ValueError(f"side is one of {', '.join(SIDES)}")
    if not isinstance(ref, dict) or not (ref.get("id") or ref.get("note")):
        raise ValueError("evidence names what it is (type and id) or describes it (note)")
    item = {"type": str(ref.get("type") or "note")[:32], "id": ref.get("id"), "note": str(ref.get("note") or "")[:1000],
            "by": actor[:128], "at": now().isoformat()}
    with session() as s:
        h = _get(s, hypothesis_id)
        if h.status == "archived":
            raise ValueError("the hypothesis is archived")
        setattr(h, side, [*(getattr(h, side) or []), item][-50:])
        log_event("hypothesis", f"hypothesis #{h.id}: {side} evidence from {actor}", goal_id=h.goal_id, s=s,
                  hypothesis_id=h.id, side=side, ref=item)
        return _row(h)


def record_result(hypothesis_id: int, actual_outcome: str, result: dict[str, Any] | None, actor: str,
                  s: Session | None = None) -> dict[str, Any]:
    """Record what the test showed. `result` holds its source (an experiment, an eval run, a goal's gate, a person)
    and whether it held (`passed`)."""
    if s is None:
        with session() as own:
            return record_result(hypothesis_id, actual_outcome, result, actor, s=own)
    h = _get(s, hypothesis_id)
    if h.status not in ("investigating", "testing", "inconclusive"):
        raise ValueError(f"a {h.status} hypothesis takes no test result")
    if not (actual_outcome or "").strip():
        raise ValueError("say what actually happened")
    h.actual_outcome = actual_outcome.strip()[:4000]
    h.test_result = {"source": actor[:128], **dict(result or {}), "recorded_by": actor[:128], "at": now().isoformat()}
    log_event("hypothesis", f"hypothesis #{h.id}: test result recorded by {actor}", goal_id=h.goal_id, s=s,
              hypothesis_id=h.id, result=h.test_result)
    return _row(h)


def set_status(hypothesis_id: int, to: str, actor: str, note: str = "", s: Session | None = None) -> dict[str, Any]:
    """Move it along its life. An agent cannot mark it supported without a recorded test result; a person
    concluding it without one must say why (`note`)."""
    if s is None:
        with session() as own:
            return set_status(hypothesis_id, to, actor, note, s=own)
    if to not in STATUSES:
        raise ValueError(f"status is one of {', '.join(STATUSES)}")
    h = _get(s, hypothesis_id)
    if to not in TRANSITIONS.get(h.status, set()):
        raise ValueError(f"a {h.status} hypothesis cannot become {to}")
    if to == "supported" and not h.test_result:
        if _is_agent(actor):
            raise PermissionError("an agent cannot mark a hypothesis supported without a recorded test result")
        if not note.strip():
            raise ValueError("no test result is recorded: say why it is supported")
    before = h.status
    h.status = to
    if note:
        h.note = note.strip()[:2000]
    log_event("hypothesis", f"hypothesis #{h.id} {before} -> {to} by {actor}" + (f": {note[:200]}" if note else ""),
              goal_id=h.goal_id, s=s, hypothesis_id=h.id, actor=actor, before=before, to=to)
    return _row(h)


def for_experiment(s: Session, experiment_id: int, statement: str, test_method: str, expected: str,
                   agent_id: int | None = None) -> dict[str, Any] | None:
    """The hypothesis an experiment states, in `testing` (SPEC-13 S13-R-05)."""
    if len((statement or "").strip()) < 5:
        return None
    return create(statement, "stated by the experiment", actor="system", test_method=test_method,
                  expected_outcome=expected, experiment_id=experiment_id, agent_id=agent_id, status="testing", s=s)


def conclude_experiment(s: Session, experiment_id: int, comparison: dict[str, Any], decision: str = "") -> None:
    """An experiment's comparison concludes its hypothesis: better -> supported, not better -> rejected, a side
    without runs -> inconclusive. The comparison is the recorded test result."""
    h = s.scalars(select(Hypothesis).where(Hypothesis.experiment_id == experiment_id,
                                           Hypothesis.status == "testing")).first()
    if h is None:
        return
    held = comparison.get("better", comparison.get("passed"))
    samples = comparison.get("samples") or {}
    empty = any(v == 0 for v in samples.values())
    to = "inconclusive" if held is None or empty else "supported" if held else "rejected"
    outcome = decision or (f"pass rate change {comparison.get('pass_rate_change', 0):+.0%}"
                           if comparison.get("pass_rate_change") is not None else str(comparison)[:500])
    record_result(h.id, outcome, {"source": f"experiment:{experiment_id}", "passed": bool(held) if held is not None
                                  else None, "comparison": comparison}, "system", s=s)
    set_status(h.id, to, "system", s=s)


def list_hypotheses(status: str = "", experiment_id: int | None = None, goal_id: int | None = None,
                    project_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Hypothesis)
        if status:
            q = q.where(Hypothesis.status.in_(status.split(",")))
        if experiment_id is not None:
            q = q.where(Hypothesis.experiment_id == experiment_id)
        if goal_id is not None:
            q = q.where(Hypothesis.goal_id == goal_id)
        if project_id is not None:
            q = q.where(Hypothesis.project_id == project_id)
        return [_row(h) for h in s.scalars(q.order_by(Hypothesis.id.desc()).limit(limit))]


def get(hypothesis_id: int) -> dict[str, Any]:
    with session() as s:
        return _row(_get(s, hypothesis_id))
