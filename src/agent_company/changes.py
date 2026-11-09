"""AICO UI-HIST-01: one timeline of changes to the company, read from the tables that already keep them.

Kinds: agent configuration versions (`agent_versions`), playbook versions, applied definition rewrites, organisation
versions, governance setting versions (`policy_versions`), route changes (audit records), merges (release records)
and self-evolution deploys and rollbacks (improvement contracts). Each entry names who, when, what, and the page
that shows its diff and its restore action where one exists. Nothing is written here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from agent_company.db import (Agent, AgentVersion, AuditEvent, DefinitionCandidate, Deliverable, ImprovementContract,
                              OrgVersion, PlaybookVersion, PolicyVersion, now, session)

KINDS = ("agent_config", "playbook", "definition", "organization", "governance", "route", "merge", "self_evolution")
LABELS = {"agent_config": "Agent configuration", "playbook": "Playbook", "definition": "Agent definition",
          "organization": "Organization", "governance": "Governance", "route": "Model route", "merge": "Merge",
          "self_evolution": "Self-evolution"}
ROUTE_ACTIONS = ("route_set", "routes_auto", "route_fallback")


def _iso(t: datetime | None) -> str:
    return "" if t is None else (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()


def timeline(kind: str = "", agent_id: int | None = None, project_id: int | None = None, days: int = 30,
             offset: int = 0, limit: int = 50) -> dict[str, Any]:
    since = now() - timedelta(days=max(1, min(days, 3650)))
    out: list[dict[str, Any]] = []
    want = (lambda k: True) if not kind else (lambda k: k == kind)
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())

        def add(k: str, at: datetime | None, title: str, actor: str = "", link: str = "", restore: str = "",
                agent: int | None = None, project: int | None = None) -> None:
            if (agent_id is not None and agent != agent_id) or (project_id is not None and project != project_id):
                return
            out.append({"kind": k, "label": LABELS[k], "at": _iso(at), "title": title, "actor": actor or "",
                        "agent_id": agent, "agent": names.get(agent or 0), "project_id": project, "link": link,
                        "restore": restore})

        if want("agent_config"):
            for v in s.scalars(select(AgentVersion).where(AgentVersion.created_at >= since)):
                add("agent_config", v.created_at, f"{names.get(v.agent_id, f'agent #{v.agent_id}')} configuration "
                    f"v{v.version}: {v.reason}", v.changed_by, f"#/agents/{v.agent_id}",
                    "Agent card → Versions → Roll back", v.agent_id)
        if want("playbook"):
            for v in s.scalars(select(PlaybookVersion).where(PlaybookVersion.created_at >= since)):
                add("playbook", v.created_at, f"{names.get(v.agent_id, f'agent #{v.agent_id}')} playbook "
                    f"v{v.version}: {v.reason}", "", f"#/agents/{v.agent_id}", "Agent card → Evolution", v.agent_id)
        if want("definition"):
            for c in s.scalars(select(DefinitionCandidate).where(DefinitionCandidate.status == "applied",
                                                                 DefinitionCandidate.decided_at >= since)):
                add("definition", c.decided_at, f"{names.get(c.agent_id, f'agent #{c.agent_id}')} definition "
                    f"rewrite #{c.id} applied: {c.reason}", c.decided_by or "", f"#/agents/{c.agent_id}",
                    "Agent card → Agent code (history)", c.agent_id)
        if want("organization"):
            for v in s.scalars(select(OrgVersion).where(OrgVersion.created_at >= since)):
                add("organization", v.created_at, f"Organization v{v.version}: {v.reason}", v.actor, "#/organization",
                    "Organization Evolution → Restore")
        if want("governance"):
            for v in s.scalars(select(PolicyVersion).where(PolicyVersion.created_at >= since)):
                add("governance", v.created_at, f"{v.scope} v{v.version}: {v.note}", v.author, "#/settings",
                    "Settings → Policy versions → Restore",
                    project=int(v.scope.split(":")[1]) if v.scope.startswith("project:") and
                    v.scope.split(":")[1].isdigit() else None)
        if want("route"):
            for a in s.scalars(select(AuditEvent).where(AuditEvent.action.in_(ROUTE_ACTIONS),
                                                        AuditEvent.created_at >= since)):
                after = a.after if isinstance(a.after, dict) else {}
                what = (f"{after.get('model', '')}" + (f", fallback {', '.join(map(str, after['fallback']))}"
                                                       if isinstance(after.get("fallback"), list) else "")
                        if a.action == "route_set" else "models assigned to every role")
                add("route", a.created_at, f"Route {a.target}: {what}"[:300], a.actor, "#/models", "Models → Routes")
        if want("merge"):
            for d in s.scalars(select(Deliverable).where(Deliverable.created_at >= since)):
                add("merge", d.created_at, f"Goal #{d.goal_id} merged ({d.merge_commit[:10]}): {d.title}",
                    d.approved_by, "#/deliverables", "", project=d.project_id)
        if want("self_evolution"):
            for c in s.scalars(select(ImprovementContract).where(ImprovementContract.updated_at >= since,
                                                                 ImprovementContract.status.in_(
                                                                     ("monitoring", "stable", "rolled_back")))):
                add("self_evolution", c.updated_at, f"Improvement #{c.id} {c.status.replace('_', ' ')}: {c.title}",
                    c.created_by, "#/system", "System → Code evolution → Roll back", project=c.project_id)
    out.sort(key=lambda x: x["at"], reverse=True)
    return {"total": len(out), "offset": offset, "items": out[offset:offset + max(1, min(limit, 200))],
            "kinds": [{"kind": k, "label": LABELS[k]} for k in KINDS]}
