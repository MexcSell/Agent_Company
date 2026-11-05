"""Decision records (docs/requirements/SPEC-04-orchestration.md S04-R-02, escalation S04-R-07).

A decision names its topic, the alternatives considered, the recommendation, reasoning, evidence,
assumptions and an estimated confidence. Decisions that need a person (`requires_human_approval`, and
every `critical` one) block the goal's merge until an administrator approves them.

Escalation levels (S04-R-07, `escalation.py`): 1 assigned agent, 2 goal GM, 3 room manager, 4 executive GM of the
platform project, 5 human. The level follows the category and the impact, not only the attempt count: `critical`
goes straight to 5, technical and organizational decisions about the protected platform project to 4. Decisions of
level 3 and 4 that need no person are escalated to that level's agent, who reads them in its next prompt.

Decision reports (ASEAO07 S07-R-01, §13-§14, AT-007): besides the above a decision keeps its question, the criteria
the options were compared on, its limitations and why the confidence is what it is. Each evidence entry may carry a
SPEC-11 evidence class (`{"ref": ..., "class": "MEASURED"}`), so facts, measurements, inferences, hypotheses and
assumptions stay apart; `report` groups them and names the parts an important decision still lacks. A
recommendation is not authorisation to implement: only an approved decision is. Independent reviews (S07-FR-09) come
from anyone but the decision's author; a reviewer who disagrees with an automatically approved decision sends it to
a person.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from agent_company import escalation
from agent_company.db import Decision, Goal, Project, log_event, now, session

CATEGORIES = ["operational", "technical", "organizational", "financial", "critical"]
OPEN = ("draft", "submitted", "under_review", "escalated")
LEVEL_BY_CATEGORY = escalation.LEVEL_BY_CATEGORY
# SPEC-11 evidence classes (learning.py's knowledge items use the same ones)
EVIDENCE_CLASSES = ["FACT", "MEASURED", "INFERENCE", "HYPOTHESIS", "ASSUMPTION", "PREDICTION", "UNKNOWN"]
VERDICTS = ("agree", "concerns", "disagree")
_CLASS_BY_TYPE = {"hypothesis": "HYPOTHESIS", "eval": "MEASURED", "experiment": "MEASURED", "benchmark": "MEASURED"}


def _evidence(items: Any) -> list[Any]:
    """Stored evidence: strings and dicts are kept as they are (kgraph.evidence_ref reads them); a dict's class is
    normalised to a SPEC-11 class or dropped."""
    out: list[Any] = []
    for x in list(items or [])[:20]:
        if isinstance(x, dict):
            x = dict(x)
            c = str(x.pop("class", "") or x.pop("evidence_class", "")).upper()
            if c in EVIDENCE_CLASSES:
                x["class"] = c
            out.append(x)
        elif isinstance(x, str | int | float) and str(x).strip():
            out.append(str(x)[:1000])
    return out


def evidence_class(x: Any) -> str:
    """The class an evidence entry was given, or one its type implies (a hypothesis, an eval run), else UNKNOWN."""
    if isinstance(x, dict):
        return x.get("class") or _CLASS_BY_TYPE.get(str(x.get("type", "")).lower(), "UNKNOWN")
    return "UNKNOWN"


def _texts(items: Any, n: int = 20) -> list[str]:
    return [str(x)[:1000] for x in list(items or [])[:n] if str(x).strip()]


def important(d: Decision) -> bool:
    """Decisions the master criterion is about: ones a person approves, costly or critical ones, and ones escalated
    to a manager (levels 3-5)."""
    return bool(d.requires_human_approval or d.category in {"financial", "critical"} or (d.escalation_level or 0) >= 3)


def _person(actor: str) -> bool:
    return bool(actor) and actor not in {"auto", "system"} and not actor.startswith("agent:")


def missing(d: Decision) -> list[str]:
    """Parts of the decision report that are still empty. For an important decision a missing approver (it waits
    for a person) and a missing independent review count too."""
    out = [name for name, have in (("rationale", d.reasoning), ("alternatives", d.alternatives),
                                   ("criteria", d.criteria), ("evidence", d.evidence),
                                   ("limitations", d.limitations), ("confidence explanation", d.confidence_note))
           if not have]
    if important(d):
        if not _person(d.approver or ""):
            out.append("responsible approver")
        if not d.reviews and not _person(d.approver or ""):
            out.append("independent review")
    return out


def _row(d: Decision) -> dict[str, Any]:
    return {"id": d.id, "goal_id": d.goal_id, "project_id": d.project_id, "topic": d.topic,
            "question": d.question or d.topic, "alternatives": d.alternatives or [], "criteria": d.criteria or [],
            "recommendation": d.recommendation, "reasoning": d.reasoning,
            "evidence": d.evidence or [],  # as stored (kgraph and older readers parse these)
            "evidence_labelled": [x | {"class": evidence_class(x)} if isinstance(x, dict)
                                  else {"ref": x, "class": "UNKNOWN"} for x in d.evidence or []],
            "assumptions": d.assumptions or [], "limitations": d.limitations or [], "confidence": d.confidence,
            "confidence_note": d.confidence_note or "", "reviews": d.reviews or [], "adr_path": d.adr_path or "",
            "category": d.category, "requires_human_approval": d.requires_human_approval, "status": d.status,
            "authorised": d.status == "approved", "important": important(d), "missing": missing(d),
            "escalation_level": d.escalation_level, "created_by_agent_id": d.created_by_agent_id,
            "approver": d.approver, "note": d.note, "decided_at": d.decided_at.isoformat() if d.decided_at else None,
            "at": d.created_at.isoformat()}


def record(goal_id: int | None, topic: str, recommendation: str, alternatives: list[Any] | None = None,
           reasoning: str = "", evidence: list[Any] | None = None, assumptions: list[Any] | None = None,
           confidence: float = 0.5, category: str = "technical", requires_human_approval: bool = False,
           agent_id: int | None = None, project_id: int | None = None, *, question: str = "",
           criteria: list[Any] | None = None, limitations: list[Any] | None = None,
           confidence_note: str = "") -> dict[str, Any]:
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
                     reasoning=str(reasoning)[:4000], evidence=_evidence(evidence),
                     assumptions=list(assumptions or [])[:20], confidence=max(0.0, min(1.0, float(confidence or 0))),
                     question=str(question or "")[:2000], criteria=_texts(criteria, 10),
                     limitations=_texts(limitations), confidence_note=str(confidence_note or "")[:2000],
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
        out = _row(d)
    if out["adr_path"]:  # S08-R-02: the ADR's status follows the decision
        from agent_company import adr

        adr.on_decided(decision_id)
    return out


def review(decision_id: int, actor: str, verdict: str, note: str = "", agent_id: int | None = None
           ) -> dict[str, Any]:
    """An independent review of a decision (S07-FR-09). The author cannot review its own decision. A reviewer who
    disagrees with a decision that was approved automatically (or still waits) sends it to a person: it becomes
    `under_review` and blocks the goal's merge until an administrator decides."""
    from agent_company.governance import notify

    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if verdict != "agree" and not str(note).strip():
        raise ValueError("say what is wrong with the decision")
    with session() as s:
        d = s.get(Decision, decision_id)
        if d is None:
            raise KeyError(f"decision {decision_id}")
        if d.status in {"rejected", "expired", "cancelled"}:
            raise ValueError(f"decision is already {d.status}")
        if agent_id is not None and agent_id == d.created_by_agent_id:
            raise PermissionError("a decision's author cannot review it")
        if any(r.get("by") == actor for r in d.reviews or []):
            raise ValueError(f"{actor} has already reviewed this decision")
        d.reviews = [*(d.reviews or []), {"by": actor[:128], "agent_id": agent_id, "verdict": verdict,
                                          "note": str(note)[:2000], "at": now().isoformat()}]
        reopened = verdict == "disagree" and (d.status in OPEN or not _person(d.approver or ""))
        if reopened:
            d.status, d.requires_human_approval, d.approver, d.decided_at = "under_review", True, "", None
            notify(f"Decision disputed: {d.topic}"[:256], f"{actor}: {str(note)[:500]}", level="warning",
                   source="decision", goal_id=d.goal_id, project_id=d.project_id, admin_only=True, s=s)
        log_event("decision", f"decision #{d.id} reviewed by {actor}: {verdict}"
                  + (" (now waits for a person)" if reopened else ""), goal_id=d.goal_id, agent_id=agent_id, s=s,
                  decision_id=d.id, verdict=verdict)
        out = _row(d)
    if reopened and out["adr_path"]:  # S08-R-02: a disputed ADR is only proposed again
        from agent_company import adr

        adr.on_decided(decision_id)
    return out


