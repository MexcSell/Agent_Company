"""Command line.

  python -m agent_company "<request>"            classic four-agent CrewAI pipeline (output/)
  python -m agent_company serve                  API + dashboard on http://127.0.0.1:PORT (nginx in front: transport.py)
  python -m agent_company goal "<goal>" [...]    run one goal through the circular loop
  python -m agent_company crawl <url>            collect a site (and its linked pages)
  python -m agent_company models                 model catalog, availability and routes
  python -m agent_company agents                 agents by room
  python -m agent_company approve <goal id>      merge a goal that passed the gate
  python -m agent_company stop [--level E0..E5 --scope X] [--lift|--status]   emergency stop (SPEC-14 S14-R-01)
  python -m agent_company host status|cleanup|launch <name>|...   Docker and LXD VMs on this host
  python -m agent_company gm status|agents|create-agent|add|goal|configure|study ...   the GM's toolbox (gm.py)
"""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

COMMANDS = {"serve", "goal", "crawl", "models", "agents", "approve", "crew", "rotate", "eval", "learn", "supervise", "host", "gm",
            "stop", "improve", "requirements", "sbom", "strategies", "marketplace"}


def _marketplace(args: argparse.Namespace) -> None:
    """REQ-002: the command line is the server's operator, an administrator; every decision is audited."""
    from pathlib import Path

    from agent_company import governance, marketplace

    act, actor = args.action, "cli"
    try:
        if act == "list":
            for i in marketplace.list_imports(args.status):
                print(f"#{i['id']:<4} {i['status']:<10} {i['manifest'].get('role', ''):<12} {i['licence']:<12} "
                      f"{i['source_sha'][:12]:<12} {i['source']}")
            return
        if act != "import" and not (args.target or "").isdigit():
            raise SystemExit(f"marketplace {act} needs an import id")
        if act == "show":
            print(json.dumps(marketplace.get(int(args.target)), indent=2))
            return
        if act == "import":
            if not args.target:
                raise SystemExit("marketplace import needs a repository URL or a package file")
            path = Path(args.target)
            if "://" not in args.target and path.is_file():
                out = marketplace.fetch_upload(path, path.name, actor)
            else:
                out = marketplace.fetch_url(args.target, actor)
            if out["status"] == "converted":
                out = marketplace.create_candidate(out["id"])
            governance.audit(actor, "marketplace_import", out["id"],
                             after={"source": out["source"], "sha": out["source_sha"], "status": out["status"]})
        elif act == "approve":
            grant = [g for x in args.grant for g in x.split(",") if g] if args.grant else None
            out = marketplace.approve(int(args.target), actor, grant, admin=True, autonomy=args.autonomy,
                                      accept_licence=args.accept_licence, acknowledge=args.acknowledge)
            governance.audit(actor, "marketplace_approve", out["id"],
                             after={"permissions": out["ceiling"].get("permissions"), "requested": grant})
        else:
            out = marketplace.reject(int(args.target), actor, args.reason)
            governance.audit(actor, "marketplace_reject", out["id"], after={"reason": args.reason[:300]})
    except (ValueError, KeyError, PermissionError) as e:
        raise SystemExit(f"marketplace {act}: {e}") from e
    print(json.dumps({k: out[k] for k in ("id", "status", "source", "source_sha", "licence", "ceiling", "eval",
                                          "agent_id", "error")}, indent=2))


