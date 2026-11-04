"""Projects, the command window and agent proposals.

A project is a git repository the company works on, with an optional GitHub URL,
default quality commands and a General Manager. Its command window is a
conversation: an order becomes a goal for that project's GM, who plans it and
reports back in the same thread (plan, task results, gate result). Slash
commands: /standup, /stop, /status, /sync, /help.

The platform project (this repository) is registered as protected: it cannot be
deleted and only administrators see it, so agents can develop the platform itself.

Agents may end a task with `PROPOSAL: <title> :: <why>` lines. Proposals wait for an
administrator, or the GM decides them (`proposal_policy = superiors`). An approved
proposal becomes a new goal; one goal may spawn at most `max_proposal_goals`.
"""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from agent_company import worktrees
from agent_company.config import REPO_ROOT, settings
from agent_company.db import Agent, Goal, Message, Project, Proposal, Task, Upload, log_event, session

NAME_RE = re.compile(r"^[\w][\w .-]{0,127}$")
PLATFORM = "Agent Company"
MAX_UPLOAD = 10 * 1024 * 1024
UPLOAD_TYPES = ("image/", "text/", "application/json", "application/pdf", "application/x-yaml")


def project_dict(p: Project, counts: dict[str, int] | None = None) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "path": p.path, "github_url": p.github_url, "description": p.description,
            "quality_commands": p.quality_commands, "protected": p.protected, "manager_id": p.manager_id,
            "created_at": p.created_at.isoformat(), **(counts or {})}


def seed() -> None:
    """Register this repository as the protected platform project."""
    with session() as s:
        if s.scalars(select(Project).where(Project.protected)).first() is None:
            gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
            s.add(Project(name=PLATFORM, path=str(REPO_ROOT), protected=True, manager_id=gm.id if gm else None,
                          description="Develop the Agent Company platform itself. Approved changes are merged "
                                      "into this repository; restart the service to load them.",
                          quality_commands=["uv run pytest -q"]))


def visible(user: dict[str, Any]):  # noqa: ANN201
    q = select(Project)
    if user["is_admin"]:
        return q
    from sqlalchemy import or_

    q = q.where(Project.protected.is_(False))
    allowed = user.get("permissions", {}).get("projects", "own")
    if allowed == "all":
        return q
    granted = list(allowed) if isinstance(allowed, list) else []
    return q.where(or_(Project.created_by == user["id"], Project.id.in_(granted or [-1])))


def list_projects(user: dict[str, Any]) -> list[dict[str, Any]]:
    with session() as s:
        out = []
        for p in s.scalars(visible(user).order_by(Project.protected.desc(), Project.id)):
            running = s.scalar(select(func.count()).select_from(Goal).where(
                Goal.project_id == p.id, Goal.status.in_(["queued", "running"]))) or 0
            agents = s.scalar(select(func.count(func.distinct(Task.agent_id))).join(Goal, Goal.id == Task.goal_id)
                              .where(Goal.project_id == p.id, Task.status == "running")) or 0
            out.append(project_dict(p, {"running_goals": running, "running_agents": agents}))
        return out


def get(project_id: int, user: dict[str, Any]) -> Project:
    with session() as s:
        p = s.scalars(visible(user).where(Project.id == project_id)).first()
        if p is None:
            raise KeyError(project_id)
        s.expunge(p)
        return p


def default_base() -> Path:
    """Where new projects go by default: the folder that holds this repository (one level above it)."""
    return REPO_ROOT.resolve().parent


