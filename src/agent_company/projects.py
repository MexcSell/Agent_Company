"""Projects, the command window and agent proposals.

A project is a git repository the company works on, with an optional GitHub URL,
default quality commands and a General Manager. Its command window is a
conversation: an order becomes a goal for that project's GM, who plans it and
reports back in the same thread (plan, task results, gate result). Slash
commands: /standup, /stop, /status, /summary, /sync, /help.

Topics (ASEAO05 S05-R-07) are conversation lanes of the command window: `general` always exists, others
are opened by anyone who may give orders. An order's goal carries its topic, so the goal's reports land
in that lane and goals of one topic run in order (G-R-07, `loop.lane_ahead`). Administrators assign agents
to a topic; its stand-up then reports them. `DECISION: <subject> :: <choice>` lines a person writes, and the
decisions a `/summary` finds, become decision records (SPEC-04 S04-R-02) instead of staying in chat.

The platform project (this repository) is registered as protected: it cannot be
deleted and only administrators see it, so agents can develop the platform itself.

Agents may end a task with `PROPOSAL: <title> :: <why>` lines. Proposals wait for an
administrator, or the GM decides them (`proposal_policy = superiors`). An approved
proposal becomes a new goal; one goal may spawn at most `max_proposal_goals`.

A new product or project (ASEAO10 S10-R-01) is `PROPOSAL[product]: …` or `PROPOSAL[new_project]: …`, followed by
its documented case as `- Concept: …`, `- Target user: …`, `- Expected value: …`, `- Feasibility: …`,
`- Requirements: …`, `- Estimated cost: …` lines. Only a person decides one, and only a complete case becomes a
project (its requirements as a draft requirement); an incomplete one stays a proposal until the case is written.
"""

from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from agent_company import worktrees
from agent_company.config import REPO_ROOT, settings
from agent_company.db import Agent, Goal, Message, Project, Proposal, Task, Topic, Upload, log_event, now, session
from agent_company.workflow import ACTIVE, set_status

NAME_RE = re.compile(r"^[\w][\w .-]{0,127}$")
PLATFORM = "Agent Company"
MAX_UPLOAD = 10 * 1024 * 1024
UPLOAD_TYPES = ("image/", "text/", "application/json", "application/pdf", "application/x-yaml")


def project_dict(p: Project, counts: dict[str, int] | None = None) -> dict[str, Any]:
    deadline = _aware(p.deadline)
    return {"id": p.id, "name": p.name, "path": p.path, "github_url": p.github_url, "description": p.description,
            "quality_commands": p.quality_commands, "protected": p.protected, "manager_id": p.manager_id,
            "classification": p.classification or "internal", "created_by": p.created_by,
            "stack": list(p.stack or []), "required_skills": list(p.required_skills or []), "rooms": list(p.rooms or []),
            "budget_usd": p.budget_usd or 0.0, "spent_usd": round(project_spend(p.id), 4),
            "deadline": deadline.isoformat() if deadline else None, "overdue": bool(deadline and deadline < now()
                                                                                   and (p.status or "active") in OPEN),
            "priority": p.priority or "normal", "status": p.status or "active", "status_reason": p.status_reason or "",
            "target_outcome": p.target_outcome or "", "related_project_id": p.related_project_id,
            "team_strategy": p.team_strategy or "gm", "created_at": p.created_at.isoformat(), **(counts or {})}


# --- registration fields and lifecycle (ASEAO05 S05-R-06) ---------------------------------------

PRIORITIES = ("low", "normal", "high", "critical")
TEAM_STRATEGIES = ("gm", "reuse", "new")  # UI-PROJ-05: the GM recommends | reuse existing teams | form a new team
OPEN = ("active", "paused")
PROJECT_TRANSITIONS = {"active": {"paused", "archived", "cancelled", "completed"},
                       "paused": {"active", "archived", "cancelled"},
                       "archived": {"active"}, "completed": {"archived"}, "cancelled": set()}


def _aware(t):  # noqa: ANN001, ANN202 - SQLite returns naive datetimes
    from datetime import UTC

    return t.replace(tzinfo=UTC) if t is not None and t.tzinfo is None else t


def project_spend(project_id: int) -> float:
    """What the project's goals have cost so far."""
    from agent_company.db import Run

    with session() as s:
        return float(s.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).join(Goal, Goal.id == Run.goal_id)
                              .where(Goal.project_id == project_id)) or 0.0)


def refusal(p: Project | None) -> str | None:
    """Why the project takes no new goals now, or None: only an active project within its budget does."""
    if p is None:
        return None
    if (p.status or "active") != "active":
        return f"project '{p.name}' is {p.status}" + (f" ({p.status_reason})" if p.status_reason else "")
    if (p.budget_usd or 0) > 0:
        spent = project_spend(p.id)
        if spent >= p.budget_usd:
            return f"project '{p.name}' used its budget of ${p.budget_usd:.2f} (${spent:.2f}); raise it first"
    return None


