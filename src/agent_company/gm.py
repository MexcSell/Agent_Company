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
- start a study visit, which proposes a definition candidate an administrator applies after seeing its diff
  (`gm study`, G-R-19);
- record a hypothesis to test and move it along (`gm hypothesis`, `gm hypotheses`, `gm hypothesis-status`); as an
  agent it cannot mark one supported without a recorded test result (hypotheses.py, ASEAO07 S07-R-03);
- write down the project's requirements as drafts a person approves, and name them on goals (`gm requirement`,
  `gm requirements --trace`, `gm goal --requirement`; requirements.py, ASEAO08 S08-R-01);
- read a decision report and review a decision it did not take (`gm decision`, `gm decision-review`; decisions.py,
  ASEAO07 S07-R-01).

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
from agent_company.workflow import ACTIVE


def _cli() -> str:
    """The toolbox command, runnable from any folder (this service's Python and its .env)."""
    import shlex
    import sys

    from agent_company.config import REPO_ROOT

    return f"cd {shlex.quote(str(REPO_ROOT))} && {shlex.quote(sys.executable)} -m agent_company gm"


def system_prompt(project: dict[str, Any]) -> str:
    gm = _cli()
    return f"""You are the General Manager of the AI software company "Agent Company", working on the project
"{project['name']}" in {project['path']}. The owner talks only to you. Decide on your own what is needed and do it.

How you run the company (shell commands; GM below stands for: {gm}):
  GM status                         agents, running goals, catalog sizes
  GM agents                         every agent: id, role, skills, plugins, model
  GM create-agent <role> --title "…" --skills "python=7,api=6" --plugins "code-review" --mission "…" [--writes]
  GM set-model <agent id> "<model name>"   (see: gm models)
  GM set-fallback <role> "<model>,<model>"   models that take a role's work when its model hits a usage limit
  GM models                         the model catalog and role routes
  GM catalog [--kind skill|plugin|rule|command|hook]
  GM add <kind> <name> --description "…" --content "…"   add what is missing, then use it
  GM mcp                            MCP servers in Tools & MCP
  GM configure {project['id']}                  write the catalog into this project's .claude/
  GM goal {project['id']} "<goal>" --details "…" [--check "uv run pytest -q"] [--requirement REQ-001]
       [--kind feature|bugfix|refactor|research|docs]   the workflow template (default: the planner picks)
  GM team <goal id> [--add <role> --skills "a,b" | --release <agent id> | --back <agent id>] --reason "…"
  GM requirement {project['id']} "<text>" [--kind functional|non_functional|story|use_case] [--accept "…"]   a draft
     (a story: "As a <actor>, I want <what>, so that <benefit>"; a use case: lines "Actors:", "Preconditions:",
     "Main flow:" with numbered steps, "Alternatives:", "Postconditions:")
  GM requirements {project['id']} [--trace]   the project's requirements; --trace: implemented, tested, merged
  GM study [<agent id>]             one learning visit: a definition candidate an administrator applies
  GM schedule {project['id']} "<goal>" --every "daily 06:00" [--details "…"] [--check "…"] [--kind …]   a recurring goal (UTC;
       hourly | daily HH:MM | weekly <mon..sun> HH:MM); GM schedules {project['id']}
  GM import-skills <folder> [--only "a,b"] [--enable]   <folder>/<name>/SKILL.md skills into the catalog (screened)
  GM hypothesis "<statement>" --reason "…" --test "how to test it" --expect "…" [--project {project['id']}]
  GM hypotheses [--status proposed,testing]      hypotheses and their evidence
  GM hypothesis-status <id> <investigating|testing|rejected|inconclusive|archived> [--note "…"]
  GM decision <id>                  a decision report: options, criteria, evidence by class, limitations, reviews
  GM decision-review <id> <agree|concerns|disagree> --note "…"   review a decision you did not take
  GM org-propose "<title>" --changes '[{{"op": "update_room", "key": "…", "changes": {{"lead_agent_id": 3}}}}]' --reason "…" [--impact "…"] [--evidence "…"] [--migration "…"]
                                    propose an organisation change (rooms, leads, reporting lines); a person applies it

Rules:
- Small requests (questions, explanations, a quick config edit) you handle yourself here.
- Work that needs building, testing and review goes to the goal loop (`gm goal`): the agents plan, build,
  review and fix in a sandboxed worktree until the quality gate passes; the owner approves the merge.
- Keep the project's Claude Code configuration in .claude/ current: agents/ (one file per teammate, with
  model and effort), rules/, skills/, commands/, hooks/, settings.json, .mcp.json; specs go in
  .claude/specs/<slug>/ (requirements.md, spec.md, design.md, tasks.md, review.md, sa-review.md, decisions.md).
- Prefer existing agents, skills and plugins; when one is missing, add it with `gm create-agent` / `gm add`.
- Label what you know: facts with their source, hypotheses as hypotheses (`gm hypothesis`), assumptions as
  assumptions. A hypothesis is supported only by a recorded test result, never by your own judgement.
- A decision report names the question, the options, the criteria, the evidence with its class, the limitations
  and why the confidence is what it is. A recommendation is not authorisation: only an approved decision is.
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
                 for g in s.scalars(select(Goal).where(Goal.status.in_([*ACTIVE, "awaiting_approval"])))]
        agents = s.scalars(select(Agent).where(Agent.lifecycle == "active")).all()
        busy = [a.name for a in agents if a.status == "busy"]
    items = catalog.list_items()
    return {"agents": len(agents), "busy": busy, "goals": goals,
            "catalog": {k: sum(i["kind"] == k for i in items) for k in catalog.KINDS},
            "mcp_servers": len(mcp.list_servers()["servers"])}


def create_goal(project_id: int, title: str, details: str = "", checks: list[str] | None = None,
                requirement_keys: list[str] | None = None, kind: str = "") -> int:
    """A goal for the project, queued for the server's goal queue (which picks it up within seconds)."""
    from agent_company import loop
    from agent_company.projects import post

    p = _project(project_id)
    gid = loop.create_goal(title[:200], details or title, repo_path=p["path"],
                           quality_commands=list(checks or p["quality_commands"]), project_id=project_id,
                           extract_checks=True, requirement_keys=requirement_keys, kind=kind)
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
    fb = sub.add_parser("set-fallback")
    fb.add_argument("role")
    fb.add_argument("models", help="comma-separated model names, in order; '' clears")
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
    go.add_argument("--requirement", action="append", default=[])
    go.add_argument("--kind", default="", choices=["", "feature", "bugfix", "refactor", "research", "docs"])
    tm = sub.add_parser("team")
    tm.add_argument("goal_id", type=int)
    tm.add_argument("--add", default="", help="a role to add to the goal's team")
    tm.add_argument("--skills", default="", help="comma-separated skills of the added role")
    tm.add_argument("--release", type=int, default=None, help="the id of an agent to release")
    tm.add_argument("--back", type=int, default=None, help="the id of a released agent to take back")
    tm.add_argument("--reason", default="")
    rq = sub.add_parser("requirement")
    rq.add_argument("project_id", type=int)
    rq.add_argument("text")
    rq.add_argument("--kind", default="functional")
    rq.add_argument("--accept", action="append", default=[])
    rq.add_argument("--subject", default="")
    rl = sub.add_parser("requirements")
    rl.add_argument("project_id", type=int)
    rl.add_argument("--trace", action="store_true")
    st = sub.add_parser("study")
    st.add_argument("agent_id", type=int, nargs="?")
    hy = sub.add_parser("hypothesis")
    hy.add_argument("statement")
    hy.add_argument("--reason", default="")
    hy.add_argument("--test", default="")
    hy.add_argument("--expect", default="")
    hy.add_argument("--project", type=int, default=None)
    hl = sub.add_parser("hypotheses")
    hl.add_argument("--status", default="")
    hs = sub.add_parser("hypothesis-status")
    hs.add_argument("hypothesis_id", type=int)
    hs.add_argument("status")
    hs.add_argument("--note", default="")
    im = sub.add_parser("import-skills", help="FW-R-05: import <folder>/<name>/SKILL.md skills into the catalog")
    im.add_argument("folder")
    im.add_argument("--enable", action="store_true", help="enable them at once (default: disabled until chosen)")
    im.add_argument("--only", default="", help="comma-separated skill names")
    sc = sub.add_parser("schedule", help="FW-R-04: order a goal on a schedule")
    sc.add_argument("project_id", type=int)
    sc.add_argument("title")
    sc.add_argument("--every", required=True, help="hourly | 'daily HH:MM' | 'weekly <mon..sun> HH:MM' (UTC)")
    sc.add_argument("--details", default="")
    sc.add_argument("--check", action="append", default=[])
    sc.add_argument("--kind", default="", choices=["", "feature", "bugfix", "refactor", "research", "docs"])
    sl = sub.add_parser("schedules")
    sl.add_argument("project_id", type=int, nargs="?")
    op = sub.add_parser("org-propose", help="UI-ORG-02: propose an organisation change; a person approves it")
    op.add_argument("title")
    op.add_argument("--changes", required=True, help='JSON list: [{"op": "create_room"|"update_room"|"update_agent", ...}]')
    op.add_argument("--reason", required=True)
    op.add_argument("--impact", default="")
    op.add_argument("--evidence", action="append", default=[])
    op.add_argument("--migration", default="")
    dr = sub.add_parser("decision")
    dr.add_argument("decision_id", type=int)
    dv = sub.add_parser("decision-review")
    dv.add_argument("decision_id", type=int)
    dv.add_argument("verdict", choices=["agree", "concerns", "disagree"])
    dv.add_argument("--note", default="")
    a = p.parse_args(argv)
    out: Any
    if a.cmd == "status":
        out = status()
    elif a.cmd == "agents":
        out = [{k: x[k] for k in ("id", "name", "role", "room", "skills", "plugins", "model_pref", "status",
                                  "lifecycle", "enabled")} for x in registry.list_agents() if x["lifecycle"] != "retired"]
    elif a.cmd == "models":
        out = {"models": [{k: m[k] for k in ("name", "kind", "available", "unavailable_reason", "limited_until")}
                          for m in router.list_models()],
               "routes": router.list_routes(), "fallbacks": {k: v for k, v in router.list_fallbacks().items() if v}}
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
    elif a.cmd == "set-fallback":
        out = {"role": a.role, "fallback": router.set_fallback(a.role, a.models.split(","))}
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
        out = {"goal_id": create_goal(a.project_id, a.title, a.details, a.check, a.requirement, a.kind)}
    elif a.cmd == "team":  # S04-R-11: resize a goal's team; without an option, its history
        from agent_company import teams

        if a.release is not None:
            out = teams.release(a.goal_id, a.release, a.reason, "general manager")
        elif a.back is not None or a.add:
            out = teams.add(a.goal_id, a.add, [x.strip() for x in a.skills.split(",") if x.strip()], a.reason,
                            "general manager", agent_id=a.back)
        else:
            out = teams.history(a.goal_id)
    elif a.cmd in ("requirement", "requirements"):
        from agent_company import requirements

        _project(a.project_id)
        if a.cmd == "requirement":  # a draft: a person approves it (S08-FR-10)
            out = requirements.create(a.project_id, a.text, a.kind, a.accept, subject=a.subject,
                                      source="general manager", actor="agent:general_manager")
        else:
            out = requirements.traceability(a.project_id) if a.trace else requirements.list_requirements(a.project_id)
    elif a.cmd in ("hypothesis", "hypotheses", "hypothesis-status"):
        from agent_company import hypotheses

        actor = "agent:general_manager"  # an agent: no "supported" without a recorded test result
        if a.cmd == "hypothesis":
            out = hypotheses.create(a.statement, a.reason, actor=actor, test_method=a.test, expected_outcome=a.expect,
                                    project_id=a.project)
        elif a.cmd == "hypotheses":
            out = hypotheses.list_hypotheses(a.status)
        else:
            out = hypotheses.set_status(a.hypothesis_id, a.status, actor, a.note)
    elif a.cmd == "import-skills":
        out = catalog.import_skills(a.folder, a.enable, [x for x in a.only.split(",") if x.strip()], actor="gm")
    elif a.cmd in ("schedule", "schedules"):
        from agent_company import schedules

        if a.cmd == "schedule":
            _project(a.project_id)
            out = schedules.create(a.project_id, a.title, a.every, a.details, a.check, a.kind)
        else:
            out = schedules.list_schedules(a.project_id)
    elif a.cmd == "org-propose":
        from agent_company import organization

        out = organization.propose(a.title, json.loads(a.changes), a.reason, a.impact, a.evidence, a.migration,
                                   "agent:general_manager")
    elif a.cmd == "decision":
        from agent_company import decisions

        print(decisions.report(a.decision_id)["markdown"])
        return
    elif a.cmd == "decision-review":
        from agent_company import decisions

        with session() as s:
            gm_id = s.scalars(select(Agent.id).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()
        out = decisions.review(a.decision_id, "agent:general_manager", a.verdict, a.note, agent_id=gm_id)
    else:
        out = agentdefs.study(a.agent_id)
        if out.get("candidate_id"):
            c = agentdefs.candidate(out["candidate_id"])
            print(f"definition candidate #{c['id']} for {c['agent_name']} (agent {c['agent_id']}), model {c['model']}, "
                  f"status {c['status']}: an administrator applies or discards it after reading the diff")
            print(c["diff"] or "(no change to the file)")
            return
        if out.get("studied") and not out.get("changed"):
            print(f"{out['studied']}: no candidate ({out.get('detail', 'no change')})")
            return
    print(json.dumps(out, indent=2, default=str))