def _check(name: str, path: str, github_url: str, quality_commands: list[str]) -> str:
    from agent_company.governance import check_command

    if not NAME_RE.match(name or ""):
        raise ValueError("project names are 1-128 letters, digits, spaces, dots, dashes or underscores")
    if github_url and not re.match(r"^(https://|git@)[\w.@:/~-]+$", github_url):
        raise ValueError("GitHub URL must start with https:// or git@")
    for cmd in quality_commands:
        if check_command(cmd):
            raise ValueError(f"quality command refused: {cmd}")
    if path:
        p = Path(path).expanduser().resolve()
        if not p.exists() and p.parent.is_dir() and p.parent == default_base():
            p.mkdir()  # a new project folder next to this repository (the default location)
        if not p.is_dir():
            raise ValueError(f"{p} is not a directory")
        if not (p / ".git").exists():
            # Never `git add -A` a folder someone named: it may hold .env files, keys or a whole home
            # directory. Commit only a source-only .gitignore; agents add real files through goals.
            from agent_company.gitsync import ensure_gitignore

            worktrees.git(p, "init", "-q", "-b", "main")
            ensure_gitignore(p)
            worktrees.git(p, "add", "--", ".gitignore")
            worktrees.git(p, "commit", "-q", "--allow-empty", "-m", "Initial commit by Agent Company")
        return str(p)
    return ""


def create(name: str, path: str = "", github_url: str = "", description: str = "",
           quality_commands: list[str] | None = None, created_by: int | None = None) -> dict[str, Any]:
    path = _check(name, path, github_url, quality_commands or [])
    with session() as s:
        if s.scalars(select(Project).where(func.lower(Project.name) == name.lower())).first():
            raise ValueError("a project with that name exists")
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
        p = Project(name=name.strip(), path=path, github_url=github_url, description=description,
                    quality_commands=list(quality_commands or []), created_by=created_by,
                    manager_id=gm.id if gm else None)
        s.add(p)
        s.flush()
        if not p.path:
            p.path = str(worktrees.new_workspace(f"project-{p.id}"))
        s.add(Message(project_id=p.id, author_type="system", kind="chat",
                      content=f"Project created. Give the General Manager an order below; it works in {p.path}."))
        return project_dict(p)


def update(project_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "github_url", "description", "quality_commands"}
    bad = set(changes) - allowed
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        if p.protected and "name" in changes:
            raise ValueError("the platform project cannot be renamed")
        _check(changes.get("name", p.name), "", changes.get("github_url", p.github_url),
               changes.get("quality_commands", p.quality_commands))
        for k, v in changes.items():
            setattr(p, k, v.strip() if isinstance(v, str) else v)
        s.flush()
        return project_dict(p)


def delete(project_ids: list[int]) -> list[int]:
    """Delete projects (their goals stay, detached). The protected project is refused."""
    done = []
    with session() as s:
        for pid in project_ids:
            p = s.get(Project, pid)
            if p is None:
                continue
            if p.protected:
                raise ValueError("the platform project cannot be deleted")
            for g in s.scalars(select(Goal).where(Goal.project_id == pid, Goal.status.in_(["queued", "running"]))):
                g.status = "cancelled"
            s.query(Message).filter(Message.project_id == pid).delete()
            s.delete(p)
            done.append(pid)
    return done


# --- messages ---------------------------------------------------------------------

def post(project_id: int, content: str, author_type: str = "system", kind: str = "chat", goal_id: int | None = None,
         agent_id: int | None = None, user_id: int | None = None, attachments: list[Any] | None = None,
         **meta: Any) -> int:
    with session() as s:
        m = Message(project_id=project_id, content=content[:20000], author_type=author_type, kind=kind,
                    goal_id=goal_id, agent_id=agent_id, user_id=user_id, attachments=attachments or [], meta=meta)
        s.add(m)
        s.flush()
        return m.id


def messages(project_id: int, after: int = 0, limit: int = 300) -> list[dict[str, Any]]:
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        rows = s.scalars(select(Message).where(Message.project_id == project_id, Message.id > after)
                         .order_by(Message.id.desc()).limit(limit)).all()
        return [{"id": m.id, "author_type": m.author_type, "agent": names.get(m.agent_id), "agent_id": m.agent_id,
                 "user_id": m.user_id, "kind": m.kind, "content": m.content, "goal_id": m.goal_id,
                 "attachments": m.attachments, "meta": m.meta, "at": m.created_at.isoformat()}
                for m in reversed(rows)]


def post_for_goal(goal_id: int, content: str, kind: str = "chat", agent_id: int | None = None, **meta: Any) -> None:
    with session() as s:
        g = s.get(Goal, goal_id)
        pid = g.project_id if g else None
    if pid:
        post(pid, content, "agent" if agent_id else "system", kind, goal_id=goal_id, agent_id=agent_id, **meta)


