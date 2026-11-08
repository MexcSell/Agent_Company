"""Verification phase 4, live: the plan's two-agent test on this server's models. The Project Manager (the General
Manager) receives the assignment as a goal and delegates it; the Developer does the work; the result comes back
through the gate. Runs `loop.run_goal` in this process on the live DB, in a scratch repository of its own
(data/workspaces/goal-<id>). Report: docs/verification/phase-4-communication.md.

    uv run python docs/verification/phase4_live.py two     # the GM is asked for one developer task; research and
                                                           # the critic are off (the template still adds its QA review)
    uv run python docs/verification/phase4_live.py three   # the GM plans as it normally does, research and critic on

Follow-up proposals the agents raise are not auto-approved here (a toy goal's follow-ups would become goals); they
are rejected after the run and listed in the evidence. Evidence: data/verification/phase4/<mode>.json
"""
import json
import sys
import time

from sqlalchemy import select

from agent_company import blackboard, loop, prefs, projects, router
from agent_company.config import settings
from agent_company.db import Agent, Event, LiveProcess, Proposal, Run, Task, session

ASSIGNMENT = ("Ask the Developer Agent to create a Python function that calculates factorial.\n\n"
              "The Developer must return:\n1. Source code (factorial.py).\n2. Unit tests (test_factorial.py, "
              "pytest).\n3. Execution result: the tests' result as the developer saw it.")
TWO = ("\n\nThis is a two-agent communication test: plan exactly one task, for the developer. Do not add a QA, "
       "security or research task.")


def main(mode: str) -> None:
    prefs.load()  # the DB settings: enable_claude_code and the rest (memory: CLI scripts need prefs.load)
    settings().auto_approve_proposals = False  # this process only; the server's setting is unchanged
    if mode == "two":
        settings().task_critic = False  # a critic would be a third agent
        loop._research = lambda goal, iteration, feedback: ""  # so would the research lead
    with session() as s:
        first_run = max([r.id for r in s.scalars(select(Run).order_by(Run.id.desc()).limit(1))] or [0]) + 1
    gid = loop.create_goal(f"Phase 4 live ({mode} agents): factorial", ASSIGNMENT + (TWO if mode == "two" else ""),
                           max_iterations=2)
    t0 = time.monotonic()
    out = loop.run_goal(gid)  # claims the goal at once: the server's watcher only takes goals queued for 15 s
    elapsed = time.monotonic() - t0
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        tasks = [{"id": t.id, "iteration": t.iteration, "key": t.key, "role": t.role, "status": t.status,
                  "stage": t.stage, "sender": names.get(t.planned_by), "sender_id": t.planned_by,
                  "receiver": names.get(t.agent_id), "receiver_id": t.agent_id, "model": t.model_name,
                  "created_at": t.created_at, "started_at": t.started_at, "finished_at": t.finished_at,
                  "stage_log": t.stage_log, "acceptance": (t.gate or {}).get("acceptance"),
                  "files": (t.gate or {}).get("files"), "contract": t.contract, "output": t.output[-4000:]}
                 for t in s.scalars(select(Task).where(Task.goal_id == gid).order_by(Task.id))]
        runs = [{"id": r.id, "agent": names.get(r.agent_id), "purpose": r.purpose, "task_id": r.task_id,
                 "model": r.model_name, "ok": r.ok, "error": r.error[:500], "trace": r.trace,
                 "issues": router.trace_issues(r.trace or {}), "duration": round(r.duration, 1), "cost": r.cost}
                for r in s.scalars(select(Run).where(Run.goal_id == gid, Run.id >= first_run).order_by(Run.id))]
        events = [{"at": e.created_at, "kind": e.kind, "agent": names.get(e.agent_id), "message": e.message[:400]}
                  for e in s.scalars(select(Event).where(Event.goal_id == gid).order_by(Event.id))]
        live = len(s.scalars(select(LiveProcess)).all())
        raised = [p.id for p in s.scalars(select(Proposal).where(Proposal.goal_id == gid, Proposal.status == "pending"))]
        g = loop._goal(gid)
    rejected = []
    for pid in raised:
        try:
            projects.decide(pid, False, "verification phase 4 (toy goal; follow-ups not wanted)")
            rejected.append(pid)
        except Exception as e:  # noqa: BLE001
            rejected.append(f"{pid}: {type(e).__name__}: {e}")
    evidence = {"mode": mode, "goal": {"id": gid, "status": g.status, "result": g.result, "history": g.history,
                                       "repo": g.repo_path, "worktree": g.worktree},
                "out": {k: v for k, v in out.items() if k != "report"}, "elapsed_s": round(elapsed, 1),
                "tasks": tasks, "runs": runs, "messages": blackboard.read(gid, ["messages"]),
                "board": [{k: e[k] for k in ("section", "key", "version", "author_agent_id", "task_id")}
                          for e in blackboard.read(gid)],
                "events": events, "live_processes": live, "proposals_rejected": rejected}
    dest = settings().data_dir / "verification" / "phase4"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{mode}.json"
    path.write_text(json.dumps(evidence, indent=1, default=str))
    print(json.dumps({"goal": gid, "status": g.status, "elapsed_s": evidence["elapsed_s"],
                      "tasks": [{k: t[k] for k in ("iteration", "key", "role", "status", "sender", "receiver", "model")}
                                | {"acceptance": [(c.get("check") or c.get("type"), c["passed"])
                                                  for c in t["acceptance"] or []]} for t in tasks],
                      "runs": [{k: r[k] for k in ("id", "agent", "purpose", "model", "ok", "issues")} for r in runs],
                      "proposals_rejected": rejected}, indent=1, default=str))
    print(f"evidence: {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "two")
