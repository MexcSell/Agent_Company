"""AICO UI-DEL-01/03: one release record per merged goal, stored at the merge.

The record keeps what was delivered at that moment: project, goal, merge commit, branches, the repository's remote,
the SBOM path if one was written, the changed files, the acceptance labels (from the gate and reviews, never the
agents' text, UI-D-08), who approved, and the completion report as Markdown with its SHA-256. The loop sends the
completion notification only after the row is written ("do not report completion if storage fails"). A project's
newest record is `ready`; earlier ones become `superseded`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select

from agent_company.db import Deliverable, Goal, session


def _iso(t: Any) -> str | None:
    return t.isoformat() if t else None


def completion_report(goal_id: int, sha: str, approved_by: str) -> tuple[str, dict[str, int], list[str]]:
    """UI-DEL-03 sections from the mission report, the requirements the goal traces to, its ADRs, its team and the
    merge. Returns (markdown, acceptance labels, changed files)."""
    from agent_company import decisions, mission, teams
    from agent_company.db import Project, Requirement, RequirementLink

    d = mission.report(goal_id)
    with session() as s:
        g = s.get(Goal, goal_id)
        p = s.get(Project, g.project_id) if g.project_id else None
        last = next((h for h in reversed(g.history or []) if h.get("passed")), (g.history or [{}])[-1] if g.history else {})
        files = list(last.get("changed_files") or [])
        links = s.execute(select(Requirement.key, Requirement.text, RequirementLink.kind, RequirementLink.passed)
                          .join(RequirementLink, RequirementLink.requirement_id == Requirement.id)
                          .where(RequirementLink.goal_id == goal_id)).all()
        remote, branch, base, project = (p.github_url if p else ""), g.branch, g.base_branch, (p.name if p else "")
    reqs: dict[str, dict[str, Any]] = {}
    for key, text, kind, passed in links:
        r = reqs.setdefault(key, {"text": text, "kinds": set(), "passed": True})
        r["kinds"].add(kind)
        r["passed"] = r["passed"] and bool(passed)
    adrs = [x for x in decisions.list_decisions(goal_id=goal_id) if x.get("adr_path")]
    members = {(m["agent"] or m["role"]): m for m in teams.history(goal_id)}
    labels = d["tasks"]["labels"]
    out = [f"# Completion report: {d['title']}", "",
           f"Project: {project or '-'} · goal #{goal_id} · merged {sha[:10]} into {base or '-'} · approved by {approved_by}",
           "", "## Overview", "", d["objective"], "",
           f"Tasks verified {labels['verified']}, partial {labels['partial']}, failed {labels['failed']}.", "",
           "## Requirements and fulfilment", ""]
    out += [f"- {k}: {r['text'][:200]} — {', '.join(sorted(r['kinds']))}, {'met' if r['passed'] else 'not all checks passed'}"
            for k, r in sorted(reqs.items())] or ["- no requirement linked"]
    out += ["", "## Architecture (decisions and ADRs)", ""]
    out += [f"- {x['topic']} ({x['status']}): {x['adr_path']}" for x in adrs] or ["- none recorded"]
    out += ["", "## Team", ""]
    out += [f"- {name} ({m['role']}): {m['action']}" for name, m in members.items()] or ["- none recorded"]
    out += ["", "## Deployment notes", "", f"- merge commit {sha}", f"- branch {branch or '-'} → {base or '-'}",
            f"- remote {remote or 'none (pushed on /sync or AUTO_PUSH)'}", "", "## Artifacts", ""]
    out += [f"- {f}" for f in files[:200]] or ["- no changed file recorded"]
    out += ["", "---", "", mission.report_markdown(d)]
    return "\n".join(out), {k: int(v) for k, v in labels.items()}, files


def record(goal_id: int, sha: str, approved_by: str) -> dict[str, Any]:
    """Store the release record of a merged goal; a second call for the same goal stores nothing and returns the
    first. Raises if the report cannot be built or stored, so the caller does not report completion."""
    from agent_company import dependencies

    with session() as s:
        old = s.scalars(select(Deliverable).where(Deliverable.goal_id == goal_id)).first()
        if old is not None:
            return deliverable_dict(old)
    md, labels, files = completion_report(goal_id, sha, approved_by)
    sbom = dependencies.sbom_path(sha)
    with session() as s:
        g = s.get(Goal, goal_id)
        from agent_company.db import Project

        p = s.get(Project, g.project_id) if g.project_id else None
        if g.project_id is not None:
            for d in s.scalars(select(Deliverable).where(Deliverable.project_id == g.project_id,
                                                         Deliverable.status == "ready")):
                d.status = "superseded"
        row = Deliverable(project_id=g.project_id, goal_id=goal_id, title=g.title, merge_commit=sha,
                          branch=g.branch or "", base_branch=g.base_branch or "", remote_url=(p.github_url if p else ""),
                          sbom_path=str(sbom) if sbom.is_file() else "", report_md=md,
                          checksum=hashlib.sha256(md.encode()).hexdigest(), acceptance=labels,
                          changed_files=files[:500], approved_by=approved_by, status="ready")
        s.add(row)
        s.flush()
        return deliverable_dict(row)


def deliverable_dict(d: Deliverable, report: bool = False) -> dict[str, Any]:
    out = {"id": d.id, "project_id": d.project_id, "goal_id": d.goal_id, "title": d.title,
           "merge_commit": d.merge_commit, "branch": d.branch, "base_branch": d.base_branch, "tag": d.tag,
           "remote_url": d.remote_url, "sbom_path": d.sbom_path, "checksum": d.checksum,
           "acceptance": d.acceptance or {}, "changed_files": d.changed_files or [], "approved_by": d.approved_by,
           "status": d.status, "created_at": _iso(d.created_at)}
    if report:
        out["report_md"] = d.report_md
    return out


def list_deliverables(project_ids: set[int] | None = None, project_id: int | None = None) -> list[dict[str, Any]]:
    """Newest first; `project_ids` None = every project (administrators), else only those (records without a
    project are administrators' only)."""
    with session() as s:
        q = select(Deliverable).order_by(Deliverable.id.desc())
        if project_id is not None:
            q = q.where(Deliverable.project_id == project_id)
        rows = s.scalars(q.limit(500)).all()
        return [deliverable_dict(d) for d in rows
                if project_ids is None or (d.project_id is not None and d.project_id in project_ids)]


def get(deliverable_id: int) -> dict[str, Any]:
    with session() as s:
        d = s.get(Deliverable, deliverable_id)
        if d is None:
            raise KeyError(f"deliverable {deliverable_id}")
        return deliverable_dict(d, report=True)