def _names(value: Any, what: str, slug: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 30 or not all(isinstance(x, str) and 0 < len(x.strip()) <= 64
                                                                  for x in value):
        raise ValueError(f"{what} is a list of at most 30 names")
    out = [re.sub(r"[^a-z0-9]+", "_", x.strip().lower()).strip("_") if slug else x.strip() for x in value]
    return list(dict.fromkeys(x for x in out if x))


def _fields(s, changes: dict[str, Any], current: list[Any] | None = None) -> dict[str, Any]:  # noqa: ANN001
    """Validate and normalise the registration fields of an update; `current` = the rooms the project lists now."""
    from datetime import datetime

    from agent_company.db import Room

    out = dict(changes)
    if "stack" in out:
        out["stack"] = _names(out["stack"], "stack")
    if "required_skills" in out:
        out["required_skills"] = _names(out["required_skills"], "required_skills", slug=True)
    if "rooms" in out:
        rooms = _names(out["rooms"], "rooms")
        known = {r.key for r in s.scalars(select(Room))}
        if set(rooms) - known:
            raise ValueError(f"unknown room(s): {', '.join(sorted(set(rooms) - known))}")
        off = (set(rooms) - set(current or [])) & {r.key for r in s.scalars(select(Room).where(Room.enabled.is_(False)))}
        if off:  # S01-R-01: a project keeps a room disabled later, but cannot add one
            raise ValueError(f"disabled room(s): {', '.join(sorted(off))}")
        out["rooms"] = rooms
    if "budget_usd" in out:
        try:
            out["budget_usd"] = round(float(out["budget_usd"] or 0), 4)
        except (TypeError, ValueError):
            out["budget_usd"] = -1.0
        if not 0 <= out["budget_usd"] <= 1_000_000:
            raise ValueError("budget_usd is 0 (none) to 1000000")
    if "deadline" in out:
        d = out["deadline"]
        try:
            out["deadline"] = _aware(datetime.fromisoformat(str(d).replace("Z", "+00:00"))) if d else None
        except ValueError as e:
            raise ValueError("deadline is an ISO date, e.g. 2026-12-31") from e
    if "priority" in out and out["priority"] not in PRIORITIES:
        raise ValueError(f"priority is one of {', '.join(PRIORITIES)}")
    if "team_strategy" in out and (out["team_strategy"] or "gm") not in TEAM_STRATEGIES:
        raise ValueError(f"team_strategy is one of {', '.join(TEAM_STRATEGIES)}")
    if "target_outcome" in out:
        out["target_outcome"] = str(out["target_outcome"] or "")[:4000]
    if "related_project_id" in out:
        rid = out["related_project_id"]
        out["related_project_id"] = int(rid) if rid else None
        if out["related_project_id"] is not None and s.get(Project, out["related_project_id"]) is None:
            raise ValueError(f"unknown related project #{rid}")
    return out


def set_status(project_id: int, to: str, actor: str, reason: str = "") -> dict[str, Any]:
    """Pause, resume, archive, cancel or complete a project. Pausing pauses its active goals (running ones stop at
    the next step) and resuming resumes the ones the pause stopped; cancelling cancels them; archiving and completing
    need a project without active goals. Paused, archived, cancelled and completed projects take no new goals."""
    from agent_company import loop

    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        cur = p.status or "active"
        if to == cur:
            return project_dict(p)
        if to not in PROJECT_TRANSITIONS.get(cur, set()):
            allowed = ", ".join(sorted(PROJECT_TRANSITIONS.get(cur, set()))) or "nothing (final)"
            raise ValueError(f"a {cur} project can become: {allowed}")
        if p.protected and to != "active":
            raise ValueError("the platform project stays active; stop its goals instead")
        goals = [(g.id, g.status, dict(g.checkpoint or {}))
                 for g in s.scalars(select(Goal).where(Goal.project_id == project_id, Goal.status.in_(ACTIVE)))]
        if to in {"archived", "completed"} and goals:
            raise ValueError(f"cancel or finish its {len(goals)} open goal(s) first: "
                             + ", ".join(f"#{g[0]} {g[1]}" for g in goals[:10]))
        p.status, p.status_reason = to, (reason or "").strip()[:500]
        log_event("project", f"project {p.name}: {cur} → {to} by {actor}" + (f": {reason}" if reason else ""), s=s,
                  project_id=project_id)
    changed: list[int] = []
    kept: dict[int, str] = {}
    for gid, status, cp in goals:
        try:
            if to == "cancelled":
                loop.cancel(gid, actor=actor)
                changed.append(gid)
            elif to == "paused" and status != "paused":
                loop.pause(gid, actor, f"project paused{': ' + reason if reason else ''}")
                with session() as s:
                    g = s.get(Goal, gid)
                    g.checkpoint = {**(g.checkpoint or {}), "paused_by_project": True}
                changed.append(gid)
            elif to == "active" and status == "paused" and cp.get("paused_by_project"):
                with session() as s:
                    g = s.get(Goal, gid)
                    g.checkpoint = {k: v for k, v in (g.checkpoint or {}).items() if k != "paused_by_project"}
                loop.resume(gid, actor)
                changed.append(gid)
        except ValueError as e:  # a goal over its own budget stays paused; the project changes anyway
            kept[gid] = str(e)
    verb = {"paused": "Paused", "active": "Resumed", "cancelled": "Cancelled"}.get(to, to.capitalize())
    post(project_id, f"Project {to} by {actor}" + (f": {reason}" if reason else "") + "."
         + (f" {verb} goal(s) {', '.join(f'#{g}' for g in changed)}." if changed else "")
         + "".join(f" Goal #{g} stays paused: {why}" for g, why in kept.items()), kind="result")
    with session() as s:
        return project_dict(s.get(Project, project_id)) | {"goals_changed": changed, "goals_kept": kept}


def seed() -> None:
    """Register this repository as the protected platform project."""
    with session() as s:
        if s.scalars(select(Project).where(Project.protected)).first() is None:
            gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
            s.add(Project(name=PLATFORM, path=str(REPO_ROOT), protected=True, manager_id=gm.id if gm else None,
                          description="Develop the Agent Company platform itself. Approved changes are merged "
                                      "into this repository; restart the service to load them.",
                          quality_commands=["uv run pytest -q"]))
        # Projects whose repository was set up with GitHub (at creation or since) show its URL.
        for p in s.scalars(select(Project).where(Project.github_url == "", Project.path != "")).all():
            p.github_url = repo_remote(p.path)


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
    from agent_company.policies import delegated

    return q.where(or_(Project.created_by == user["id"], Project.id.in_(granted or [-1]),
                       Project.id.in_(delegated(user["id"]))))  # while a delegation holds (S01-R-03)


def list_projects(user: dict[str, Any]) -> list[dict[str, Any]]:
    with session() as s:
        out = []
        for p in s.scalars(visible(user).order_by(Project.protected.desc(), Project.id)):
            running = s.scalar(select(func.count()).select_from(Goal).where(
                Goal.project_id == p.id, Goal.status.in_(ACTIVE))) or 0
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


GITHUB_URL_RE = re.compile(r"^(https://|git@)[\w.@:/~-]+$")


def repo_remote(path: str | Path) -> str:
    """The remote a project's repository was set up with (origin, else the first one), as its GitHub URL: an
    https URL loses any user name or token in it (never stored). Empty when there is none."""
    repo = Path(path or "").expanduser()
    if not path or not (repo / ".git").exists():
        return ""
    try:
        names = worktrees.git(repo, "remote", check=False).split()
        if not names:
            return ""
        url = worktrees.git(repo, "remote", "get-url", "origin" if "origin" in names else names[0], check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    url = re.sub(r"^https://[^/@]*@", "https://", url.strip())
    return url if GITHUB_URL_RE.match(url) else ""


def _check(name: str, path: str, github_url: str, quality_commands: list[str]) -> str:
    from agent_company.governance import check_command

    if not NAME_RE.match(name or ""):
        raise ValueError("project names are 1-128 letters, digits, spaces, dots, dashes or underscores")
    if github_url and not GITHUB_URL_RE.match(github_url):
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
           quality_commands: list[str] | None = None, created_by: int | None = None,
           **registration: Any) -> dict[str, Any]:
    """`registration`: target_outcome, related_project_id, team_strategy (UI-PROJ-05), validated as in `update`."""
    bad = set(registration) - {"target_outcome", "related_project_id", "team_strategy"}
    if bad:
        raise ValueError(f"unknown field(s): {', '.join(sorted(bad))}")
    path = _check(name, path, github_url, quality_commands or [])
    # A repository already set up with GitHub brings its URL; a URL given for one without a remote becomes its origin.
    remote = repo_remote(path) if path else ""
    if not github_url:
        github_url = remote
    elif path and not remote and not worktrees.git(Path(path), "remote", check=False).strip():
        worktrees.git(Path(path), "remote", "add", "origin", github_url, check=False)
    with session() as s:
        if s.scalars(select(Project).where(func.lower(Project.name) == name.lower())).first():
            raise ValueError("a project with that name exists")
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
        p = Project(name=name.strip(), path=path, github_url=github_url, description=description,
                    quality_commands=list(quality_commands or []), created_by=created_by,
                    manager_id=gm.id if gm else None, **_fields(s, registration))
        s.add(p)
        s.flush()
        if not p.path:
            p.path = str(worktrees.new_workspace(f"project-{p.id}"))
        if gm is not None:
            from agent_company.organization import record_position

            record_position(s, f"project:{p.id}:gm", None, gm.id, f"user:{created_by}" if created_by else "system",
                            "project created")
        s.add(Message(project_id=p.id, author_type="system", kind="chat",
                      content=f"Project created. Give the General Manager an order below; it works in {p.path}."))
        return project_dict(p)


def update(project_id: int, changes: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    """Change a project. A new General Manager (`manager_id`, with an optional `manager_reason`) is recorded in the
    position history `project:<id>:gm` (SPEC-01 S01-R-02)."""
    from agent_company.learning import CLASSIFICATIONS

    allowed = {"name", "github_url", "description", "quality_commands", "classification", "stack", "required_skills",
               "rooms", "budget_usd", "deadline", "priority", "manager_id", "manager_reason", "target_outcome",
               "related_project_id", "team_strategy"}
    bad = set(changes) - allowed
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    if "classification" in changes and changes["classification"] not in CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {', '.join(CLASSIFICATIONS)}")
    if changes.get("manager_id"):
        from agent_company.management import sync

        sync(project_id)  # current blockers and action items for the handover package (S05-R-09)
    handover = None
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        if p.protected and "name" in changes:
            raise ValueError("the platform project cannot be renamed")
        _check(changes.get("name", p.name), "", changes.get("github_url", p.github_url),
               changes.get("quality_commands", p.quality_commands))
        changes = _fields(s, changes, p.rooms)
        reason = str(changes.pop("manager_reason", "") or "")
        if "manager_id" in changes:
            from agent_company.organization import record_position

            changes["manager_id"] = int(changes["manager_id"]) if changes["manager_id"] else None
            gm = s.get(Agent, changes["manager_id"]) if changes["manager_id"] else None
            if gm is None or gm.lifecycle != "active":
                raise ValueError("the project's General Manager is an active agent")
            record_position(s, f"project:{p.id}:gm", p.manager_id, gm.id, actor, reason)
            if gm.id != p.manager_id:
                from agent_company.management import gm_handover

                handover = gm_handover(s, p, gm.id, actor, reason)
        old = p.classification or "internal"
        for k, v in changes.items():
            setattr(p, k, v.strip() if isinstance(v, str) else v)
        s.flush()
        out = project_dict(p)
        if handover is not None:
            pk = handover["package"]
            s.add(Message(project_id=p.id, author_type="system", kind="chat", agent_id=gm.id,
                          meta={"handover_id": handover["id"]},
                          content=f"{gm.name} is now the General Manager of this project ({actor}"
                                  + (f": {reason}" if reason else "") + "). Handover: "
                                  + f"{len(pk['goals'])} open goal(s), {len(pk['blockers'])} open blocker(s), "
                                  + f"{len(pk['action_items'])} open action item(s)."))
    if out["classification"] != old:
        from agent_company.learning import reclassify

        reclassify(project_id, out["classification"])
    return out


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
            for g in s.scalars(select(Goal).where(Goal.project_id == pid, Goal.status.in_(ACTIVE))):
                set_status(s, g, "cancelled", "project deleted")
            s.query(Message).filter(Message.project_id == pid).delete()
            s.query(Topic).filter(Topic.project_id == pid).delete()
            s.delete(p)
            done.append(pid)
    return done


# --- messages ---------------------------------------------------------------------

def post(project_id: int, content: str, author_type: str = "system", kind: str = "chat", goal_id: int | None = None,
         agent_id: int | None = None, user_id: int | None = None, attachments: list[Any] | None = None,
         topic: str = "general", **meta: Any) -> int:
    with session() as s:
        m = Message(project_id=project_id, content=content[:20000], author_type=author_type, kind=kind,
                    goal_id=goal_id, agent_id=agent_id, user_id=user_id, attachments=attachments or [], meta=meta,
                    topic=topic or GENERAL)
        s.add(m)
        s.flush()
        return m.id


def messages(project_id: int, after: int = 0, limit: int = 300, topic: str = "", q: str = "") -> list[dict[str, Any]]:
    """A project's messages, oldest first: all of them, one topic's, or those containing `q` (any case)."""
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        query = select(Message).where(Message.project_id == project_id, Message.id > after)
        if topic:
            query = query.where(Message.topic == topic_name(topic))
        if q.strip():
            query = query.where(Message.content.icontains(q.strip(), autoescape=True))
        rows = s.scalars(query.order_by(Message.id.desc()).limit(limit)).all()
        return [{"id": m.id, "author_type": m.author_type, "agent": names.get(m.agent_id), "agent_id": m.agent_id,
                 "user_id": m.user_id, "kind": m.kind, "content": m.content, "goal_id": m.goal_id,
                 "topic": m.topic or GENERAL, "attachments": m.attachments, "meta": m.meta,
                 "at": m.created_at.isoformat()}
                for m in reversed(rows)]


def post_for_goal(goal_id: int, content: str, kind: str = "chat", agent_id: int | None = None, **meta: Any) -> None:
    """Report on a goal in its project's command window, in the topic the order came from."""
    with session() as s:
        g = s.get(Goal, goal_id)
        pid, topic = (g.project_id, g.topic or GENERAL) if g else (None, GENERAL)
    if pid:
        post(pid, content, "agent" if agent_id else "system", kind, goal_id=goal_id, agent_id=agent_id, topic=topic,
             **meta)


def members(project_id: int, only: list[int] | None = None) -> list[dict[str, Any]]:
    """Agents that worked on the project's goals (or exactly `only`, a topic's agents), busy ones first. An agent
    removed from the project (`remove_members`) is left out until it gets a task of the project after its removal;
    the manager until any task of the project is created after it."""
    from datetime import datetime

    from agent_company.registry import agent_dict

    with session() as s:
        running = dict(s.execute(select(Task.agent_id, Task.title).join(Goal, Goal.id == Task.goal_id)
                                 .where(Goal.project_id == project_id, Task.status == "running")).all())
        if only is not None:
            ids = set(only)
        else:
            last = dict(s.execute(select(Task.agent_id, func.max(Task.created_at)).join(Goal, Goal.id == Task.goal_id)
                                  .where(Goal.project_id == project_id, Task.agent_id.is_not(None))
                                  .group_by(Task.agent_id)).all())
            ids = set(last)
            p = s.get(Project, project_id)
            if p and p.manager_id:
                ids.add(p.manager_id)
                last[p.manager_id] = s.scalar(select(func.max(Task.created_at)).join(Goal, Goal.id == Task.goal_id)
                                              .where(Goal.project_id == project_id))
            for aid, at in ((p.removed_members or {}) if p else {}).items():
                t = _aware(last.get(int(aid)))
                if int(aid) not in running and (t is None or t <= datetime.fromisoformat(at)):
                    ids.discard(int(aid))
        agents = s.scalars(select(Agent).where(Agent.id.in_(ids))).all()
        out = [agent_dict(a) | {"current_task": running.get(a.id)} for a in agents]
        return sorted(out, key=lambda a: (a["current_task"] is None, a["status"] != "busy", a["id"]))


def remove_members(project_id: int, agent_ids: list[int] | None = None) -> list[int]:
    """Take agents (None: every one shown now) off the project's Agents panel. They stay in the company and keep
    their history; an agent comes back once it works on the project again. Returns the ids removed."""
    shown = {a["id"] for a in members(project_id)}
    ids = sorted(shown if agent_ids is None else shown & {int(i) for i in agent_ids})
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        at = now().isoformat()
        p.removed_members = {**(p.removed_members or {}), **{str(i): at for i in ids}}
    return ids


# --- uploads ----------------------------------------------------------------------

def save_upload(name: str, data: bytes, content_type: str, user_id: int | None, classification: str = "internal"
                ) -> dict[str, Any]:
    from agent_company.learning import check_level

    check_level(classification or "internal")
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
        s.add(Upload(id=uid, name=safe, path=str(path), content_type=ctype, size=len(data), user_id=user_id,
                     classification=classification or "internal"))
    return {"id": uid, "name": safe, "content_type": ctype, "size": len(data), "classification": classification or "internal"}


def uploads(ids: list[str]) -> list[dict[str, Any]]:
    with session() as s:
        return [{"id": u.id, "name": u.name, "path": u.path, "content_type": u.content_type, "size": u.size,
                 "classification": u.classification or "internal"}
                for u in s.scalars(select(Upload).where(Upload.id.in_(ids)))]


def upload_path(uid: str) -> tuple[Path, str]:
    with session() as s:
        u = s.get(Upload, uid)
        if u is None:
            raise KeyError(uid)
        return Path(u.path), u.content_type


# --- topics (S05-R-07) ---------------------------------------------------------------

GENERAL = "general"
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9 _.-]{0,63}$")
DECISION_RE = re.compile(r"^\s*DECISION:\s*(.+?)\s*::\s*(.+?)(?:\s*::\s*(.+?))?\s*$", re.M)
SUMMARY_CHARS = 12000  # the newest messages that fit go to the summary


def topic_name(name: str) -> str:
    n = " ".join((name or "").strip().lower().split()) or GENERAL
    if not TOPIC_RE.match(n):
        raise ValueError("topic names are 1-64 lowercase letters, digits, spaces, dots, dashes or underscores")
    return n


def _topic_row(s, project_id: int, name: str, create: bool = False) -> Topic | None:  # noqa: ANN001
    t = s.scalars(select(Topic).where(Topic.project_id == project_id, Topic.name == name)).first()
    if t is None and create and name == GENERAL:  # general exists implicitly; its row is made when needed
        t = Topic(project_id=project_id, name=GENERAL)
        s.add(t)
        s.flush()
    return t


def topic_dict(t: Topic | None, project_id: int, name: str, names: dict[int, str], counts: dict[str, Any]
               ) -> dict[str, Any]:
    c = counts.get(name, (0, None))
    ids = list(t.agent_ids or []) if t else []
    return {"id": t.id if t else None, "project_id": project_id, "name": name, "description": t.description if t else "",
            "agent_ids": ids, "agents": [names[i] for i in ids if i in names], "messages": c[0],
            "last_at": c[1].isoformat() if c[1] else None, "summary": t.summary if t else "",
            "summary_at": t.summary_at.isoformat() if t and t.summary_at else None,
            "archived": bool(t.archived) if t else False}


def topics(project_id: int) -> list[dict[str, Any]]:
    """`general` first, then open topics by name, then archived ones; with message counts."""
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        counts = {k or GENERAL: (n, at) for k, n, at in s.execute(
            select(Message.topic, func.count(), func.max(Message.created_at))
            .where(Message.project_id == project_id).group_by(Message.topic))}
        rows = {t.name: t for t in s.scalars(select(Topic).where(Topic.project_id == project_id))}
        out = [topic_dict(rows.get(GENERAL), project_id, GENERAL, names, counts)]
        for t in sorted((t for n, t in rows.items() if n != GENERAL), key=lambda t: (t.archived, t.name)):
            out.append(topic_dict(t, project_id, t.name, names, counts))
        return out


def _check_agents(s, agent_ids: Any) -> list[int]:  # noqa: ANN001
    ids = [int(i) for i in agent_ids or []]
    known = set(s.scalars(select(Agent.id).where(Agent.id.in_(ids or [-1]))))
    missing = [i for i in ids if i not in known]
    if missing:
        raise ValueError(f"unknown agent(s): {', '.join(map(str, missing))}")
    return list(dict.fromkeys(ids))


def create_topic(project_id: int, name: str, description: str = "", agent_ids: list[int] | None = None,
                 created_by: int | None = None) -> dict[str, Any]:
    name = topic_name(name)
    if name == GENERAL:
        raise ValueError("the general topic always exists")
    with session() as s:
        if s.get(Project, project_id) is None:
            raise KeyError(project_id)
        if _topic_row(s, project_id, name):
            raise ValueError(f"topic '{name}' exists")
        s.add(Topic(project_id=project_id, name=name, description=(description or "").strip()[:2000],
                    agent_ids=_check_agents(s, agent_ids), created_by=created_by))
    post(project_id, f"Topic '{name}' opened." + (f" {description.strip()}" if description.strip() else ""),
         topic=name)
    return next(t for t in topics(project_id) if t["name"] == name)


def update_topic(project_id: int, name: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Administrators: description, assigned agents, archived (archived topics take no new messages)."""
    bad = set(changes) - {"description", "agent_ids", "archived"}
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    name = topic_name(name)
    if name == GENERAL and changes.get("archived"):
        raise ValueError("the general topic cannot be archived")
    with session() as s:
        t = _topic_row(s, project_id, name, create=True)
        if t is None:
            raise KeyError(name)
        if "agent_ids" in changes:
            t.agent_ids = _check_agents(s, changes["agent_ids"])
        if "description" in changes:
            t.description = str(changes["description"] or "").strip()[:2000]
        if "archived" in changes:
            t.archived = bool(changes["archived"])
    return next(x for x in topics(project_id) if x["name"] == name)


def _open_topic(project_id: int, name: str) -> str:
    name = topic_name(name)
    if name == GENERAL:
        return name
    with session() as s:
        t = _topic_row(s, project_id, name)
        if t is None:
            raise ValueError(f"no topic '{name}' in this project")
        if t.archived:
            raise ValueError(f"topic '{name}' is archived")
    return name


def _known_decision(project_id: int, subject: str, choice: str) -> bool:
    from agent_company.db import Decision

    with session() as s:
        return s.scalars(select(Decision.id).where(
            Decision.project_id == project_id, func.lower(Decision.topic) == subject.lower()[:256],
            func.lower(Decision.recommendation) == choice.lower()[:4000])).first() is not None


def record_decision_lines(project_id: int, topic: str, content: str, message_id: int, author: str
                          ) -> list[dict[str, Any]]:
    """`DECISION: <subject> :: <choice> [:: <why>]` lines a person wrote in the command window."""
    from agent_company import decisions

    out = []
    for subject, choice, why in DECISION_RE.findall(content or "")[:10]:
        if _known_decision(project_id, subject, choice):
            continue
        out.append(decisions.record(None, subject, choice, reasoning=why, project_id=project_id,
                                    evidence=[f"message #{message_id} by {author} in topic '{topic}'"]))
    return out


def summary_draft(project_id: int, topic: str) -> tuple[str, str]:
    """(an extractive summary, the transcript a model summarises): goals with their state, decisions recorded,
    and the newest messages that fit in SUMMARY_CHARS."""
    rows = messages(project_id, topic=topic, limit=400)
    lines: list[str] = []
    used = 0
    for m in reversed(rows):
        who = m["meta"].get("author") if m["author_type"] == "user" else m["agent"] or "System"
        line = f"[{m['at'][:16]}] {who} ({m['kind']}): {m['content']}"
        if used + len(line) > SUMMARY_CHARS:
            break
        lines.append(line)
        used += len(line)
    transcript = "\n".join(reversed(lines))
    with session() as s:
        goals = s.scalars(select(Goal).where(Goal.project_id == project_id, Goal.topic == topic)
                          .order_by(Goal.id.desc()).limit(10)).all()
        goal_lines = [f"- #{g.id} [{g.status}] {g.title}" for g in goals]
    from agent_company import decisions

    found = [d for d in decisions.list_decisions(project_id=project_id, limit=400)
             if any(f"in topic '{topic}'" in str(e) for e in d["evidence"])
             or (d["goal_id"] and d["goal_id"] in {g.id for g in goals})][:10]
    out = [f"Summary of topic '{topic}': {len(rows)} message(s)"
           + (f" since {rows[0]['at'][:10]}" if rows else "") + "."]
    if goal_lines:
        out += ["Goals:", *goal_lines]
    if found:
        out += ["Decisions:", *[f"- #{d['id']} {d['topic']}: {d['recommendation'][:200]} ({d['status']})"
                                for d in found]]
    last = [r for r in rows if r["kind"] in ("command", "result", "error")][-5:]
    if last:
        out += ["Latest:", *[f"- {r['content'].splitlines()[0][:160]}" for r in last if r["content"]]]
    return "\n".join(out), transcript


def topic_level(project_id: int, topic: str) -> str:
    """The most classified content in a topic: its project's level, raised by classified orders and their goals
    (S14-R-05). A summary may only go to a model cleared for it."""
    from agent_company.learning import goal_level, highest

    with session() as s:
        p = s.get(Project, project_id)
        levels = [p.classification if p else None]
        levels += [goal_level(s, g) for g in s.scalars(select(Goal).where(Goal.project_id == project_id,
                                                                          Goal.topic == topic))]
        levels += [(m.meta or {}).get("classification") for m in s.scalars(
            select(Message).where(Message.project_id == project_id, Message.topic == topic,
                                  Message.kind == "command"))]
    return highest(*levels)


def summarize(project_id: int, topic: str) -> str:
    """The GM's summary of a topic (its model; the extractive draft when no model answers). Decisions the model
    lists in a fenced ```json {"decisions": [...]}``` block become decision records; the summary is kept on the
    topic."""
    from agent_company import decisions, executors, router
    from agent_company.management import _FENCED

    draft, transcript = summary_draft(project_id, topic)
    with session() as s:
        p = s.get(Project, project_id)
        gm = s.get(Agent, p.manager_id) if p and p.manager_id else None
        gm_name, gm_pref, gm_id = (gm.name, gm.model_pref, gm.id) if gm else ("General Manager", None, None)
    level = topic_level(project_id, topic)
    text, recorded = draft, []
    if transcript:
        req = executors.Request(
            purpose="summary", role="general_manager", system=f"You are {gm_name}, the General Manager.",
            prompt=(f"Summarise the project conversation '{topic}' below for someone who has not read it: what "
                    "was asked, what was done, what is open and who is waiting on whom. At most 15 lines.\n"
                    "If the conversation settles decisions that are not yet listed under 'Decisions' in the draft, "
                    "add a fenced ```json block {\"decisions\": [{\"topic\", \"recommendation\", \"reasoning\", "
                    "\"alternatives\", \"confidence\", \"category\"}]} after the summary.\n\n"
                    f"Draft:\n{draft}\n\nConversation:\n{transcript}"),
            data={"draft": draft})
        try:  # only a model cleared for the topic's classification (S14-R-05) reads the conversation
            choice = router.resolve("general_manager", gm_pref, classification=level)
            res = executors.for_choice(choice, settings().command_timeout).run(req)
        except Exception as e:  # noqa: BLE001 - the draft still answers (also when no model is cleared)
            res = executors.Result(text="", ok=False, error=str(e))
        if res.ok and res.text.strip():
            text = res.text.strip()
            items = []
            for block in _FENCED.findall(text):
                try:
                    data = json.loads(block)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("decisions"), list):
                    items += [it for it in data["decisions"] if isinstance(it, dict)
                              and not _known_decision(project_id, str(it.get("topic", "")),
                                                      str(it.get("recommendation", "")))]
            text = _FENCED.sub("", text).strip() or draft
            for it in items[:10]:
                it["evidence"] = [*(it.get("evidence") or [])][:5] + [f"summary of topic '{topic}'"]
            recorded = decisions.from_plan(None, items[:10], gm_id, project_id)
    if recorded:
        text += "\n\nRecorded as decisions: " + ", ".join(f"#{d['id']} {d['topic']}" for d in recorded)
    with session() as s:
        t = _topic_row(s, project_id, topic, create=True)
        if t is not None:
            t.summary, t.summary_at = text[:20000], now()
    return text


# --- commands ---------------------------------------------------------------------

HELP = ("Commands: an order in plain words starts a goal for the General Manager, in the topic it is sent in.\n"
        "DECISION: <subject> :: <choice> [:: <why>]  record a decision (no goal is started)\n"
        "/standup  every agent on this project (or assigned to this topic) reports its status\n"
        "/status   open blockers, the goals (of this topic) and their state, what happens next\n"
        "/summary  the General Manager summarises this topic and records the decisions it finds\n"
        "/stop     cancel the project's running and queued goals (every topic)\n"
        "/sync     push approved work to the project's GitHub remote\n"
        "/help     this list")


def command(project_id: int, user: dict[str, Any], content: str, attachment_ids: list[str] | None = None,
            topic: str = GENERAL, classification: str = "") -> dict[str, Any]:
    """Handle one command-window entry in a topic. Returns {"message_id", "goal_id"?, "decisions"?}.
    `classification` marks the order and its attachments (SPEC-14 S14-R-05); below the project's it is ignored."""
    from agent_company import loop
    from agent_company.auth import can

    p = get(project_id, user)
    if not can(user, "run"):
        raise PermissionError("you can read this project but not give it orders")
    content = (content or "").strip()
    files = uploads(attachment_ids or [])
    if not content and not files:
        raise ValueError("empty command")
    topic = _open_topic(p.id, topic)
    from agent_company.learning import check_level, highest, rank

    level = highest(check_level(classification) if classification else None, p.classification)
    if files:
        with session() as s:
            for u in s.scalars(select(Upload).where(Upload.id.in_([f["id"] for f in files]))):
                u.classification = highest(u.classification, level)
    mid = post(p.id, content, "user", "command", user_id=user["id"],
               attachments=[{k: f[k] for k in ("id", "name", "content_type", "size")} for f in files],
               topic=topic, author=user["name"], **({"classification": level} if rank(level) > rank("internal") else {}))
    slash = content.split()[0].lower() if content.startswith("/") else ""

    def reply(text: str, kind: str = "chat", **kw: Any) -> None:
        post(p.id, text, kind=kind, topic=topic, **kw)

    if slash == "/help":
        reply(HELP)
        return {"message_id": mid}
    if slash == "/status":
        reply(status_text(p.id, topic), "result")
        return {"message_id": mid}
    if slash == "/summary":
        reply(summarize(p.id, topic), "result", agent_id=p.manager_id, author_type="agent" if p.manager_id else "system")
        return {"message_id": mid}
    if slash == "/stop":
        with session() as s:
            ids = [g.id for g in s.scalars(select(Goal).where(Goal.project_id == p.id,
                                                              Goal.status.in_(ACTIVE)))]
        for gid in ids:
            loop.cancel(gid)
        reply(f"Stopped {len(ids)} goal(s)." if ids else "Nothing is running.", "result")
        return {"message_id": mid}
    if slash == "/standup":
        reply(standup(p.id, topic), "standup", author_type="agent", agent_id=p.manager_id)
        return {"message_id": mid}
    if slash == "/sync":
        from agent_company import gitsync

        reply(gitsync.sync_project(p.id, user), "result")
        return {"message_id": mid}
    if slash:
        reply(f"Unknown command {slash}.\n\n{HELP}", "error")
        return {"message_id": mid}
    found = record_decision_lines(p.id, topic, content, mid, user["name"])
    rest = DECISION_RE.sub("", content).strip()
    if DECISION_RE.search(content):
        reply(("Recorded: " + ", ".join(f"decision #{d['id']} {d['topic']}" for d in found)) if found
              else "Those decisions are already recorded.", "result")
        if not rest and not files:  # only decisions: no goal
            return {"message_id": mid, "decisions": [d["id"] for d in found]}
    with session() as s:
        why = refusal(s.get(Project, p.id))
    if why:  # S05-R-06: the order is kept, no goal is started
        reply(f"No new goals: {why}.", "error")
        return {"message_id": mid}
    title = content.splitlines()[0][:200] if content else f"Look at {files[0]['name']}"
    if len(title) < 3:
        title = f"Order: {title}"
    gid = loop.create_goal(title, content or title, repo_path=p.path, quality_commands=list(p.quality_commands),
                           created_by=user["id"], project_id=p.id, attachments=files,
                           extract_checks=user["is_admin"], topic=topic, classification=level)
    with session() as s:  # UI-GM-05: the order names the goal it started
        s.get(Message, mid).goal_id = gid
    ahead = loop.lane_ahead(gid)
    reply(f"Order received as goal #{gid}. " + (f"It starts when goal #{ahead} of this topic is done."
                                               if ahead else "Planning now."),
          author_type="agent", goal_id=gid, agent_id=p.manager_id)
    loop.enqueue(gid)
    return {"message_id": mid, "goal_id": gid}


ORDER_REPLY = "Order received as goal #"
TRACE_GOALS = 50


def _order_goal(s, m: Message) -> int | None:  # noqa: ANN001
    """The goal an order started: its own link, or (orders before UI-GM-05) the GM's "Order received" reply."""
    if m.goal_id or m.kind != "command":
        return m.goal_id
    lane = (Message.project_id == m.project_id) & (Message.topic == m.topic)
    nxt = s.scalars(select(Message.id).where(lane, Message.id > m.id, Message.kind == "command")
                    .order_by(Message.id)).first()  # a reply after the next order answers that one
    return s.scalars(select(Message.goal_id).where(
        lane, Message.id > m.id, Message.id < (nxt or m.id + 50), Message.goal_id.is_not(None),
        Message.content.like(f"{ORDER_REPLY}%")).order_by(Message.id)).first()


def trace(message_id: int, user: dict[str, Any]) -> dict[str, Any]:
    """UI-GM-05 (FR-003.7): what a message led to, from stored records only: the goal it started or belongs to and
    the follow-up goals spawned from it, each with its requirements, tasks, decisions, merge and release record; and
    the decisions the message itself recorded (`DECISION:` lines)."""
    from agent_company.db import Decision, Deliverable, Requirement, RequirementLink

    with session() as s:
        m = s.get(Message, message_id)
        if m is None:
            raise KeyError(message_id)
        pid = m.project_id
    get(pid, user)  # the project must be visible to the person
    with session() as s:
        m = s.get(Message, message_id)
        root = _order_goal(s, m)
        ids: list[int] = []
        frontier = [root] if root else []
        while frontier and len(ids) < TRACE_GOALS:  # the goal and its descendants (follow-ups), breadth first
            ids += frontier
            frontier = list(s.scalars(select(Goal.id).where(Goal.parent_goal_id.in_(frontier))
                                      .order_by(Goal.id)))[: TRACE_GOALS - len(ids)]
        goals = {g.id: g for g in s.scalars(select(Goal).where(Goal.id.in_(ids)))}
        keys: dict[int, set[str]] = {i: set(goals[i].requirement_keys or []) for i in goals}
        for gid, key in s.execute(select(RequirementLink.goal_id, Requirement.key)
                                  .join(Requirement, Requirement.id == RequirementLink.requirement_id)
                                  .where(RequirementLink.goal_id.in_(ids))):
            keys[gid].add(key)
        tasks: dict[int, dict[str, int]] = {}
        for gid, status, n in s.execute(select(Task.goal_id, Task.status, func.count()).where(Task.goal_id.in_(ids))
                                        .group_by(Task.goal_id, Task.status)):
            tasks.setdefault(gid, {})[status] = n
        decisions: dict[int, list[dict[str, Any]]] = {}
        for d in s.scalars(select(Decision).where(Decision.goal_id.in_(ids)).order_by(Decision.id)):
            decisions.setdefault(d.goal_id, []).append({"id": d.id, "topic": d.topic, "status": d.status})
        releases = {r.goal_id: r for r in s.scalars(select(Deliverable).where(Deliverable.goal_id.in_(ids)))}
        recorded = [{"id": d.id, "topic": d.topic, "status": d.status}
                    for d in s.scalars(select(Decision).where(Decision.project_id == pid).order_by(Decision.id))
                    if any(str(e).startswith(f"message #{m.id} ") for e in d.evidence or [])]
        out = []
        for i in ids:
            g, rel = goals[i], releases.get(i)
            out.append({"id": g.id, "title": g.title, "status": g.status, "parent_goal_id": g.parent_goal_id,
                        "origin": g.origin, "iteration": g.iteration, "requirements": sorted(keys[i]),
                        "tasks": tasks.get(i, {}), "decisions": decisions.get(i, []),
                        "release": {"id": rel.id, "merge_commit": rel.merge_commit, "status": rel.status,
                                    "approved_by": rel.approved_by, "at": rel.created_at.isoformat()} if rel else None})
        return {"message": {"id": m.id, "kind": m.kind, "topic": m.topic, "content": m.content[:2000],
                            "at": m.created_at.isoformat()}, "project_id": pid, "goals": out, "decisions": recorded,
                "truncated": len(ids) >= TRACE_GOALS}


def status_text(project_id: int, topic: str = GENERAL) -> str:
    """Open blockers first, then the latest goals (of the topic, unless it is general), then what happens next
    (ASEAO05 S05-R-02, S05-R-04)."""
    from agent_company.management import status_lines

    blockers, nxt = status_lines(project_id)
    with session() as s:
        q = select(Goal).where(Goal.project_id == project_id)
        if topic != GENERAL:
            q = q.where(Goal.topic == topic)
        goals = s.scalars(q.order_by(Goal.id.desc()).limit(10)).all()
        lines = [f"#{g.id} [{g.status}] {g.title} (iteration {g.iteration}/{g.max_iterations})" for g in goals]
        d = project_dict(s.get(Project, project_id))
    head = [x for x in [
        f"Project {d['status']}" + (f": {d['status_reason']}" if d["status_reason"] else "") if d["status"] != "active"
        else "",
        f"Deadline {d['deadline'][:10]}" + (" — overdue" if d["overdue"] else "") if d["deadline"] else "",
        f"Budget ${d['spent_usd']:.2f} of ${d['budget_usd']:.2f} used" if d["budget_usd"] else ""] if x]
    out = ([*head, ""] if head else []) + (["Open blockers:", *blockers, ""] if blockers else []) + (lines or ["No goals yet."])
    return "\n".join(out + (["", "Next:", *nxt] if nxt else []))


def standup(project_id: int, topic: str = GENERAL) -> str:
    """Each agent on the project (or assigned to the topic, when it has agents) reports: what it is doing, what it
    finished, what failed. Open blockers first."""
    from agent_company.management import status_lines

    blockers, _ = status_lines(project_id, 0)
    with session() as s:
        t = _topic_row(s, project_id, topic)
        only = list(t.agent_ids) if t is not None and t.agent_ids else None
    lines = ["Stand-up" + (f" ({topic})" if only else ""), *(["Open blockers:", *blockers] if blockers else [])]
    with session() as s:
        for a in members(project_id, only):
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
# ASEAO10 S10-R-01: a proposed product or project and the case it needs before it may become a project
CASE_KINDS = ("product", "new_project")
PROPOSAL_KINDS = ("follow_up", *CASE_KINDS)
CASE_FIELDS = {"concept": "Concept", "target_user": "Target user", "expected_value": "Expected value",
               "feasibility": "Feasibility", "requirements": "Requirements", "estimated_cost": "Estimated cost"}
CASE_RE = re.compile(r"^\s*PROPOSAL\[(product|new_project)\]:\s*(.+?)(?:\s*::\s*(.+))?\s*$", re.M | re.I)
_CASE_LINE = re.compile(r"^\s*[-*]?\s*(" + "|".join(v.lower() for v in CASE_FIELDS.values())
                        + r")\s*:\s*(.+?)\s*$", re.I)
CASE_LIMIT = 2000  # characters per field


def _case_after(output: str, end: int) -> dict[str, str]:
    """The case lines right below a PROPOSAL[product] line, up to a blank line or the next PROPOSAL."""
    by_label = {v.lower(): k for k, v in CASE_FIELDS.items()}
    case: dict[str, str] = {}
    for line in output[end:].splitlines()[1:]:
        if not line.strip() or line.strip().upper().startswith("PROPOSAL"):
            break
        m = _CASE_LINE.match(line)
        if m:
            case[by_label[m.group(1).lower()]] = m.group(2)[:CASE_LIMIT]
    return case


def case_missing(kind: str, case: dict[str, Any] | None) -> list[str]:
    """The case fields a product or project proposal still lacks (none for a follow-up)."""
    if kind not in CASE_KINDS:
        return []
    return [k for k in CASE_FIELDS if not str((case or {}).get(k) or "").strip()]


def extract_proposals(goal_id: int, agent_id: int, output: str) -> int:
    output = output or ""
    found: list[tuple[int, str, str, str, dict[str, str]]] = [
        (m.start(), "follow_up", m.group(1), m.group(2) or "", {}) for m in PROPOSAL_RE.finditer(output)]
    found += [(m.start(), m.group(1).lower(), m.group(2), m.group(3) or "", _case_after(output, m.start()))
              for m in CASE_RE.finditer(output)]
    found = sorted(found)[:PROPOSALS_PER_TASK]
    if not found:
        return 0
    added = 0
    with session() as s:
        g = s.get(Goal, goal_id)
        # a product or project the goal already proposes is not proposed again (parallel tasks of one analysis)
        named = {t.lower() for t in s.scalars(select(Proposal.title).where(
            Proposal.goal_id == goal_id, Proposal.kind.in_(CASE_KINDS), Proposal.status == "pending"))}
        for _, kind, title, why, case in found:
            title = title.strip()[:256]
            if kind in CASE_KINDS:
                if title.lower() in named:
                    continue
                named.add(title.lower())
            s.add(Proposal(goal_id=goal_id, project_id=g.project_id if g else None, agent_id=agent_id,
                           title=title, description=why.strip(), kind=kind, case=case))
            added += 1
    return added


def proposal_dict(p: Proposal, names: dict[int, str]) -> dict[str, Any]:
    kind = p.kind or "follow_up"
    return {"id": p.id, "goal_id": p.goal_id, "project_id": p.project_id, "agent": names.get(p.agent_id),
            "title": p.title, "description": p.description, "status": p.status, "decided_by": p.decided_by,
            "spawned_goal_id": p.spawned_goal_id, "tag": p.tag or "", "report_id": p.report_id,
            "kind": kind, "case": dict(p.case or {}), "case_missing": case_missing(kind, p.case),
            "spawned_project_id": p.spawned_project_id, "at": p.created_at.isoformat()}


def set_case(proposal_id: int, kind: str | None = None, case: dict[str, Any] | None = None) -> dict[str, Any]:
    """A person writes (or completes) the documented case of a pending proposal, or changes its kind (S10-R-01).
    Given fields replace the stored ones; an empty value removes one."""
    with session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if p.status != "pending":
            raise ValueError(f"proposal is already {p.status}")
        if p.kind == "rule":
            raise ValueError("a rule proposal has no documented case")
        if kind is not None:
            if kind not in PROPOSAL_KINDS:
                raise ValueError(f"kind must be one of {', '.join(PROPOSAL_KINDS)}")
            p.kind = kind
        if case:
            unknown = sorted(set(case) - set(CASE_FIELDS))
            if unknown:
                raise ValueError(f"unknown case field(s): {', '.join(unknown)}")
            merged = dict(p.case or {})
            for k, v in case.items():
                v = str(v or "").strip()[:CASE_LIMIT]
                if v:
                    merged[k] = v
                else:
                    merged.pop(k, None)
            p.case = merged
        return proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))


