"""AICO UI-SRCH-01: one search over the company for the top bar (Ctrl+K).

Results are grouped by kind and limited to what the user may see: projects, goals, tasks and research reports of
visible projects; agents; knowledge items (administrators, the knowledge FTS search); files by name in the visible
projects' repositories (`git ls-files`, from 3 characters). Each result carries the route that opens it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import or_, select

from agent_company.db import Agent, Goal, ResearchReport, Task, session

LIMIT = 8


def search(user: dict[str, Any], q: str, visible_projects: set[int] | None = None) -> dict[str, Any]:
    from agent_company import learning, projects, worktrees

    q = (q or "").strip()[:200]
    if not q:
        return {"q": q, "groups": []}
    like = f"%{q}%"
    seen = (lambda pid: True) if visible_projects is None else (lambda pid: pid is None or pid in visible_projects)
    groups: list[dict[str, Any]] = []

    def add(kind: str, title: str, items: list[dict[str, Any]]) -> None:
        if items:
            groups.append({"kind": kind, "title": title, "items": items[:LIMIT]})

    with session() as s:
        plist = [p for p in s.scalars(projects.visible(user).where(or_(
            projects.Project.name.ilike(like), projects.Project.description.ilike(like),
            projects.Project.github_url.ilike(like))).limit(LIMIT))]
        add("project", "Projects", [{"id": p.id, "label": p.name, "detail": p.status or "active",
                                     "href": f"#/projects/{p.id}"} for p in plist])
        gq = select(Goal).where(Goal.origin != "eval", or_(Goal.title.ilike(like), Goal.description.ilike(like)))
        if q.lstrip("#").isdigit():
            gq = select(Goal).where(or_(Goal.id == int(q.lstrip("#")), Goal.title.ilike(like)))
        goals = [g for g in s.scalars(gq.order_by(Goal.id.desc()).limit(40)) if seen(g.project_id)]
        add("goal", "Goals", [{"id": g.id, "label": f"#{g.id} {g.title}", "detail": g.status,
                               "href": f"#/goals/{g.id}"} for g in goals])
        tasks = [(t, g) for t, g in s.execute(select(Task, Goal).join(Goal, Goal.id == Task.goal_id)
                                               .where(Task.title.ilike(like), Goal.origin != "eval")
                                               .order_by(Task.id.desc()).limit(40)) if seen(g.project_id)]
        add("task", "Tasks", [{"id": t.id, "label": t.title, "detail": f"goal #{g.id} · {t.role} · {t.status}",
                               "href": f"#/goals/{g.id}"} for t, g in tasks])
        agents = s.scalars(select(Agent).where(Agent.lifecycle != "retired", or_(
            Agent.name.ilike(like), Agent.role.ilike(like), Agent.title.ilike(like))).limit(LIMIT)).all()
        add("agent", "Agents", [{"id": a.id, "label": a.name, "detail": a.role.replace("_", " "),
                                 "href": f"#/agents/{a.id}"} for a in agents])
        reports = [r for r in s.scalars(select(ResearchReport).where(or_(
            ResearchReport.question.ilike(like), ResearchReport.report.ilike(like)))
            .order_by(ResearchReport.id.desc()).limit(40)) if seen(r.project_id)]
        add("report", "Research reports", [{"id": r.id, "label": r.question[:120], "detail": r.status,
                                             "href": "#/research"} for r in reports])
        paths = [(p.id, p.name, p.path) for p in s.scalars(projects.visible(user)) if p.path]
    if user.get("is_admin"):
        try:
            items = learning.knowledge("", None, LIMIT, q)
        except Exception:  # noqa: BLE001 - the FTS index may be rebuilding; the rest still answers
            items = []
        add("knowledge", "Knowledge", [{"id": i["id"], "label": str(i["text"])[:120], "detail": i["status"],
                                        "href": "#/knowledge"} for i in items])
    if len(q) >= 3:
        files: list[dict[str, Any]] = []
        low = q.lower()
        for pid, name, path in paths[:20]:
            if not (Path(path) / ".git").exists():
                continue
            try:
                listed = worktrees.git(Path(path), "ls-files", check=False).splitlines()
            except Exception:  # noqa: BLE001 - an unreadable repository is skipped
                continue
            for f in listed:
                if low in f.rsplit("/", 1)[-1].lower():
                    files.append({"id": f"{pid}:{f}", "label": f, "detail": name, "path": str(Path(path) / f),
                                  "href": f"#/projects/{pid}"})
                    if len(files) >= LIMIT:
                        break
            if len(files) >= LIMIT:
                break
        add("file", "Files", files)
    return {"q": q, "groups": groups}