def get(decision_id: int) -> dict[str, Any]:
    with session() as s:
        d = s.get(Decision, decision_id)
        if d is None:
            raise KeyError(f"decision {decision_id}")
        return _row(d)


def report(decision_id: int) -> dict[str, Any]:
    """The decision report (ASEAO07 §13.1): the record plus its evidence grouped by class and a Markdown text."""
    r = get(decision_id)
    groups = {c: [x for x in r["evidence_labelled"] if x["class"] == c] for c in EVIDENCE_CLASSES}
    groups["ASSUMPTION"] += [{"ref": a, "class": "ASSUMPTION"} for a in r["assumptions"]]
    r["evidence_by_class"] = {c: v for c, v in groups.items() if v}

    def ref(x: dict[str, Any]) -> str:
        rest = {k: v for k, v in x.items() if k != "class"}
        return str(rest.get("ref", "")) if list(rest) == ["ref"] else ", ".join(f"{k} {v}" for k, v in rest.items())

    def items(xs: list[Any]) -> list[str]:
        return [f"- {x if isinstance(x, str) else ref(x) if isinstance(x, dict) else x}" for x in xs] or ["- (none)"]

    lines = [f"# Decision #{r['id']}: {r['topic']}", "", f"**Question:** {r['question']}", "",
             f"**Recommendation:** {r['recommendation']}", "", "## Options", *items(r["alternatives"]), "",
             "## Criteria", *items(r["criteria"]), "", "## Rationale", r["reasoning"] or "(none)", ""]
    labels = {"FACT": "Facts", "MEASURED": "Measured", "INFERENCE": "Inferences", "HYPOTHESIS": "Hypotheses",
              "ASSUMPTION": "Assumptions", "PREDICTION": "Predictions", "UNKNOWN": "Unclassified evidence"}
    lines.append("## Evidence")
    for c, xs in r["evidence_by_class"].items():
        lines += [f"### {labels[c]}", *items(xs)]
    lines += [*([] if r["evidence_by_class"] else ["- (none)"]), "", "## Limitations", *items(r["limitations"]), "",
              f"**Confidence (an estimate):** {round(r['confidence'] * 100)}% — {r['confidence_note'] or 'not explained'}",
              "", "## Independent review",
              *([f"- {v['by']}: {v['verdict']}" + (f" — {v['note']}" if v["note"] else "") for v in r["reviews"]]
                or ["- (none)"]), "",
              f"**Category:** {r['category']} · **escalation level:** {r['escalation_level']}",
              f"**Status:** {r['status']}" + (f" by {r['approver']}" if r["approver"] else "")
              + ("" if r["authorised"] else " — not authorised to implement")]
    if r["missing"]:
        lines += ["", f"**Missing:** {', '.join(r['missing'])}"]
    r["markdown"] = "\n".join(lines)
    return r


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
        out.append(record(goal_id, it["topic"], it["recommendation"], _list(it.get("alternatives")),
                          it.get("reasoning", ""), _list(it.get("evidence")), _list(it.get("assumptions")), conf,
                          str(it.get("category", "technical")), bool(it.get("requires_human_approval")), agent_id,
                          project_id, question=str(it.get("question", "")), criteria=_list(it.get("criteria")),
                          limitations=_list(it.get("limitations")),
                          confidence_note=str(it.get("confidence_note", ""))))
    return out


def _list(x: Any) -> list[Any]:
    """A model may give one string where a list is asked for."""
    return x if isinstance(x, list) else [x] if isinstance(x, str | dict) and x else []