def _case_text(title: str, why: str, case: dict[str, Any]) -> str:
    return "\n".join([why.strip() or title, "", *(f"{label}: {case[k]}" for k, label in CASE_FIELDS.items()
                                                   if k != "requirements")]).strip()


def _to_project(proposal_id: int, decided_by: str) -> dict[str, Any]:
    """Turn a complete product or project proposal into a project: the case is its description, the requirements
    draft its first (draft) requirement. Called after the checks in `decide`, outside any session."""
    from agent_company import requirements

    with session() as s:
        p = s.get(Proposal, proposal_id)
        origin = s.get(Goal, p.goal_id)
        title, why, case = p.title, p.description, dict(p.case or {})
        created_by = origin.created_by if origin else None
    name = re.sub(r"\s+", " ", title).strip()[:128]
    made = create(name, description=_case_text(title, why, case), created_by=created_by)
    requirements.create(made["id"], case["requirements"], kind="functional", source=f"proposal:{proposal_id}",
                        actor=decided_by, status="draft", check_conflicts=False)
    with session() as s:
        p = s.get(Proposal, proposal_id)
        p.status, p.decided_by, p.spawned_project_id = "approved", decided_by, made["id"]
        out = proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))
    if out["project_id"]:
        post(out["project_id"], f"Proposal approved by {decided_by}: '{out['title']}' is now project "
                                f"'{made['name']}' (#{made['id']}).", kind="proposal")
    log_event("proposal", f"proposal {proposal_id} approved by {decided_by} as project #{made['id']}",
              goal_id=out["goal_id"])
    return out


