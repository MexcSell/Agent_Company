"""AICO UI-APPR-01: one queue of what waits for a person, read from the tables that hold it.

Nothing here decides anything: each item names its kind, title, origin, age and the page that decides it (`link`),
and `actions` lists the decisions the list itself may send to an existing endpoint (`approve`, `reject`); the rest
open their page. Administrators see every kind; others see the goals of the projects they may see that their
approval right covers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from agent_company.db import (Agent, Decision, DefinitionCandidate, Experiment, Goal, Proposal, Recruitment,
                              Requirement, session)

OPEN_DECISION = ("submitted", "under_review", "escalated")
KINDS = ("goal", "plan", "questions", "org", "decision", "proposal", "definition", "recruitment", "experiment", "requirements", "user")


def _at(t: datetime | None) -> str | None:
    if t is None:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()


def queue(user: dict[str, Any], visible_projects: set[int] | None = None) -> list[dict[str, Any]]:
    """Newest first. `visible_projects` None = every project (administrators). A user who is not an administrator
    sees the goals awaiting approval that a delegation of `approve_goals` covers (S01-R-03, as `goal_approver`)."""
    from agent_company import auth, policies, projects

    admin = bool(user.get("is_admin"))
    out: list[dict[str, Any]] = []
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        pnames = dict(s.execute(select(projects.Project.id, projects.Project.name)).all())

        def item(kind: str, id_: int, title: str, at: datetime | None, link: str, project_id: int | None = None,
                 origin: str = "", actions: tuple[str, ...] = (), **extra: Any) -> None:
            if visible_projects is not None and project_id is not None and project_id not in visible_projects:
                return
            out.append({"kind": kind, "id": id_, "title": title, "project_id": project_id,
                        "project": pnames.get(project_id) if project_id else None, "origin": origin,
                        "at": _at(at), "link": link, "actions": list(actions)} | extra)

        for g in s.scalars(select(Goal).where(Goal.status == "awaiting_approval").order_by(Goal.id.desc())):
            if admin or policies.allows(user, "approve_goals", g.project_id):
                item("goal", g.id, g.title, g.updated_at, f"#/goals/{g.id}", g.project_id,
                     f"goal #{g.id} · {g.origin}" if g.origin else f"goal #{g.id}")
        for g in s.scalars(select(Goal).where(Goal.status == "paused").order_by(Goal.id.desc())):
            if (g.checkpoint or {}).get("paused_for") == "plan" and (
                    admin or policies.allows(user, "approve_goals", g.project_id)):
                item("plan", g.id, f"Plan: {g.title}", g.updated_at, f"#/general-manager/{g.project_id}"
                     if g.project_id else f"#/goals/{g.id}", g.project_id, f"goal #{g.id} · iteration {g.iteration}",
                     ("approve",))
            if (g.checkpoint or {}).get("paused_for") == "clarification" and (
                    admin or policies.allows(user, "approve_goals", g.project_id)):
                item("questions", g.id, f"Questions: {g.title}", g.updated_at, f"#/general-manager/{g.project_id}"
                     if g.project_id else f"#/goals/{g.id}", g.project_id,
                     f"goal #{g.id} · {len((g.checkpoint or {}).get('questions') or [])} question(s)")
        if not admin:
            return sorted(out, key=lambda x: x["at"] or "", reverse=True)

        from agent_company.db import OrgProposal

        for o in s.scalars(select(OrgProposal).where(OrgProposal.status == "pending")):  # UI-ORG-02
            item("org", o.id, f"Organisation: {o.title}", o.created_at, "#/organization", None, o.proposed_by)
        for d in s.scalars(select(Decision).where(Decision.requires_human_approval,
                                                  Decision.status.in_(OPEN_DECISION))):
            item("decision", d.id, d.topic, d.created_at,
                 f"#/goals/{d.goal_id}" if d.goal_id else (f"#/projects/{d.project_id}" if d.project_id else ""),
                 d.project_id, names.get(d.created_by_agent_id or 0, "") or (f"goal #{d.goal_id}" if d.goal_id else ""),
                 ("approve", "reject"), category=d.category)
        for p in s.scalars(select(Proposal).where(Proposal.status == "pending")):
            item("proposal", p.id, p.title, p.created_at, f"#/projects/{p.project_id}" if p.project_id else
                 f"#/goals/{p.goal_id}", p.project_id, names.get(p.agent_id or 0, ""),
                 # a proposal for a project or a rule needs its case read on its page; follow-ups decide here
                 ("approve", "reject") if p.kind == "follow_up" and not projects.restricted(p.title, p.description)
                 else ("reject",), proposal_kind=p.kind)
        for c in s.scalars(select(DefinitionCandidate).where(DefinitionCandidate.status == "pending")):
            item("definition", c.id, f"Definition candidate for {names.get(c.agent_id, f'agent #{c.agent_id}')}",
                 c.created_at, f"#/agents/{c.agent_id}", None, names.get(c.agent_id, ""), agent_id=c.agent_id)
        for r in s.scalars(select(Recruitment).where(Recruitment.status == "pending")):
            gp = s.get(Goal, r.goal_id).project_id if r.goal_id and s.get(Goal, r.goal_id) else None
            item("recruitment", r.id, f"Recruit a {r.requested_role.replace('_', ' ')}", r.created_at,
                 "#/evolution", gp, f"goal #{r.goal_id}" if r.goal_id else "")
        for e in s.scalars(select(Experiment).where(Experiment.status == "review")):
            item("experiment", e.id, f"Experiment #{e.id}: {e.target} candidate" + (f" for {names[e.agent_id]}"
                 if e.agent_id in names else ""), e.created_at, "#/evolution", None, e.origin or e.target)
        drafts = s.execute(select(Requirement.project_id, func.count(), func.max(Requirement.created_at))
                           .where(Requirement.status == "draft").group_by(Requirement.project_id)).all()
        for pid, n, at in drafts:
            item("requirements", pid, f"{n} draft requirement{'s' if n != 1 else ''}", at, f"#/projects/{pid}",
                 pid, "requirements", count=n)
        for u in s.scalars(select(auth.User).where(auth.User.status == "pending")):
            item("user", u.id, f"Account request: {u.name}", u.created_at, "#/users", None, "registration")
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {k: 0 for k in KINDS}
    for x in items:
        out[x["kind"]] = out.get(x["kind"], 0) + 1
    return out
