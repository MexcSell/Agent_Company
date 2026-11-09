"""Verification phase 6, live: a project receives an assignment and produces a working result. TEST-PROJECT-003
(Simple Calculator) is registered on this server; the plan's order goes to its General Manager through the project's
command window, as the owner gives orders; the goal runs in this process on this server's agents and models. The
deliverable is then checked by the platform, not by the agents' report: files in the project's repository, the
tests and the application run in the L3 sandbox. Report: docs/verification/phase-6-execution.md.

    uv run python docs/verification/phase6_live.py

Follow-up proposals are not auto-approved in this process; they are rejected and listed. The project is archived at
the end (its id is in the evidence); a checkout of its main branch stays in data/worktrees/phase6-check-<time>. Evidence: data/verification/phase6/run.json
"""
import json
import subprocess
import threading
import time
from pathlib import Path

from sqlalchemy import select

from agent_company import auth, contracts, evolution, mission, prefs, projects, router, sandbox
from agent_company.config import settings
from agent_company.db import Agent, Event, Goal, LiveProcess, Proposal, Run, Task, User, session

REQUIREMENTS = ("Project: Simple Calculator\n\nRequirements:\n- Addition\n- Subtraction\n- Multiplication\n"
                "- Division\n- Division-by-zero handling\n\nAssigned agents: Project Manager, Developer, QA")
ORDER = ("Develop a simple calculator application.\n\nFirst:\n1. Analyze the requirements.\n"
         "2. Create a development plan.\n3. Divide the work into tasks.\n4. Assign implementation to Developer Agent.\n"
         "5. Assign testing to QA Agent.\n6. Report the final results.")


def run(cmd: str, wt: Path) -> tuple[bool, str]:
    return contracts.run_command(cmd, wt, 300, mode=sandbox.mode_for(None))


def main() -> None:
    prefs.load()  # the DB settings (memory: CLI scripts need prefs.load)
    settings().auto_approve_proposals = False  # this process only
    # a smoke trial started here would die with this process and leave its eval goal in `planning` (V25); the
    # server's own sweep (evolution: "smoke trials a restart cut off") starts it instead
    evolution._spawn = lambda fn: None
    with session() as s:
        admin = auth.user_dict(s.scalars(select(User).where(User.is_admin).order_by(User.id)).first())
        first_run = max([r.id for r in s.scalars(select(Run).order_by(Run.id.desc()).limit(1))] or [0]) + 1
        dev, qa = (s.scalars(select(Agent.id).where(Agent.name == n)).one() for n in ("Backend Developer", "QA Engineer"))
    p = projects.create("TEST-PROJECT-003", description=REQUIREMENTS, created_by=admin["id"])
    projects.update(p["id"], {"stack": ["python"], "rooms": ["development"]}, actor="verification phase 6")
    projects.update_topic(p["id"], "general", {"agent_ids": [dev, qa]})
    started = threading.enumerate()
    t0 = time.monotonic()
    out = projects.command(p["id"], admin, ORDER)  # the command window: the order becomes a goal, run at once
    gid = out["goal_id"]
    for th in threading.enumerate():
        if th not in started and th.name == f"goal-{gid}":
            th.join()
    elapsed = time.monotonic() - t0
    repo = Path(p["path"])
    with session() as s:
        g = s.get(Goal, gid)
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        tasks = [{"id": t.id, "project_id": g.project_id, "iteration": t.iteration, "key": t.key, "title": t.title,
                  "role": t.role, "receiver": names.get(t.agent_id), "sender": names.get(t.planned_by),
                  "description": t.contract.get("description"), "priority": t.priority,
                  "estimated_minutes": t.contract.get("estimated_minutes"), "created_at": t.created_at,
                  "started_at": t.started_at, "finished_at": t.finished_at, "status": t.status, "stage": t.stage,
                  "stage_log": t.stage_log, "acceptance": (t.gate or {}).get("acceptance"),
                  "files": (t.gate or {}).get("files"), "output": t.output[-5000:]}
                 for t in s.scalars(select(Task).where(Task.goal_id == gid).order_by(Task.id))]
        runs = [{"id": r.id, "agent": names.get(r.agent_id), "purpose": r.purpose, "task_id": r.task_id,
                 "model": r.model_name, "ok": r.ok, "error": r.error[:300], "issues": router.trace_issues(r.trace or {}),
                 "duration": round(r.duration, 1), "cost": r.cost}
                for r in s.scalars(select(Run).where(Run.goal_id == gid, Run.id >= first_run).order_by(Run.id))]
        events = [{"at": e.created_at, "kind": e.kind, "agent": names.get(e.agent_id), "message": e.message[:300]}
                  for e in s.scalars(select(Event).where(Event.goal_id == gid).order_by(Event.id))]
        raised = [x.id for x in s.scalars(select(Proposal).where(Proposal.goal_id == gid, Proposal.status == "pending"))]
        status, result, worktree = g.status, g.result, g.worktree
        live = len(s.scalars(select(LiveProcess)).all())
    rejected = []
    for pid in raised:
        try:
            projects.decide(pid, False, "verification phase 6 (test project; follow-ups not wanted)")
            rejected.append(pid)
        except Exception as e:  # noqa: BLE001
            rejected.append(f"{pid}: {type(e).__name__}: {e}")
    # 6.6: the deliverable on the project's main branch, checked by the platform
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo, capture_output=True,
                           text=True).stdout.split()
    check_wt = settings().worktrees_dir / f"phase6-check-{time.strftime('%Y%m%d-%H%M%S')}"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(check_wt), "HEAD"], cwd=repo, check=True)
    checks = {"pytest": run("python3 -B -m pytest -q -p no:cacheprovider", check_wt), "worktree": str(check_wt)}
    try:
        report = mission.report(gid)
    except Exception as e:  # noqa: BLE001
        report = {"error": f"{type(e).__name__}: {e}"}
    evidence = {"project": p | {"path": str(repo)}, "goal": {"id": gid, "status": status, "result": result,
                                                              "worktree": worktree},
                "elapsed_s": round(elapsed, 1), "tasks": tasks, "runs": runs, "events": events,
                "main_files": files, "checks": checks, "mission_report": report, "live_processes": live,
                "proposals_rejected": rejected,
                "messages": [{k: m[k] for k in ("kind", "agent", "content", "at")} for m in projects.messages(p["id"])]}
    dest = settings().data_dir / "verification" / "phase6"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "run.json"
    path.write_text(json.dumps(evidence, indent=1, default=str))
    print(json.dumps({"project": p["id"], "goal": gid, "status": status, "elapsed_s": evidence["elapsed_s"],
                      "tasks": [{k: t[k] for k in ("iteration", "key", "role", "status", "sender", "receiver")}
                                | {"acceptance": [c["passed"] for c in t["acceptance"] or []]} for t in tasks],
                      "main_files": files, "pytest": checks["pytest"][0], "proposals_rejected": rejected},
                     indent=1, default=str))
    projects.set_status(p["id"], "archived", "verification phase 6", "test project; reopen to look at it")
    print(f"evidence: {path}; the check worktree {check_wt} stays for running the application")


if __name__ == "__main__":
    main()