def _to_rule(proposal_id: int, decided_by: str) -> dict[str, Any]:
    """EL-R-04: an approved rule proposal becomes an enabled catalog rule, which `catalog.export_claude` (and `gm
    configure`) write to .claude/rules/<name>.md. Called after the checks in `decide`, outside any session."""
    from agent_company import catalog

    with session() as s:
        p = s.get(Proposal, proposal_id)
        description, case = p.description, dict(p.case or {})
    cause = case.get("cause") or {}
    goals = ", ".join(f"#{g}" for g in case.get("goals") or [])
    line = case.get("rule") or description.splitlines()[0]
    args = dict(description=f"From RCA: {cause.get('layer', '')}/{cause.get('check', '')} in goals {goals}",
                content=f"- {line}\n\nEvidence: {description}", source=f"proposal:{proposal_id}",
                created_by=decided_by)
    name = case.get("name") or f"rca-rule-{proposal_id}"
    try:
        item = catalog.add("rule", name, **args)
    except ValueError:  # the name exists (another project's proposal for the same cause)
        item = catalog.add("rule", f"{name[:56]}-{proposal_id}", **args)
    with session() as s:
        p = s.get(Proposal, proposal_id)
        p.status, p.decided_by = "approved", decided_by
        p.case = {**case, "catalog_id": item["id"], "name": item["name"]}  # a new dict: in-place changes are lost
        out = proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))
    if out["project_id"]:
        post(out["project_id"], f"Proposal approved by {decided_by}: rule '{item['name']}' added to the catalog.",
             kind="proposal")
    log_event("proposal", f"proposal {proposal_id} approved by {decided_by} as catalog rule '{item['name']}'",
              goal_id=out["goal_id"])
    return out


