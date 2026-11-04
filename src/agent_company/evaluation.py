"""Eval harness: measure the company on a fixed suite of tasks (as /home/thomas/bit/eval does).

A suite is a JSON file:

    {"name": "smoke", "tasks": [
        {"name": "fizzbuzz", "title": "Write fizzbuzz.py ...", "description": "...",
         "files": {"README.md": "seed content"},           # the starting repository
         "checks": ["python -c 'import fizzbuzz'"],        # the task's quality commands
         "max_iterations": 2}
    ]}

Each task runs `repeats` times, each time as a fresh goal (origin "eval") in a new
repository seeded with `files`. A task counts as solved only if every repeat passes.
Per run we record pass/fail, why it ended, iterations, cost, tokens and time; results
are kept under a label so two labels (e.g. before and after a routing change) can be
compared. Eval goals never wait for approval and never merge anywhere.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select

from agent_company import loop, worktrees
from agent_company.config import REPO_ROOT, settings
from agent_company.db import EvalRun, Goal, Run, log_event, session

SUITES_DIR = REPO_ROOT / "evals"


def load_suite(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() and (SUITES_DIR / f"{path}.json").exists():
        p = SUITES_DIR / f"{path}.json"
    data = json.loads(p.read_text())
    if not isinstance(data.get("tasks"), list) or not data["tasks"]:
        raise ValueError("a suite needs a non-empty 'tasks' list")
    for t in data["tasks"]:
        if not t.get("name") or not t.get("title"):
            raise ValueError("every task needs a name and a title")
    data.setdefault("name", p.stem)
    return data


def list_suites() -> list[dict[str, Any]]:
    out = []
    for p in sorted(SUITES_DIR.glob("*.json")):
        try:
            s = load_suite(p)
            out.append({"name": s["name"], "file": p.name, "tasks": [t["name"] for t in s["tasks"]]})
        except (ValueError, json.JSONDecodeError):
            continue
    return out


def _seed_repo(label: str, task: dict[str, Any], repeat: int) -> Path:
    repo = settings().workspaces_dir / f"eval-{label}-{task['name']}-{repeat}-{int(time.time() * 1000)}"
    repo.mkdir(parents=True)
    worktrees.git(repo, "init", "-q", "-b", "main")
    files = dict(task.get("files") or {"README.md": f"# {task['name']}\n"})
    for rel, text in files.items():
        target = (repo / rel).resolve()
        if repo.resolve() not in target.parents:
            raise ValueError(f"seed file escapes the repository: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    worktrees.git(repo, "add", "-A")
    worktrees.git(repo, "commit", "-q", "-m", "Eval seed")
    return repo


def _end_reason(goal: Goal) -> str:
    if goal.status in {"approved", "awaiting_approval"}:
        return "passed"
    text = goal.result or ""
    if "budget" in text:
        return "out_of_budget"
    if "Error" in text.split("\n", 1)[0]:
        return "crashed"
    return "gate_failed"


def run_suite(suite: str | Path, label: str, repeats: int = 3, only: list[str] | None = None) -> dict[str, Any]:
    data = load_suite(suite)
    tasks = [t for t in data["tasks"] if not only or t["name"] in only]
    for task in tasks:
        for rep in range(1, repeats + 1):
            repo = _seed_repo(label, task, rep)
            started = time.monotonic()
            gid = loop.create_goal(task["title"], task.get("description", task["title"]), str(repo),
                                   quality_commands=list(task.get("checks", [])),
                                   max_iterations=task.get("max_iterations"), origin="eval",
                                   allowed_paths=task.get("allowed_paths"), protect_tests=task.get("protect_tests", False))
            loop.run_goal(gid)
            g = loop._goal(gid)
            with session() as s:
                cost, tokens = s.execute(select(func.coalesce(func.sum(Run.cost), 0.0),
                                                func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0))
                                         .where(Run.goal_id == gid)).one()
                score = (g.history or [{}])[-1].get("score", 0.0) if g.history else 0.0
                s.add(EvalRun(label=label, suite=data["name"], task=task["name"], repeat=rep, goal_id=gid,
                              passed=g.status in {"approved", "awaiting_approval"}, status=g.status,
                              end_reason=_end_reason(g), iterations=g.iteration, cost=float(cost),
                              tokens=int(tokens), seconds=round(time.monotonic() - started, 2), score=score))
            log_event("eval", f"{label}: {task['name']} run {rep} {g.status}", goal_id=gid)
    return summary(label)


def summary(label: str) -> dict[str, Any]:
    with session() as s:
        runs = s.scalars(select(EvalRun).where(EvalRun.label == label).order_by(EvalRun.id)).all()
        if not runs:
            raise KeyError(label)
        by_task: dict[str, list[EvalRun]] = {}
        for r in runs:
            by_task.setdefault(r.task, []).append(r)
        tasks = [{"task": t, "runs": len(rs), "passed": sum(r.passed for r in rs), "solved": all(r.passed for r in rs),
                  "avg_cost": round(sum(r.cost for r in rs) / len(rs), 6),
                  "avg_seconds": round(sum(r.seconds for r in rs) / len(rs), 2),
                  "avg_iterations": round(sum(r.iterations for r in rs) / len(rs), 2),
                  "end_reasons": sorted({r.end_reason for r in rs})} for t, rs in by_task.items()]
        return {"label": label, "suite": runs[0].suite, "runs": len(runs),
                "pass_rate": round(sum(r.passed for r in runs) / len(runs), 3),
                "solved": sum(t["solved"] for t in tasks), "tasks": len(tasks),
                "total_cost": round(sum(r.cost for r in runs), 6),
                "avg_seconds": round(sum(r.seconds for r in runs) / len(runs), 2), "by_task": tasks}


def compare(a: str, b: str) -> dict[str, Any]:
    sa, sb = summary(a), summary(b)
    ta = {t["task"]: t for t in sa["by_task"]}
    tb = {t["task"]: t for t in sb["by_task"]}
    rows = [{"task": name, a: ta.get(name, {}).get("passed"), b: tb.get(name, {}).get("passed"),
             "change": (tb.get(name, {}).get("passed", 0) or 0) - (ta.get(name, {}).get("passed", 0) or 0)}
            for name in sorted(set(ta) | set(tb))]
    return {"a": sa, "b": sb, "pass_rate_change": round(sb["pass_rate"] - sa["pass_rate"], 3),
            "cost_change": round(sb["total_cost"] - sa["total_cost"], 6), "tasks": rows}


def labels() -> list[dict[str, Any]]:
    with session() as s:
        rows = s.execute(select(EvalRun.label, func.count(), func.sum(case((EvalRun.passed, 1), else_=0)),
                                func.max(EvalRun.created_at)).group_by(EvalRun.label)).all()
    return [{"label": r[0], "runs": r[1], "passed": int(r[2] or 0), "at": r[3].isoformat() if r[3] else None}
            for r in rows]
