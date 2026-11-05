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
"""

from __future__ import annotations

import json
import mimetypes
import re
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
    return {"id": p.id, "name": p.name, "path": p.path, "github_url": p.github_url, "description": p.description,
            "quality_commands": p.quality_commands, "protected": p.protected, "manager_id": p.manager_id,
            "classification": p.classification or "internal",
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
    from agent_company.learning import CLASSIFICATIONS

    allowed = {"name", "github_url", "description", "quality_commands", "classification"}
    bad = set(changes) - allowed
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    if "classification" in changes and changes["classification"] not in CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {', '.join(CLASSIFICATIONS)}")
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        if p.protected and "name" in changes:
            raise ValueError("the platform project cannot be renamed")
        _check(changes.get("name", p.name), "", changes.get("github_url", p.github_url),
               changes.get("quality_commands", p.quality_commands))
        old = p.classification or "internal"
        for k, v in changes.items():
            setattr(p, k, v.strip() if isinstance(v, str) else v)
        s.flush()
        out = project_dict(p)
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
    """Agents that worked on the project's goals (or exactly `only`, a topic's agents), busy ones first."""
    from agent_company.registry import agent_dict

    with session() as s:
        if only is not None:
            ids = set(only)
        else:
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
        level = p.classification if p else "internal"
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
        try:  # only a model cleared for the project's classification (S14-R-05) reads the conversation
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
    from agent_company.learning import check_level, highest

    level = highest(check_level(classification) if classification else None, p.classification)
    if files:
        with session() as s:
            for u in s.scalars(select(Upload).where(Upload.id.in_([f["id"] for f in files]))):
                u.classification = highest(u.classification, level)
    mid = post(p.id, content, "user", "command", user_id=user["id"],
               attachments=[{k: f[k] for k in ("id", "name", "content_type", "size")} for f in files],
               topic=topic, author=user["name"], **({"classification": level} if level != "internal" else {}))
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
    title = content.splitlines()[0][:200] if content else f"Look at {files[0]['name']}"
    if len(title) < 3:
        title = f"Order: {title}"
    gid = loop.create_goal(title, content or title, repo_path=p.path, quality_commands=list(p.quality_commands),
                           created_by=user["id"], project_id=p.id, attachments=files,
                           extract_checks=user["is_admin"], topic=topic, classification=level)
    ahead = loop.lane_ahead(gid)
    reply(f"Order received as goal #{gid}. " + (f"It starts when goal #{ahead} of this topic is done."
                                               if ahead else "Planning now."),
          author_type="agent", goal_id=gid, agent_id=p.manager_id)
    loop.enqueue(gid)
    return {"message_id": mid, "goal_id": gid}


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
    out = (["Open blockers:", *blockers, ""] if blockers else []) + (lines or ["No goals yet."])
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
                        r"|security|approval|emergency|audit|credential|secret|access control|sudo)s?\b")


def restricted(title: str, description: str = "") -> bool:
    return bool(RESTRICTED.search(f"{title}\n{description}"))


def decide(proposal_id: int, approve: bool, decided_by: str, by_human: bool = True) -> dict[str, Any]:
    """Approve (spawning a goal, within the loop guards) or reject a proposal. An agent (`by_human=False`)
    cannot approve a restricted proposal."""
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
        if approve and not by_human and restricted(p.title, p.description):
            raise PermissionError("this proposal touches authority, budgets or safety controls: a person decides it")
        if approve and origin is not None and _spawned_from(s, _root(s, origin).id) >= limit:
            approve, note = False, f" (limit of {limit} proposal goals reached)"
        elif approve and origin is not None and _depth(s, origin) >= settings().max_goal_depth:
            approve, note = False, f" (goal chain depth {settings().max_goal_depth} reached)"  # S04-R-08
        p.status, p.decided_by = ("approved" if approve else "rejected"), decided_by + note
        if approve:
            spawn = {"title": p.title, "description": p.description or p.title,
                     "repo": origin.repo_path if origin else "",
                     "checks": list(origin.quality_commands) if origin else [], "project_id": p.project_id,
                     "parent": origin.id if origin else None, "created_by": origin.created_by if origin else None,
                     "topic": origin.topic if origin else GENERAL,
                     "classification": origin.classification if origin else ""}
        out = proposal_dict(p, dict(s.execute(select(Agent.id, Agent.name)).all()))
    if spawn is not None:
        gid = loop.create_goal(spawn["title"], spawn["description"], spawn["repo"], quality_commands=spawn["checks"],
                               created_by=spawn["created_by"], project_id=spawn["project_id"], origin="proposal",
                               parent_goal_id=spawn["parent"], topic=spawn["topic"],
                               classification=spawn["classification"])
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
    if settings().proposal_policy == "superiors" and gm is not None:  # the GM decides only unrestricted ones
        human = [x for x in pending if restricted(x[1], x[2])]
        pending = [x for x in pending if not restricted(x[1], x[2])]
        for pid, title, why in human:
            post_for_goal(goal_id, f"Proposal (needs a person: it touches authority or safety controls): {title}"
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
            data={"title": title})
        res = executors.for_choice(choice, settings().command_timeout).run(req)
        verdict = res.text.strip().upper().startswith("APPROVE") if res.ok else False
        decide(pid, verdict, f"{gm.name} (GM)", by_human=False)