def members(project_id: int) -> list[dict[str, Any]]:
    """Agents that worked on the project's goals, busy ones first."""
    from agent_company.registry import agent_dict

    with session() as s:
        ids = set(s.scalars(select(Task.agent_id).join(Goal, Goal.id == Task.goal_id)
                            .where(Goal.project_id == project_id, Task.agent_id.is_not(None))))
        p = s.get(Project, project_id)
        if p and p.manager_id:
            ids.add(p.manager_id)
        running = dict(s.execute(select(Task.agent_id, Task.title).join(Goal, Goal.id == Task.goal_id)
                                 .where(Goal.project_id == project_id, Task.status == "running")).all())
        agents = s.scalars(select(Agent).where(Agent.id.in_(ids))).all()
        out = [agent_dict(a) | {"current_task": running.get(a.id)} for a in agents]
        return sorted(out, key=lambda a: (a["current_task"] is None, a["status"] != "busy", a["id"]))


# --- uploads ----------------------------------------------------------------------

def save_upload(name: str, data: bytes, content_type: str, user_id: int | None) -> dict[str, Any]:
    if len(data) > MAX_UPLOAD:
        raise ValueError("files may be at most 10 MB")
    ctype = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not ctype.startswith(UPLOAD_TYPES):
        raise ValueError(f"{ctype} files are not accepted (images, text, JSON, YAML and PDF are)")
    uid = uuid.uuid4().hex
    safe = re.sub(r"[^\w.-]+", "_", Path(name).name)[:100] or "file"
    folder = settings().data_dir / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uid}-{safe}"
    path.write_bytes(data)
    with session() as s:
        s.add(Upload(id=uid, name=safe, path=str(path), content_type=ctype, size=len(data), user_id=user_id))
    return {"id": uid, "name": safe, "content_type": ctype, "size": len(data)}


def uploads(ids: list[str]) -> list[dict[str, Any]]:
    with session() as s:
        return [{"id": u.id, "name": u.name, "path": u.path, "content_type": u.content_type, "size": u.size}
                for u in s.scalars(select(Upload).where(Upload.id.in_(ids)))]


def upload_path(uid: str) -> tuple[Path, str]:
    with session() as s:
        u = s.get(Upload, uid)
        if u is None:
            raise KeyError(uid)
        return Path(u.path), u.content_type


# --- commands ---------------------------------------------------------------------

HELP = ("Commands: an order in plain words starts a goal for the General Manager.\n"
        "/standup  every agent on this project reports its status\n"
        "/status   the project's goals and their state\n"
        "/stop     cancel the project's running and queued goals\n"
        "/sync     push approved work to the project's GitHub remote\n"
        "/help     this list")


def command(project_id: int, user: dict[str, Any], content: str, attachment_ids: list[str] | None = None
            ) -> dict[str, Any]:
    """Handle one command-window entry. Returns {"message_id", "goal_id"?}."""
    from agent_company import loop
    from agent_company.auth import can

    p = get(project_id, user)
    if not can(user, "run"):
        raise PermissionError("you can read this project but not give it orders")
    content = (content or "").strip()
    files = uploads(attachment_ids or [])
    if not content and not files:
        raise ValueError("empty command")
    mid = post(p.id, content, "user", "command", user_id=user["id"],
               attachments=[{k: f[k] for k in ("id", "name", "content_type", "size")} for f in files],
               author=user["name"])
    slash = content.split()[0].lower() if content.startswith("/") else ""
    if slash == "/help":
        post(p.id, HELP)
        return {"message_id": mid}
    if slash == "/status":
        post(p.id, status_text(p.id), kind="result")
        return {"message_id": mid}
    if slash == "/stop":
        with session() as s:
            ids = [g.id for g in s.scalars(select(Goal).where(Goal.project_id == p.id,
                                                              Goal.status.in_(["queued", "running"])))]
        for gid in ids:
            loop.cancel(gid)
        post(p.id, f"Stopped {len(ids)} goal(s)." if ids else "Nothing is running.", kind="result")
        return {"message_id": mid}
    if slash == "/standup":
        post(p.id, standup(p.id), "agent", kind="standup", agent_id=p.manager_id)
        return {"message_id": mid}
    if slash == "/sync":
        from agent_company import gitsync

        post(p.id, gitsync.sync_project(p.id, user), kind="result")
        return {"message_id": mid}
    if slash:
        post(p.id, f"Unknown command {slash}.\n\n{HELP}", kind="error")
        return {"message_id": mid}
    title = content.splitlines()[0][:200] if content else f"Look at {files[0]['name']}"
    if len(title) < 3:
        title = f"Order: {title}"
    gid = loop.create_goal(title, content or title, repo_path=p.path, quality_commands=list(p.quality_commands),
                           created_by=user["id"], project_id=p.id, attachments=files,
                           extract_checks=user["is_admin"])
    post(p.id, f"Order received as goal #{gid}. Planning now.", "agent", goal_id=gid, agent_id=p.manager_id)
    loop.enqueue(gid)
    return {"message_id": mid, "goal_id": gid}