def list_proposals(user: dict[str, Any], status: str = "") -> list[dict[str, Any]]:
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        seen = None if user["is_admin"] else set(s.scalars(visible(user).with_only_columns(Project.id)))
        q = select(Proposal)
        if status:
            q = q.where(Proposal.status == status)
        rows = s.scalars(q.order_by(Proposal.id.desc()).limit(200)).all()
        return [proposal_dict(p, names) for p in rows if seen is None or p.project_id in seen]


# --- UI-GM-02: "New project…" — the GM analyses a request and proposes a project; it starts no code ---------------

ANALYSIS_TOPIC = "new-project"
ANALYSIS_NOTE = (
    "This is a request for a new project, not an order to build it: nobody changes or creates a file. Analyse the "
    "request: what it is for, who would use it, what it needs, how feasible it is and what it would cost, and say "
    "what is unknown. The final task, and only that task, ends its answer with this block, every line filled:\n"
    "PROPOSAL[new_project]: <project name> :: <why, one line>\n- Concept: …\n- Target user: …\n- Expected value: …\n"
    "- Feasibility: …\n- Requirements: …\n- Estimated cost: …\n"
    "A person reads the case and decides whether it becomes a project.")


def analyse_new(text: str, user: dict[str, Any]) -> int:
    """A message to "New project…" (UI-GM-02, S10-R-01): a read-only research goal without a project that ends in a
    documented `PROPOSAL[new_project]`; only a person turns it into a project (`decide`)."""
    from agent_company import loop

    text = text.strip()
    if not text:
        raise ValueError("describe the project you have in mind")
    head = re.sub(r"\s+", " ", text.splitlines()[0]).strip()
    return loop.create_goal(f"New project analysis: {head[:200]}", f"{text[:8000]}\n\n{ANALYSIS_NOTE}",
                            created_by=user["id"], origin="user", topic=ANALYSIS_TOPIC, kind="research")