def _host(args: argparse.Namespace) -> None:
    from agent_company import containers, governance

    act = args.action
    if act in ("launch", "start", "stop", "restart", "delete") and not args.name:
        raise SystemExit(f"host {act} needs an instance name")
    try:
        if act == "status":
            print(json.dumps({k: v for k, v in containers.status().items() if k != "jobs"}, indent=2))
            return
        if act == "cleanup":
            out = containers.cleanup(args.yes, args.all_images, args.volumes, args.older_than)
            job = out.pop("job", None)
            print(json.dumps(out, indent=2))
            if not job:
                print("Dry run. Re-run with --yes to remove the above.")
                return
        elif act == "install-docker":
            job = containers.install_docker()
        elif act.startswith("docker-"):
            job = containers.docker_service(act.split("-", 1)[1])
        elif act == "install-lxd":
            job = containers.install_lxd()
        elif act == "launch":
            job = containers.launch(args.name, args.image, not args.container, args.cpus, args.memory, args.disk)
        else:
            job = containers.instance_action(args.name, act)
    except containers.HostError as e:
        raise SystemExit(str(e)) from e
    governance.audit("cli", f"host_{act.replace('-', '_')}", args.name or "", after={"job": job["id"]},
                     severity="high")
    done = containers.wait(job["id"])
    print(done["output"].rstrip())
    print(f"{done['kind']}: {done['status']}")
    if done["status"] != "done":
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    from agent_company.config import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env")  # also when run from another folder (the General Manager's toolbox)
    load_dotenv()
    if not argv:
        raise SystemExit(__doc__)
    if argv[0] not in COMMANDS:
        argv = ["crew", *argv]
    if argv[0] == "gm":  # the General Manager's toolbox has its own parser
        from agent_company import gm
        from agent_company.api import bootstrap

        bootstrap()
        gm.main(argv[1:])
        return
    p = argparse.ArgumentParser(prog="agent_company")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("crew").add_argument("request", nargs="+")
    sp = sub.add_parser("serve")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sv = sub.add_parser("supervise", help="serve and restart safely when the code changes")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    g = sub.add_parser("goal")
    g.add_argument("title")
    g.add_argument("--description", default="")
    g.add_argument("--repo", default="", help="existing git repository to work on (default: new workspace)")
    g.add_argument("--url", action="append", default=[], help="site to research first (links are followed)")
    g.add_argument("--check", action="append", default=[], help="quality command, e.g. 'uv run pytest -q'")
    g.add_argument("--max-iterations", type=int)
    g.add_argument("--allow", action="append", default=[], help="path glob the change may touch (repeatable)")
    g.add_argument("--protect-tests", action="store_true", help="refuse changes to existing test files")
    g.add_argument("--permit", action="append", default=[], choices=["dependencies", "config", "skip_tests"],
                   help="lift a change boundary: new dependencies, CI/.env edits, skipped tests (repeatable)")
    g.add_argument("--kind", default="", choices=["", "feature", "bugfix", "refactor", "research", "docs"],
                   help="workflow template (default: the planner picks one)")
    st = sub.add_parser("stop", help="emergency stop: needs no model and no running server")
    st.add_argument("--level", default="E5", choices=["E0", "E1", "E2", "E3", "E4", "E5"],
                    help="E0 executor, E1 agent, E2 goal, E3 project, E4 room, E5 everything (default)")
    st.add_argument("--scope", default="all", help="executor kind, agent id, goal id, project id or room key")
    st.add_argument("--reason", default="stopped from the command line")
    st.add_argument("--lift", action="store_true", help="lift the stop instead (administrators' decision)")
    st.add_argument("--status", action="store_true", help="show the active stops")
    c = sub.add_parser("crawl")
    c.add_argument("url")
    c.add_argument("--depth", type=int, default=1)
    c.add_argument("--max-pages", type=int, default=20)
    sub.add_parser("models")
    sub.add_parser("agents")
    sub.add_parser("rotate")
    ev = sub.add_parser("eval", help="run a benchmark suite, or compare two labels")
    ev.add_argument("--suite", default="smoke", help="suite name in evals/ or a JSON file")
    ev.add_argument("--label", help="name for this run's results")
    ev.add_argument("--repeats", type=int, default=3)
    ev.add_argument("--tasks", nargs="*", help="only these task names")
    ev.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ev.add_argument("--model", help="benchmark this catalog model for --role (S13-R-06); label defaults to model:<name>")
    ev.add_argument("--role", default="developer", help="the role --model stands in for")
    ev.add_argument("--room", help="a room trial (G-R-04): staff this gated room's agents in the suite's goals")
    ev.add_argument("--gate", metavar="ROOM", help="show the enablement gate of this gated room (G-R-04) and exit")
    ev.add_argument("--review", action="store_true", help="run the scheduled benchmark review now (G-R-14; needs "
                    "benchmark_budget_usd in Settings) and exit")
    le = sub.add_parser("learn", help="capability gaps, learning plans and experiments (SPEC-13)")
    le.add_argument("action", choices=["gaps", "raise", "plans", "plan", "start", "cancel", "experiments", "metrics",
                                       "retrieval", "generate", "suites", "approve"],
                    help="retrieval: learn the ranking procedure per query class (SE-R-21); generate: exercises "
                         "written by the instructor, verified by the harness (S13-R-02)")
    le.add_argument("args", nargs="*", help="plan: <agent id> <capability> [final suite] [practice suite]; "
                                            "start/cancel: <plan id>; generate: <skill> [practice|final] [count]; "
                                            "approve: <generated final suite>")
    sb = sub.add_parser("sbom", help="CycloneDX SBOM of a repository's dependencies at a commit (S08-R-05)")
    sb.add_argument("repo", nargs="?", default=".", help="git repository (default: the current directory)")
    sb.add_argument("--rev", default="HEAD")
    sb.add_argument("--out", default="", help="write to this file instead of stdout")
    st = sub.add_parser("strategies", help="self-evaluation of the workflow templates (SPEC-04 S04-R-13)")
    st.add_argument("action", choices=["evaluate", "recommend"])
    st.add_argument("kind", nargs="?", default="", help="goal kind (recommend: required)")
    a = sub.add_parser("approve")
    a.add_argument("goal_id", type=int)
    rq = sub.add_parser("requirements", help="project requirements and traceability (ASEAO08 S08-R-01)")
    rq.add_argument("action", choices=["list", "trace", "import"])
    rq.add_argument("--project", type=int, default=None, help="project id (default: the platform project)")
    im = sub.add_parser("improve", help="verified self-evolution: improvement contracts for the platform's code")
    im.add_argument("action", choices=["list", "weaknesses", "benchmark", "create", "start", "deploy", "rollback",
                                       "cancel", "advance"])
    im.add_argument("id", nargs="?", help="contract id (start, deploy, rollback, cancel); benchmark name (benchmark)")
    im.add_argument("--title", default="")
    im.add_argument("--objective", default="")
    im.add_argument("--component", default="memory")
    im.add_argument("--benchmark", default="retrieval")
    im.add_argument("--layer", default="code")
    im.add_argument("--kind", default="algorithmic")
    im.add_argument("--reason", default="")
    mk = sub.add_parser("marketplace", help="import external agents as candidates an administrator decides (REQ-002)")
    mk.add_argument("action", choices=["import", "list", "show", "approve", "reject"])
    mk.add_argument("target", nargs="?", help="import: a repository URL or a .zip/.tar.gz package; else an import id")
    mk.add_argument("--grant", action="append", default=[], help="approve: permissions to grant (repeatable or "
                    "comma-separated); clamped by the import's ceiling; default: the whole ceiling")
    mk.add_argument("--autonomy", default="", help="approve: G0..G2, never above the ceiling")
    mk.add_argument("--accept-licence", action="store_true", help="approve: accept an unknown licence")
    mk.add_argument("--acknowledge", action="store_true", help="approve: acknowledge the screen's findings")
    mk.add_argument("--reason", default="", help="reject: why")
    mk.add_argument("--status", default="", help="list: only this status")
    h = sub.add_parser("host", help="Docker and LXD virtual machines on this host")
    h.add_argument("action", choices=["status", "install-docker", "docker-start", "docker-stop", "docker-restart",
                                      "cleanup", "install-lxd", "launch", "start", "stop", "restart", "delete"])
    h.add_argument("name", nargs="?", help="instance name (launch, start, stop, restart, delete)")
    h.add_argument("--image", default="ubuntu:24.04")
    h.add_argument("--container", action="store_true", help="launch a system container instead of a VM")
    h.add_argument("--cpus", type=int, default=2)
    h.add_argument("--memory", default="4GiB")
    h.add_argument("--disk", default="")
    h.add_argument("--yes", action="store_true", help="cleanup: remove, not just list")
    h.add_argument("--all-images", action="store_true")
    h.add_argument("--volumes", action="store_true")
    h.add_argument("--older-than", default="24h")
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

        import os

        candidate = os.environ.get("AC_CANDIDATE") == "1"  # a supervisor's trial boot: no background work
        uvicorn.run(create_app(start_background=not candidate), host=args.host or settings().host,
                    port=args.port or settings().port)
        return
    if args.cmd == "supervise":
        from agent_company.supervisor import Supervisor

        Supervisor(args.port, args.host).run()
        return

    if args.cmd == "stop":  # writes the database directly: works while the server or its workers are stuck
        import getpass

        from agent_company import governance

        actor = f"cli:{getpass.getuser()}"
        if args.status:
            out: dict = {"E5": governance.emergency(), "stops": governance.stops()}
        elif args.lift:
            out = governance.lift(args.level, args.scope, actor)
        else:
            out = governance.stop(args.level, args.scope, actor, args.reason)
        print(json.dumps(out, indent=2, default=str))
        return
    if args.cmd == "host":  # needs no model: runs before bootstrap
        _host(args)
        return

    from agent_company.api import bootstrap

    bootstrap()
    if args.cmd == "marketplace":
        _marketplace(args)
        return
    if args.cmd == "goal":
        from agent_company import loop

        gid = loop.create_goal(args.title, args.description, args.repo, args.url, args.check,
                               args.max_iterations, allowed_paths=args.allow, protect_tests=args.protect_tests,
                               extract_checks=True, permits=args.permit, kind=args.kind)
        out = loop.run_goal(gid)
        g = loop._goal(gid)
        print(f"Goal {gid}: {out['status']} after {g.iteration} iteration(s)\n{g.result}\n"
              f"Branch {g.branch} in {g.worktree}")
        if out["status"] == "awaiting_approval":
            print(f"Approve with: python -m agent_company approve {gid}")
    elif args.cmd == "crawl":
        from agent_company import research

        print(json.dumps(research.collect(args.url, args.depth, args.max_pages), indent=2))
    elif args.cmd == "eval":
        from agent_company import evaluation

        if args.review:
            from agent_company import prefs

            prefs.load()
            out = evaluation.scheduled_review(force=True)
            print("not run: set benchmark_budget_usd (Settings) above 0" if out is None else
                  f"{out['label']}: {out['summary']['passed']}/{out['summary']['runs']} passed"
                  + (f", no longer passing: {', '.join(out['regressions'])}" if out.get("regressions") else ""))
            return
        if args.compare:
            out = evaluation.compare(*args.compare)
            print(f"pass rate {out['a']['pass_rate']:.0%} -> {out['b']['pass_rate']:.0%} "
                  f"({out['pass_rate_change']:+.0%}), cost {out['cost_change']:+.4f} USD")
            for t in out["tasks"]:
                print(f"  {t['task']:<24} {t[args.compare[0]]} -> {t[args.compare[1]]}")
            for dim, x in out["dimensions"].items():
                a, b = args.compare
                print(f"  {dim:<12} {x['metric']:<15} {x[a]} (n={x['n'][a]}) -> {x[b]} (n={x['n'][b]})")
            if out["regressions"]:
                print(f"regressions ({out['regression_rate']:.0%} of the tasks {args.compare[0]} solved): "
                      + ", ".join(out["regressions"]))
            return
        if args.gate:
            from agent_company import performance

            out = performance.room_gate(args.gate)
            print(f"room {args.gate}: " + ("met" if out["met"] else "not met"))
            for r in out["reasons"]:
                print(f"  - {r}")
            return
        trial = ({"models": {args.role: args.model}} if args.model else {}) | ({"room": args.room} if args.room else {})
        trial = trial or None
        label = args.label or (f"model:{args.model}" if args.model else "")
        if not label:
            raise SystemExit("--label is required to run a suite")
        out = evaluation.run_suite(args.suite, label, args.repeats, args.tasks, trial=trial)
        print(f"{out['label']}: {out['solved']}/{out['tasks']} solved, pass rate {out['pass_rate']:.0%}, "
              f"cost {out['total_cost']:.4f} USD, {out['avg_seconds']}s per run")
        for t in out["by_task"]:
            print(f"  {t['task']:<24} {t['passed']}/{t['runs']} {', '.join(t['end_reasons'])}")
        for dim, x in out["dimensions"].items():  # S09-R-05: each dimension with its sample size
            print(f"  {dim:<12} " + ", ".join(f"{k} {v}" for k, v in x.items()))
    elif args.cmd == "learn":
        from agent_company import experiments, training

        a = args.args
        if args.action == "gaps":
            out = training.gaps()
        elif args.action == "raise":
            out = training.raise_gaps()
        elif args.action == "plans":
            out = training.list_plans()
        elif args.action == "plan":
            if len(a) < 2:
                raise SystemExit("learn plan <agent id> <capability> [final suite] [practice suite]")
            out = training.create_plan(int(a[0]), a[1], final_suite=a[2] if len(a) > 2 else "",
                                       practice_suite=a[3] if len(a) > 3 else "", created_by="cli")
        elif args.action in {"start", "cancel"}:
            if not a:
                raise SystemExit(f"learn {args.action} <plan id>")
            if args.action == "cancel":
                out = training.cancel_plan(int(a[0]))
            else:  # in the foreground: the CLI waits for the practice and final runs
                training._spawn = lambda fn: fn()
                out = training.start_plan(int(a[0]))
        elif args.action == "generate":
            from agent_company import exercises

            if not a:
                raise SystemExit("learn generate <skill> [practice|final] [count]")
            out = exercises.generate(a[0], a[1] if len(a) > 1 else "practice", int(a[2]) if len(a) > 2 else 3, "cli")
        elif args.action == "suites":
            from agent_company import evaluation

            out = evaluation.list_suites()
        elif args.action == "approve":
            from agent_company import exercises

            if not a:
                raise SystemExit("learn approve <generated final suite>")
            out = exercises.approve(a[0], "cli")
        elif args.action == "experiments":
            out = experiments.list_experiments()
        elif args.action == "retrieval":
            from agent_company import learning

            out = learning.learn_strategies("cli")
        else:
            out = training.metrics()
        print(json.dumps(out, indent=2, default=str))
    elif args.cmd == "sbom":
        from agent_company import dependencies

        from pathlib import Path

        text = json.dumps(dependencies.sbom(args.repo, args.rev), indent=2)
        if args.out:
            Path(args.out).write_text(text)
            print(f"{args.out}: {len(json.loads(text)['components'])} component(s)")
        else:
            print(text)
    elif args.cmd == "strategies":
        from agent_company import orchestration

        if args.action == "recommend":
            if not args.kind:
                raise SystemExit("strategies recommend <kind>")
            out = orchestration.recommend(args.kind, "cli")
        else:
            out = orchestration.evaluate(args.kind)
        print(json.dumps(out, indent=2, default=str))
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
    elif args.cmd == "improve":
        _improve(args)
    elif args.cmd == "requirements":
        _requirements(args)