def status_text(project_id: int) -> str:
    with session() as s:
        goals = s.scalars(select(Goal).where(Goal.project_id == project_id).order_by(Goal.id.desc()).limit(10)).all()
        if not goals:
            return "No goals yet."
        return "\n".join(f"#{g.id} [{g.status}] {g.title} (iteration {g.iteration}/{g.max_iterations})"
                         for g in goals)


def standup(project_id: int) -> str:
    """Each agent on the project reports: what it is doing, what it finished, what failed."""
    lines = ["Stand-up"]
    with session() as s:
        for a in members(project_id):
            last = s.scalars(select(Task).join(Goal, Goal.id == Task.goal_id).where(
                Goal.project_id == project_id, Task.agent_id == a["id"]).order_by(Task.id.desc())).first()
            doing = f"working on '{a['current_task']}'" if a["current_task"] else "idle"
            done = f"last: '{last.title}' ({last.status})" if last else "no tasks here yet"
            lines.append(f"- {a['name']} ({a['role']}, L{a['level']}): {doing}; {done}")
        pending = s.scalar(select(func.count()).select_from(Proposal).where(
            Proposal.project_id == project_id, Proposal.status == "pending")) or 0
        waiting = s.scalar(select(func.count()).select_from(Goal).where(
            Goal.project_id == project_id, Goal.status == "awaiting_approval")) or 0
    if pending or waiting:
        lines.append(f"Waiting on you: {waiting} goal(s) to approve, {pending} proposal(s) to decide.")
    return "\n".join(lines)


# --- proposals --------------------------------------------------------------------

PROPOSAL_RE = re.compile(r"^\s*PROPOSAL:\s*(.+?)(?:\s*::\s*(.+))?\s*$", re.M)
PROPOSALS_PER_TASK = 2


def extract_proposals(goal_id: int, agent_id: int, output: str) -> int:
    found = PROPOSAL_RE.findall(output or "")[:PROPOSALS_PER_TASK]
    if not found:
        return 0
    with session() as s:
        g = s.get(Goal, goal_id)
        for title, why in found:
            s.add(Proposal(goal_id=goal_id, project_id=g.project_id if g else None, agent_id=agent_id,
                           title=title.strip()[:256], description=(why or "").strip()))
    return len(found)


def proposal_dict(p: Proposal, names: dict[int, str]) -> dict[str, Any]:
    return {"id": p.id, "goal_id": p.goal_id, "project_id": p.project_id, "agent": names.get(p.agent_id),
            "title": p.title, "description": p.description, "status": p.status, "decided_by": p.decided_by,
            "spawned_goal_id": p.spawned_goal_id, "at": p.created_at.isoformat()}


def list_proposals(user: dict[str, Any], status: str = "") -> list[dict[str, Any]]:
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        seen = None if user["is_admin"] else set(s.scalars(visible(user).with_only_columns(Project.id)))
        q = select(Proposal)
        if status:
            q = q.where(Proposal.status == status)
        rows = s.scalars(q.order_by(Proposal.id.desc()).limit(200)).all()
        return [proposal_dict(p, names) for p in rows if seen is None or p.project_id in seen]


