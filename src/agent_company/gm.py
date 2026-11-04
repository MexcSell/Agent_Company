"""The General Manager as a Claude Code session, and the toolbox it runs the company with.

The owner talks only to the General Manager. By default the GM is Claude Code (router.DEFAULT_ROUTES); the
Projects page opens a Claude Code conversation in the project's folder with `system_prompt()` appended, so
the GM can:

- edit the project's Claude Code configuration (`.claude/agents`, `rules`, `skills`, `commands`, `hooks`,
  `settings.json`, `.mcp.json`) itself, or write the company catalog into it (`gm configure`);
- find and staff the agents a goal needs (`gm agents`, `gm create-agent`, `gm set-model`), sharing roles,
  skills, plugins, models and memory through the catalog;
- add a missing skill, plugin, rule or command to the management page (`gm add`), then use it;
- start the goal loop, where the agents plan, build, review and fix until the goal passes (`gm goal`);
- start a study visit, which rewrites an agent's definition file (`gm study`).

`python -m agent_company gm ...` is the toolbox. A goal it creates is queued in the database; the server's
goal queue picks it up within seconds (loop.GoalQueue's watcher), so it runs in the server, in a worktree,
through the quality gate and the approval like every other goal.

The GM can be switched on its agent card (Agents → General Manager → Model): to an API model, to a Claude
Code at another address (a `claude_code` model with a base URL), or to another agent (`agent:<id>`).
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import select

from agent_company.db import Agent, Goal, Project, session


def system_prompt(project: dict[str, Any]) -> str:
    return f"""You are the General Manager of the AI software company "Agent Company", working on the project
"{project['name']}" in {project['path']}. The owner talks only to you. Decide on your own what is needed and do it.

How you run the company (shell commands, run from any folder):
  python -m agent_company gm status                         agents, running goals, catalog sizes
  python -m agent_company gm agents                         every agent: id, role, skills, plugins, model
  python -m agent_company gm create-agent <role> --title "…" --skills "python=7,api=6" --plugins "code-review" --mission "…" [--writes]
  python -m agent_company gm set-model <agent id> "<model name>"   (see: gm models)
  python -m agent_company gm models                         the model catalog and role routes
  python -m agent_company gm catalog [--kind skill|plugin|rule|command|hook]
  python -m agent_company gm add <kind> <name> --description "…" --content "…"   add what is missing, then use it
  python -m agent_company gm mcp                            MCP servers in Tools & MCP
  python -m agent_company gm configure {project['id']}                  write the catalog into this project's .claude/
  python -m agent_company gm goal {project['id']} "<goal>" --details "…" [--check "uv run pytest -q"]
  python -m agent_company gm study [<agent id>]             one learning visit: rewrites an agent's definition

Rules:
- Small requests (questions, explanations, a quick config edit) you handle yourself here.
- Work that needs building, testing and review goes to the goal loop (`gm goal`): the agents plan, build,
  review and fix in a sandboxed worktree until the quality gate passes; the owner approves the merge.
- Keep the project's Claude Code configuration in .claude/ current: agents/ (one file per teammate, with
  model and effort), rules/, skills/, commands/, hooks/, settings.json, .mcp.json; specs go in
  .claude/specs/<slug>/ (requirements.md, spec.md, design.md, tasks.md, review.md, sa-review.md, decisions.md).
- Prefer existing agents, skills and plugins; when one is missing, add it with `gm create-agent` / `gm add`.
- Never deploy, never touch production servers, never print or commit secrets.
- Report briefly what you decided and did."""


def _project(pid: int) -> dict[str, Any]:
    from agent_company.projects import project_dict

    with session() as s:
        p = s.get(Project, pid)
        if p is None:
            raise SystemExit(f"no project {pid}")
        return project_dict(p)


def status() -> dict[str, Any]:
    from agent_company import catalog, mcp

    with session() as s:
        goals = [{"id": g.id, "title": g.title, "status": g.status, "project_id": g.project_id}
                 for g in s.scalars(select(Goal).where(Goal.status.in_(["queued", "running", "awaiting_approval"])))]
        agents = s.scalars(select(Agent).where(Agent.lifecycle == "active")).all()
        busy = [a.name for a in agents if a.status == "busy"]
    items = catalog.list_items()
    return {"agents": len(agents), "busy": busy, "goals": goals,
            "catalog": {k: sum(i["kind"] == k for i in items) for k in catalog.KINDS},
            "mcp_servers": len(mcp.list_servers()["servers"])}


def create_goal(project_id: int, title: str, details: str = "", checks: list[str] | None = None) -> int:
    """A goal for the project, queued for the server's goal queue (which picks it up within seconds)."""
    from agent_company import loop
    from agent_company.projects import post

    p = _project(project_id)
    gid = loop.create_goal(title[:200], details or title, repo_path=p["path"],
                           quality_commands=list(checks or p["quality_commands"]), project_id=project_id,
                           extract_checks=True)
    post(project_id, f"The General Manager started goal #{gid}: {title}", "agent", goal_id=gid,
         agent_id=p["manager_id"])
    return gid