def _requirements(args: argparse.Namespace) -> None:
    from sqlalchemy import select

    from agent_company import requirements
    from agent_company.db import Project, session

    pid = args.project
    if pid is None:
        with session() as s:
            pid = s.scalars(select(Project.id).where(Project.protected).order_by(Project.id)).first()
        if pid is None:
            raise SystemExit("no platform project; name one with --project")
    if args.action == "import":
        out: object = requirements.import_docs(pid, "cli")
    elif args.action == "trace":
        out = requirements.traceability(pid)
    else:
        out = requirements.list_requirements(pid)
    print(json.dumps(out, indent=2, default=str))


def _improve(args: argparse.Namespace) -> None:
    from agent_company import selfevolve
    from agent_company.config import REPO_ROOT

    act, cid = args.action, args.id
    if act in ("start", "deploy", "rollback", "cancel") and not (cid or "").isdigit():
        raise SystemExit(f"improve {act} needs a contract id")
    try:
        if act == "list":
            for c in selfevolve.contracts():
                best = max((x["report"]["score_pct"] for x in c["candidates"] if x.get("report")), default=None)
                print(f"#{c['id']:<3} {c['status']:<11} {c['component']:<12} {c['title']}"
                      + (f"  (best score {best:+.2f}%)" if best is not None else ""))
        elif act == "weaknesses":
            for w in selfevolve.weaknesses():
                print(f"{w['count']:>3}x {w['layer']:<13} {w['component'] or '-':<12} {'; '.join(w['reasons'])}\n"
                      f"      -> {w['action']}")
        elif act == "benchmark":  # the fixed benchmark on the current checkout, once
            bench = selfevolve.load_benchmark(REPO_ROOT, cid or "retrieval")
            work = selfevolve.settings().data_dir / "improvement" / "adhoc"
            work.mkdir(parents=True, exist_ok=True)
            out = selfevolve.run_benchmark(REPO_ROOT, bench, work)
            print(json.dumps({"metrics": out["metrics"], "classes": out.get("classes", {})}, indent=2))
        elif act == "create":
            print(json.dumps(selfevolve.create(args.title, args.objective, args.component, args.benchmark,
                                               args.layer, args.kind), indent=2))
        elif act == "start":  # in the foreground, as `goal` does: baseline, candidate goal, verification
            from agent_company import loop

            selfevolve._spawn = lambda fn: fn()
            loop.enqueue = loop.run_goal
            selfevolve.start(int(cid))
            print(json.dumps(selfevolve.get(int(cid)), indent=2))
        elif act == "deploy":
            print(json.dumps(selfevolve.deploy(int(cid), "cli"), indent=2))
        elif act == "rollback":
            print(json.dumps(selfevolve.rollback(int(cid), "cli", args.reason), indent=2))
        elif act == "cancel":
            print(json.dumps(selfevolve.cancel(int(cid), "cli"), indent=2))
        elif act == "advance":
            print(f"{selfevolve.advance()} step(s) started")
    except (KeyError, ValueError, RuntimeError) as e:
        raise SystemExit(str(e).strip("'")) from e


if __name__ == "__main__":
    main()