def _root(s, goal: Goal) -> Goal:  # noqa: ANN001
    seen = set()
    while goal.parent_goal_id and goal.id not in seen:
        seen.add(goal.id)
        parent = s.get(Goal, goal.parent_goal_id)
        if parent is None:
            break
        goal = parent
    return goal


def _spawned_from(s, root_id: int) -> int:  # noqa: ANN001
    count, frontier = 0, [root_id]
    while frontier:
        kids = list(s.scalars(select(Goal.id).where(Goal.parent_goal_id.in_(frontier), Goal.origin == "proposal")))
        count += len(kids)
        frontier = kids
    return count


def decide(proposal_id: int, approve: bool, decided_by: str) -> dict[str, Any]:
    """Approve (spawning a goal, within the loop guard) or reject a proposal."""
    from agent_company import loop

    spawn: dict[str, Any] | None = None
    with session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if p.status != "pending":
            raise ValueError(f"proposal is already {p.status}")
        origin = s.get(Goal, p.goal_id)
        note = ""
        limit = settings().max_proposal_goals
        if approve and origin is not None and _spawned_from(s, _root(s, origin).id) >= limit:
            approve, note = False, f" (limit of {limit} proposal goals reached)"
        p.status, p.decided_by = ("approved" if approve else "rejected"), decided_by + note
        if approve:
            spawn = {"title": p.title, "description": p.description or p.title,
                     "repo": origin.repo_path if origin else "",
                     "checks": list(origin.quality_commands) if origin else [], "project_id": p.project_id,
                     "parent": origin.id if origin else None, "created_by": origin.created_by if origin else None}
        out = proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))
    if spawn is not None:
        gid = loop.create_goal(spawn["title"], spawn["description"], spawn["repo"], quality_commands=spawn["checks"],
                               created_by=spawn["created_by"], project_id=spawn["project_id"], origin="proposal",
                               parent_goal_id=spawn["parent"])
        with session() as s:
            s.get(Proposal, proposal_id).spawned_goal_id = gid
        out["spawned_goal_id"] = gid
        if spawn["project_id"]:
            post(spawn["project_id"], f"Proposal approved by {decided_by}: '{out['title']}' is now goal #{gid}.",
                 kind="proposal", goal_id=gid)
        loop.enqueue(gid)
    elif out["project_id"]:
        post(out["project_id"], f"Proposal '{out['title']}' rejected by {out['decided_by']}.", kind="proposal")
    log_event("proposal", f"proposal {proposal_id} {out['status']} by {out['decided_by']}", goal_id=out["goal_id"])
    return out


def settle_proposals(goal_id: int) -> None:
    """After a goal ends: announce its proposals, or let the GM decide them."""
    from agent_company import executors, router
    from agent_company.governance import notify

    with session() as s:
        pending = [(p.id, p.title, p.description) for p in s.scalars(
            select(Proposal).where(Proposal.goal_id == goal_id, Proposal.status == "pending"))]
        g = s.get(Goal, goal_id)
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
        goal_title = g.title if g else ""
    if not pending:
        return
    if settings().proposal_policy != "superiors" or gm is None:
        for pid, title, why in pending:
            post_for_goal(goal_id, f"Proposal: {title}" + (f"\n{why}" if why else ""), kind="proposal",
                          proposal_id=pid)
        notify(f"{len(pending)} proposal(s) wait for a decision", goal_title, level="warning", source="proposals",
               goal_id=goal_id, admin_only=True)
        return
    choice = router.resolve("general_manager", gm.model_pref)
    for pid, title, why in pending:
        req = executors.Request(
            purpose="triage", role="general_manager", system=f"You are {gm.name}, the General Manager.",
            prompt=(f"An agent working on '{goal_title}' proposes follow-up work:\n{title}\n{why}\n\n"
                    "Approve it only if it clearly serves the project and is safe and small enough. "
                    "Answer with APPROVE or REJECT on the first line, then one sentence why."),
            data={"title": title})
        res = executors.for_choice(choice, settings().command_timeout).run(req)
        verdict = res.text.strip().upper().startswith("APPROVE") if res.ok else False
        decide(pid, verdict, f"{gm.name} (GM)")