def analyses(user: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
    """The "New project…" analyses, newest first (a person sees their own, an administrator every one), each with
    its questions while it waits for answers and the project proposals it raised."""
    with session() as s:
        q = select(Goal).where(Goal.project_id.is_(None), Goal.topic == ANALYSIS_TOPIC)
        if not user["is_admin"]:
            q = q.where(Goal.created_by == user["id"])
        goals = s.scalars(q.order_by(Goal.id.desc()).limit(max(1, min(limit, 100)))).all()
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        props: dict[int, list[dict[str, Any]]] = {}
        for p in s.scalars(select(Proposal).where(Proposal.goal_id.in_([g.id for g in goals]),
                                                  Proposal.kind.in_(CASE_KINDS)).order_by(Proposal.id)):
            props.setdefault(p.goal_id, []).append(proposal_dict(p, names))
        return [{"goal_id": g.id, "title": g.title.removeprefix("New project analysis: "),
                 "request": g.description.split(f"\n\n{ANALYSIS_NOTE}")[0], "status": g.status,
                 "paused_for": (g.checkpoint or {}).get("paused_for") if g.status == "paused" else None,
                 "questions": list((g.checkpoint or {}).get("questions") or []),
                 "proposals": props.get(g.id, []), "at": g.created_at.isoformat()} for g in goals]


def _root(s, goal: Goal) -> Goal:  # noqa: ANN001
    seen = set()
    while goal.parent_goal_id and goal.id not in seen:
        seen.add(goal.id)
        parent = s.get(Goal, goal.parent_goal_id)
        if parent is None:
            break
        goal = parent
    return goal


def _eval_source(s, goal: Goal | None) -> Goal | None:  # noqa: ANN001
    """Find the nearest benchmark ancestor, or recognise a legacy eval workspace."""
    origin, seen = goal, set()
    while goal is not None and goal.id not in seen:
        seen.add(goal.id)
        if goal.origin == "eval":
            return goal
        goal = s.get(Goal, goal.parent_goal_id) if goal.parent_goal_id else None
    if origin is not None and origin.repo_path:
        try:
            relative = Path(origin.repo_path).resolve().relative_to(settings().workspaces_dir.resolve())
            if relative.parts and relative.parts[0].startswith("eval-"):
                return origin
        except (ValueError, OSError):
            pass
    return None


def _spawned_from(s, root_id: int) -> int:  # noqa: ANN001
    count, frontier = 0, [root_id]
    while frontier:
        kids = list(s.scalars(select(Goal.id).where(Goal.parent_goal_id.in_(frontier), Goal.origin == "proposal")))
        count += len(kids)
        frontier = kids
    return count


def _depth(s, goal: Goal) -> int:  # noqa: ANN001
    """1 for a goal a person ordered, +1 for each proposal generation below it."""
    depth, seen = 1, set()
    while goal.parent_goal_id and goal.id not in seen:
        seen.add(goal.id)
        goal = s.get(Goal, goal.parent_goal_id)
        if goal is None:
            break
        depth += 1
    return depth


# Proposals that would change authority, budgets or safety controls are decided by a person, never by an
# agent (SPEC-14 S14-R-02, SEC-007): an agent must not approve a change that raises any agent's authority.
RESTRICTED = re.compile(r"(?i)\b(permission|privilege|autonomy|budget|setting|route|routing|model choice"
                        r"|security|approval|emergency|audit|credential|secret|access control|sudo"
                        # what the tool gateway decides (S14-R-02, S14-R-06): an agent never widens its own tools
                        r"|shell|bash|sandbox|allowed ?tool|tool access|mcp|network access|write access|grant"
                        r"|admin|administrator|root access|G[0-5]|deploy(ment)?|production)s?\b"
                        r"|\bescalat\w*")


def restricted(title: str, description: str = "") -> bool:
    return bool(RESTRICTED.search(f"{title}\n{description}"))


def chain_block(s, origin: Goal | None) -> str | None:  # noqa: ANN001
    """Why the goal-chain guards refuse another goal spawned from `origin`'s family, or None."""
    if origin is None:
        return None
    limit = settings().max_proposal_goals
    if _spawned_from(s, _root(s, origin).id) >= limit:
        return f"limit of {limit} proposal goals reached"
    if _depth(s, origin) >= settings().max_goal_depth:
        return f"goal chain depth {settings().max_goal_depth} reached"  # S04-R-08
    return None


def decide(proposal_id: int, approve: bool, decided_by: str, by_human: bool = True,
           hold: bool = False) -> dict[str, Any]:
    """Approve (spawning a goal, within the loop guards) or reject a proposal. An agent (`by_human=False`)
    cannot approve a restricted proposal. A product or project proposal (S10-R-01) is approved only by a person and
    only with its documented case complete; it becomes a project, not a goal. `hold` (the owner policy): an approval
    the chain guards or the parent's budget refuse leaves the proposal pending, with the reason in 'held'."""
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
        if approve and not by_human and restricted(p.title, p.description):
            raise PermissionError("this proposal touches authority, budgets or safety controls: a person decides it")
        kind = p.kind or "follow_up"
        if approve and kind in CASE_KINDS:  # S10-R-01: a project only from a documented case, decided by a person
            if not by_human:
                raise PermissionError("a new product or project is a person's decision")
            missing = case_missing(kind, p.case)
            if missing:
                raise ValueError("the documented case is incomplete (missing "
                                 + ", ".join(CASE_FIELDS[k] for k in missing) + "): it stays a proposal")
            name = re.sub(r"\s+", " ", p.title).strip()[:128]
            if s.scalars(select(Project.id).where(func.lower(Project.name) == name.lower())).first():
                raise ValueError(f"a project named '{name}' exists; rename the proposal's title first")
            kind = "project"
        if approve and kind == "rule" and not by_human:  # EL-R-04: the catalog rule reaches every agent
            raise PermissionError("a project rule changes every agent's instructions: a person decides it")
        if kind in ("project", "rule"):  # the goal-chain guards limit spawned goals; neither spawns one
            pass
        elif approve and (blocked := chain_block(s, origin)):
            if hold:  # stays pending: no status, no decider, no rejection post
                return {**proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all())), "held": blocked}
            approve, note = False, f" ({blocked})"
        if kind != "project" and not (kind == "rule" and approve):
            p.status, p.decided_by = ("approved" if approve else "rejected"), decided_by + note
        if kind == "rule":
            pass
        elif approve and kind != "project" and (src := _eval_source(s, origin)) is not None:
            # Benchmark descendants also belong to the platform (2026-11-08: 21 descendant goals,
            # e.g. #178, #180, #185, inherited eval seed repos). Keep chain guards above this branch.
            plat = s.scalars(select(Project).where(Project.protected).order_by(Project.id)).first()
            from agent_company.config import REPO_ROOT

            if src is not origin:
                context = f"goal #{origin.id}, which descends from benchmark goal #{src.id}"
            elif src.origin == "eval":
                context = f"benchmark goal #{origin.id}, which ran in a scratch repository"
            else:
                context = f"goal #{origin.id}, which ran in the scratch repository {origin.repo_path}"
            spawn = {"title": p.title,
                     "description": f"{p.description or p.title}\n\n(Raised by an agent during {context}; "
                                    "the change belongs to the Agent Company platform.)",
                     "repo": (plat.path if plat and plat.path else str(REPO_ROOT)),
                     "checks": list(plat.quality_commands or []) if plat else [],
                     "project_id": plat.id if plat else None, "parent": None, "created_by": origin.created_by,
                     "topic": GENERAL, "classification": ""}
        elif approve and kind != "project":
            spawn = {"title": p.title, "description": p.description or p.title,
                     "repo": origin.repo_path if origin else "",
                     "checks": list(origin.quality_commands) if origin else [], "project_id": p.project_id,
                     "parent": origin.id if origin else None, "created_by": origin.created_by if origin else None,
                     "topic": origin.topic if origin else GENERAL,
                     "classification": origin.classification if origin else ""}
        out = proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))
    if kind == "project":
        return _to_project(proposal_id, decided_by)
    if kind == "rule" and approve:
        return _to_rule(proposal_id, decided_by)
    if spawn is not None:
        try:
            gid = loop.create_goal(spawn["title"], spawn["description"], spawn["repo"],
                                   quality_commands=spawn["checks"], created_by=spawn["created_by"],
                                   project_id=spawn["project_id"], origin="proposal", parent_goal_id=spawn["parent"],
                                   topic=spawn["topic"], classification=spawn["classification"])
        except ValueError as e:  # e.g. the parent goal's budget is used (S04-R-09): recorded, not turned into a goal
            with session() as s:
                p = s.get(Proposal, proposal_id)
                if hold:  # the owner policy leaves it for a person
                    p.status, p.decided_by = "pending", ""  # the column's undecided value (not nullable)
                else:
                    p.status, p.decided_by = "rejected", f"{p.decided_by} ({e})"[:128]
                out = proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))
            if hold:
                log_event("proposal", f"proposal {proposal_id} stays pending: {e}", goal_id=out["goal_id"])
                return {**out, "held": str(e)}
            log_event("proposal", f"proposal {proposal_id} not turned into a goal: {e}", goal_id=out["goal_id"])
            return out
        with session() as s:
            s.get(Proposal, proposal_id).spawned_goal_id = gid
        out["spawned_goal_id"] = gid
        if spawn["project_id"]:
            post(spawn["project_id"], f"Proposal approved by {decided_by}: '{out['title']}' is now goal #{gid}.",
                 kind="proposal", goal_id=gid, topic=spawn["topic"])
        loop.enqueue(gid)
    elif out["project_id"]:
        post(out["project_id"], f"Proposal '{out['title']}' rejected by {out['decided_by']}.", kind="proposal")
    log_event("proposal", f"proposal {proposal_id} {out['status']} by {out['decided_by']}", goal_id=out["goal_id"])
    return out


