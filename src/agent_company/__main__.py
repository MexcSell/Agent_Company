"""Command line.

  python -m agent_company "<request>"            classic four-agent CrewAI pipeline (output/)
  python -m agent_company serve                  API + dashboard on http://127.0.0.1:4900
  python -m agent_company goal "<goal>" [...]    run one goal through the circular loop
  python -m agent_company crawl <url>            collect a site (and its linked pages)
  python -m agent_company models                 model catalog, availability and routes
  python -m agent_company agents                 agents by room
  python -m agent_company approve <goal id>      merge a goal that passed the gate
"""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

COMMANDS = {"serve", "goal", "crawl", "models", "agents", "approve", "crew", "rotate"}


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    load_dotenv()
    if not argv:
        raise SystemExit(__doc__)
    if argv[0] not in COMMANDS:
        argv = ["crew", *argv]
    p = argparse.ArgumentParser(prog="agent_company")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("crew").add_argument("request", nargs="+")
    sp = sub.add_parser("serve")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    g = sub.add_parser("goal")
    g.add_argument("title")
    g.add_argument("--description", default="")
    g.add_argument("--repo", default="", help="existing git repository to work on (default: new workspace)")
    g.add_argument("--url", action="append", default=[], help="site to research first (links are followed)")
    g.add_argument("--check", action="append", default=[], help="quality command, e.g. 'uv run pytest -q'")
    g.add_argument("--max-iterations", type=int)
    c = sub.add_parser("crawl")
    c.add_argument("url")
    c.add_argument("--depth", type=int, default=1)
    c.add_argument("--max-pages", type=int, default=20)
    sub.add_parser("models")
    sub.add_parser("agents")
    sub.add_parser("rotate")
    a = sub.add_parser("approve")
    a.add_argument("goal_id", type=int)
    args = p.parse_args(argv)

    if args.cmd == "crew":
        from agent_company.crew import build_crew

        result = build_crew().kickoff(inputs={"request": " ".join(args.request)})
        print(result.raw)
        return
    if args.cmd == "serve":
        import uvicorn

        from agent_company.api import create_app
        from agent_company.config import settings

        uvicorn.run(create_app(), host=args.host or settings().host, port=args.port or settings().port)
        return

    from agent_company.api import bootstrap

    bootstrap()
    if args.cmd == "goal":
        from agent_company import loop

        gid = loop.create_goal(args.title, args.description, args.repo, args.url, args.check,
                               args.max_iterations)
        out = loop.run_goal(gid)
        g = loop._goal(gid)
        print(f"Goal {gid}: {out['status']} after {g.iteration} iteration(s)\n{g.result}\n"
              f"Branch {g.branch} in {g.worktree}")
        if out["status"] == "awaiting_approval":
            print(f"Approve with: python -m agent_company approve {gid}")
    elif args.cmd == "crawl":
        from agent_company import research

        print(json.dumps(research.collect(args.url, args.depth, args.max_pages), indent=2))
    elif args.cmd == "rotate":
        from agent_company import research

        print(json.dumps(research.rotation_tick(), indent=2, default=str))
    elif args.cmd == "models":
        from agent_company import router

        for m in router.list_models():
            state = "ok" if m["available"] else m["unavailable_reason"]
            print(f"{m['name']:<28} {m['kind']:<12} {m['model_id']:<34} {state}")
        print("\nRoutes (role -> model -> what runs now):")
        for role, name in router.list_routes().items():
            ch = router.resolve(role)
            print(f"  {role:<22} {name:<24} -> {ch.name} ({ch.reason})")
    elif args.cmd == "agents":
        from agent_company import registry

        for a in registry.list_agents():
            flag = "" if a["enabled"] else "  [disabled]"
            print(f"{a['id']:>3} {a['name']:<34} {a['role']:<22} {a['room']:<24} L{a['level']} "
                  f"{a['status']}{flag}")
    elif args.cmd == "approve":
        from agent_company import loop

        print(json.dumps(loop.approve(args.goal_id), indent=2))


if __name__ == "__main__":
    main()
