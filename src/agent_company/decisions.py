"""Decision records (docs/requirements/SPEC-04-orchestration.md S04-R-02, escalation S04-R-07).

A decision names its topic, the alternatives considered, the recommendation, reasoning, evidence,
assumptions and an estimated confidence. Decisions that need a person (`requires_human_approval`, and
every `critical` one) block the goal's merge until an administrator approves them.

Escalation levels (S04-R-07, `escalation.py`): 1 assigned agent, 2 goal GM, 3 room manager, 4 executive GM of the
platform project, 5 human. The level follows the category and the impact, not only the attempt count: `critical`
goes straight to 5, technical and organizational decisions about the protected platform project to 4. Decisions of
level 3 and 4 that need no person are escalated to that level's agent, who reads them in its next prompt.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from agent_company import escalation
from agent_company.db import Decision, Goal, Project, log_event, now, session

CATEGORIES = ["operational", "technical", "organizational", "financial", "critical"]
OPEN = ("draft", "submitted", "under_review", "escalated")
LEVEL_BY_CATEGORY = escalation.LEVEL_BY_CATEGORY


def _row(d: Decision) -> dict[str, Any]:
    return {"id": d.id, "goal_id": d.goal_id, "project_id": d.project_id, "topic": d.topic,
            "alternatives": d.alternatives or [], "recommendation": d.recommendation, "reasoning": d.reasoning,
            "evidence": d.evidence or [], "assumptions": d.assumptions or [], "confidence": d.confidence,
            "category": d.category, "requires_human_approval": d.requires_human_approval, "status": d.status,
            "escalation_level": d.escalation_level, "created_by_agent_id": d.created_by_agent_id,
            "approver": d.approver, "note": d.note, "decided_at": d.decided_at.isoformat() if d.decided_at else None,
            "at": d.created_at.isoformat()}


def record(goal_id: int | None, topic: str, recommendation: str, alternatives: list[Any] | None = None,
           reasoning: str = "", evidence: list[Any] | None = None, assumptions: list[Any] | None = None,
           confidence: float = 0.5, category: str = "technical", requires_human_approval: bool = False,
           agent_id: int | None = None, project_id: int | None = None) -> dict[str, Any]:
    """Store a decision. Ones that need no person are approved at once (approver 'auto'); the rest wait.
    `project_id` is for decisions taken outside a goal (a project topic's summary, S05-R-07)."""
    from agent_company.governance import notify

    category = category if category in CATEGORIES else "technical"
    human = bool(requires_human_approval) or category == "critical"
    with session() as s:
        g = s.get(Goal, goal_id) if goal_id else None
        p = s.get(Project, g.project_id if g else project_id) if (g and g.project_id) or project_id else None
        level = escalation.level_for(category, protected=bool(p and p.protected), human=human)
        d = Decision(goal_id=goal_id, project_id=g.project_id if g else project_id, topic=str(topic)[:256],
                     alternatives=list(alternatives or [])[:10], recommendation=str(recommendation)[:4000],
                     reasoning=str(reasoning)[:4000], evidence=list(evidence or [])[:20],
                     assumptions=list(assumptions or [])[:20], confidence=max(0.0, min(1.0, float(confidence or 0))),
                     category=category, requires_human_approval=human, escalation_level=level,
                     status="escalated" if human else "approved", approver="" if human else "auto",
                     decided_at=None if human else now(), created_by_agent_id=agent_id)
        s.add(d)
        s.flush()
        from agent_company import kgraph

        kgraph.on_decision(s, d.id, d.evidence)  # SPEC-11 S11-R-07
        out = _row(d)
        if human:
            log_event("escalation", f"decision #{d.id} '{d.topic}' needs a person (level 5)", goal_id=goal_id,
                      agent_id=agent_id, s=s, level=5, recipient="human", decision_id=d.id)
            notify(f"Decision needs approval: {d.topic}"[:256], f"{d.recommendation[:500]}", level="warning",
                   source="decision", goal_id=goal_id, project_id=d.project_id, admin_only=True, s=s)
        else:
            log_event("decision", f"decision #{d.id} '{d.topic}': {d.recommendation[:200]}", goal_id=goal_id,
                      agent_id=agent_id, s=s, level=level, decision_id=d.id)
    if not human and level in (3, 4):
        escalation.escalate(level, f"{category} decision #{out['id']} '{out['topic']}': {out['recommendation'][:300]}"
                            " (recorded; answer with a MESSAGE recommendation if it should change)",
                            goal_id=goal_id, agent_id=agent_id, decision_id=out["id"])
    return out


def decide(decision_id: int, approve: bool, actor: str, note: str = "") -> dict[str, Any]:
    with session() as s:
        d = s.get(Decision, decision_id)
        if d is None:
            raise KeyError(f"decision {decision_id}")
        if d.status not in OPEN:
            raise ValueError(f"decision is already {d.status}")
        d.status, d.approver, d.note, d.decided_at = ("approved" if approve else "rejected"), actor[:128], note[:2000], now()
        log_event("decision", f"decision #{d.id} {d.status} by {actor}", goal_id=d.goal_id, s=s, decision_id=d.id)
        return _row(d)


def blocking(goal_id: int) -> list[dict[str, Any]]:
    """Decisions that keep the goal from merging: a person must approve them first (also re-checked at merge
    time, so a revoked approval blocks the merge, AT-029)."""
    with session() as s:
        return [_row(d) for d in s.scalars(select(Decision).where(Decision.goal_id == goal_id,
                                                                  Decision.requires_human_approval))
                if d.status != "approved"]


def list_decisions(goal_id: int | None = None, status: str = "", limit: int = 200,
                   project_id: int | None = None) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Decision)
        if goal_id is not None:
            q = q.where(Decision.goal_id == goal_id)
        if project_id is not None:
            q = q.where(Decision.project_id == project_id)
        if status:
            q = q.where(Decision.status == status)
        return [_row(d) for d in s.scalars(q.order_by(Decision.id.desc()).limit(limit))]


def from_plan(goal_id: int | None, items: Any, agent_id: int | None, project_id: int | None = None
              ) -> list[dict[str, Any]]:
    """Decisions a planner put in its JSON plan (`"decisions": [...]`), or a topic summary found; malformed items
    are skipped."""
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("topic") or not it.get("recommendation"):
            continue
        try:
            conf = float(it.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        out.append(record(goal_id, it["topic"], it["recommendation"], it.get("alternatives"), it.get("reasoning", ""),
                          it.get("evidence"), it.get("assumptions"), conf, str(it.get("category", "technical")),
                          bool(it.get("requires_human_approval")), agent_id, project_id))
    return out