def settle_proposals(goal_id: int, only: list[int] | None = None) -> None:
    """After a goal ends: announce its proposals, or let the GM decide them. `only`: just these (research proposals
    raised after the goal ended, S06-R-06)."""
    from agent_company import executors, router
    from agent_company.governance import notify

    with session() as s:
        pending = [(p.id, p.title, p.description) for p in s.scalars(
            select(Proposal).where(Proposal.goal_id == goal_id, Proposal.status == "pending"))
            if only is None or p.id in only]
        projects_ = {p.id for p in s.scalars(select(Proposal).where(Proposal.goal_id == goal_id,
                                                                     Proposal.kind.in_((*CASE_KINDS, "rule"))))}
        rules = {p.id for p in s.scalars(select(Proposal).where(Proposal.goal_id == goal_id, Proposal.kind == "rule"))}
        g = s.get(Goal, goal_id)
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
        goal_title = g.title if g else ""
    if not pending:
        return
    if settings().auto_approve_proposals:  # owner policy: held ones are posted and notified there
        from agent_company import ownerpolicy

        ownerpolicy.approve_pending(goal_id, ids=[x[0] for x in pending])
        return
    if settings().proposal_policy == "superiors" and gm is not None:  # the GM decides only unrestricted ones
        human = [x for x in pending if restricted(x[1], x[2]) or x[0] in projects_]
        pending = [x for x in pending if x not in human]
        for pid, title, why in human:
            reason = ("a project rule" if pid in rules else "a new product or project" if pid in projects_
                      else "it touches authority or safety controls")
            post_for_goal(goal_id, f"Proposal (needs a person: {reason}): {title}"
                          + (f"\n{why}" if why else ""), kind="proposal", proposal_id=pid)
        if human:
            notify(f"{len(human)} proposal(s) need a person's decision", goal_title, level="warning",
                   source="proposals", goal_id=goal_id, admin_only=True)
    if settings().proposal_policy != "superiors" or gm is None:
        for pid, title, why in pending:
            post_for_goal(goal_id, f"Proposal: {title}" + (f"\n{why}" if why else ""), kind="proposal",
                          proposal_id=pid)
        notify(f"{len(pending)} proposal(s) wait for a decision", goal_title, level="warning", source="proposals",
               goal_id=goal_id, admin_only=True)
        return
    from agent_company.learning import goal_level

    with session() as s:
        level = goal_level(s, s.get(Goal, goal_id))
    try:
        choice = router.resolve("general_manager", gm.model_pref, classification=level)
    except router.NotCleared:  # no model may read this goal's proposals: a person decides them
        for pid, title, why in pending:
            post_for_goal(goal_id, f"Proposal: {title}" + (f"\n{why}" if why else ""), kind="proposal",
                          proposal_id=pid)
        notify(f"{len(pending)} proposal(s) wait for a decision", goal_title, level="warning", source="proposals",
               goal_id=goal_id, admin_only=True)
        return
    for pid, title, why in pending:
        req = executors.Request(
            purpose="triage", role="general_manager", system=f"You are {gm.name}, the General Manager.",
            prompt=(f"An agent working on '{goal_title}' proposes follow-up work:\n{title}\n{why}\n\n"
                    "Approve it only if it clearly serves the project and is safe and small enough. "
                    "Answer with APPROVE or REJECT on the first line, then one sentence why."),
            data={"title": title}, agent_id=gm.id, goal_id=goal_id, permissions=list(gm.permissions or []),
            autonomy=gm.autonomy or "")
        res = executors.for_choice(choice, settings().command_timeout).run(req)
        from agent_company import usage

        usage.record(model_name=choice.name, kind=choice.kind, input_tokens=res.input_tokens,
                     output_tokens=res.output_tokens, cost=res.cost, duration=res.duration, ok=res.ok,
                     error=res.error, agent_id=gm.id, purpose="triage", goal_id=goal_id, res=res)
        verdict = res.text.strip().upper().startswith("APPROVE") if res.ok else False
        decide(pid, verdict, f"{gm.name} (GM)", by_human=False)