def main(argv: list[str]) -> None:
    from agent_company import agentdefs, catalog, mcp, registry, router

    p = argparse.ArgumentParser(prog="agent_company gm", description="The General Manager's toolbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("agents")
    sub.add_parser("models")
    ca = sub.add_parser("create-agent")
    ca.add_argument("role")
    ca.add_argument("--title", default="")
    ca.add_argument("--skills", default="")
    ca.add_argument("--plugins", default="")
    ca.add_argument("--mission", default="")
    ca.add_argument("--room", default="")
    ca.add_argument("--writes", action="store_true")
    sm = sub.add_parser("set-model")
    sm.add_argument("agent_id", type=int)
    sm.add_argument("model")
    cat = sub.add_parser("catalog")
    cat.add_argument("--kind", default="")
    ad = sub.add_parser("add")
    ad.add_argument("kind", choices=catalog.KINDS)
    ad.add_argument("name")
    ad.add_argument("--description", default="")
    ad.add_argument("--content", default="")
    sub.add_parser("mcp")
    cf = sub.add_parser("configure")
    cf.add_argument("project_id", type=int)
    go = sub.add_parser("goal")
    go.add_argument("project_id", type=int)
    go.add_argument("title")
    go.add_argument("--details", default="")
    go.add_argument("--check", action="append", default=[])
    st = sub.add_parser("study")
    st.add_argument("agent_id", type=int, nargs="?")
    a = p.parse_args(argv)
    out: Any
    if a.cmd == "status":
        out = status()
    elif a.cmd == "agents":
        out = [{k: x[k] for k in ("id", "name", "role", "room", "skills", "plugins", "model_pref", "status",
                                  "lifecycle", "enabled")} for x in registry.list_agents() if x["lifecycle"] != "retired"]
    elif a.cmd == "models":
        out = {"models": [{k: m[k] for k in ("name", "kind", "available", "unavailable_reason")} for m in router.list_models()],
               "routes": router.list_routes()}
    elif a.cmd == "create-agent":
        skills = {k.strip(): int(v) for k, v in (x.split("=") for x in a.skills.split(",") if "=" in x)}
        out = registry.create_agent(a.role, a.title, skills, a.room, None, a.mission, a.writes)
        plugins = [x.strip() for x in a.plugins.split(",") if x.strip()]
        if plugins:
            catalog.ensure("plugin", plugins, "general manager")
            out = registry.update_agent(out["id"], {"plugins": plugins})
        agentdefs.sync(out["id"], "created by the General Manager")
    elif a.cmd == "set-model":
        out = registry.update_agent(a.agent_id, {"model_pref": a.model or None})
    elif a.cmd == "catalog":
        out = [{k: i[k] for k in ("kind", "name", "description")} for i in catalog.list_items(a.kind)]
    elif a.cmd == "add":
        out = catalog.add(a.kind, a.name, a.description, a.content, "general manager", "general manager", update=True)
    elif a.cmd == "mcp":
        out = [{k: s[k] for k in ("name", "slug", "transport", "status", "everyone")} | {"tools": [t["name"] for t in s["tools"] if t["allowed"]]}
               for s in mcp.list_servers()["servers"]]
    elif a.cmd == "configure":
        proj = _project(a.project_id)
        from pathlib import Path

        with session() as s:
            ids = list(s.scalars(select(Agent.id).where(Agent.enabled, Agent.lifecycle == "active", Agent.parent_id.is_(None))))
        out = {"written": catalog.export_claude(Path(proj["path"]), ids)}
    elif a.cmd == "goal":
        out = {"goal_id": create_goal(a.project_id, a.title, a.details, a.check)}
    else:
        out = agentdefs.study(a.agent_id)
    print(json.dumps(out, indent=2, default=str))
