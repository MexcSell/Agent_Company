"""Eval harness: measure the company on a fixed suite of tasks (as /home/thomas/bit/eval does).

A suite is a JSON file:

    {"name": "smoke", "tasks": [
        {"name": "fizzbuzz", "title": "Write fizzbuzz.py ...", "description": "...",
         "files": {"README.md": "seed content"},           # the starting repository
         "checks": ["python -c 'import fizzbuzz'"],        # the task's quality commands
         "max_iterations": 2}
    ]}

A suite may name its `kind` (`benchmark`, the default; `practice` exercises; or a `final` evaluation set,
whose checks never reach an agent: SPEC-13 S13-R-02; or `adversarial`, traps run like a final set: S09-R-05) and
the `skills` it trains or measures.

Each task runs `repeats` times, each time as a fresh goal (origin "eval") in a new
repository seeded with `files`. A task counts as solved only if every repeat passes.
Per run we record pass/fail, why it ended, iterations, cost, tokens and time, and the dimensions of S09-R-05
(contract compliance, tool use, security behaviour); the summary reports each dimension with its sample size,
reliability across repeats included, and no combined score. Results are kept under a label so two labels (e.g.
before and after a routing change) can be compared. Eval goals never wait for approval and never merge anywhere.
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
            out.append({"name": s["name"], "file": p.name, "tasks": [t["name"] for t in s["tasks"]],
                        "kind": s.get("kind", "benchmark"), "skills": list(s.get("skills") or [])})
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


COMPLIANCE_CHECKS = {"scope", "boundaries"}  # what the goal allowed to change, tests not disabled or weakened


def _dimensions(s: Any, goal: Goal) -> dict[str, Any]:
    """S09-R-05: one run beyond pass/fail, from what the gate itself ran (`Goal.history`), the runs' tool calls and
    the commands the policy refused, never from an agent's output. Completion and efficiency are the row's columns;
    reliability needs the repeats, so `summary` computes it."""
    from agent_company.db import Event

    hist = list(goal.history or [])
    failed = [set(h.get("failed_checks") or []) for h in hist]
    last = hist[-1] if hist else {}
    calls = failed_calls = denied = failed_runs = 0
    for tool_calls, denied_tools, ok in s.execute(select(Run.tool_calls, Run.denied_tools, Run.ok)
                                                  .where(Run.goal_id == goal.id)).all():
        calls += len(tool_calls or [])
        failed_calls += sum(1 for c in tool_calls or [] if isinstance(c, dict) and c.get("ok") is False)
        denied += len(denied_tools or [])
        failed_runs += not ok
    refused = s.scalar(select(func.count()).select_from(Event).where(Event.goal_id == goal.id,
                                                                    Event.kind == "command_refused")) or 0
    security_checks = sorted({v["command"] for h in hist for v in h.get("verification") or []
                              if v.get("stage") == "security" and not v.get("passed") and v.get("command")})
    secret = any("secret scan" in f for f in failed)
    violations = sorted({c for f in failed for c in f & COMPLIANCE_CHECKS}
                        | ({"scope"} if any(h.get("scope_failed") for h in hist) else set()))
    contract_failed = sorted(c for c in last.get("failed_checks") or [] if c.startswith("task "))
    return {
        "compliance": {"compliant": not violations and not contract_failed, "violations": violations,
                       "contract_failed": contract_failed},
        "tool_use": {"tool_calls": calls, "failed_calls": failed_calls, "denied_tools": denied,
                     "refused_commands": int(refused), "failed_runs": failed_runs},
        "security": {"secure": not (secret or security_checks or refused), "secret_found": secret,
                     "failed_checks": security_checks, "refused_commands": int(refused)},
    }


def run_suite(suite: str | Path, label: str, repeats: int = 3, only: list[str] | None = None,
              trial: dict[str, Any] | None = None) -> dict[str, Any]:
    """`trial` goes to every goal (experiment side, model overrides, trainee). A `final` suite hides its checks and
    gets one iteration: a second one would hand the agents the failed checks as findings. An `adversarial` suite
    (S09-R-05) is run the same way, so a failed trap check never tells the agents what the trap was."""
    data = load_suite(suite)
    tasks = [t for t in data["tasks"] if not only or t["name"] in only]
    final = data.get("kind") in {"final", "adversarial"}
    trial = dict(trial or {}) | ({"hide_checks": True} if final else {})
    own_limit = final or not trial.get("strategy")  # a strategy side's iteration limit is under test (S04-R-13)
    for task in tasks:
        for rep in range(1, repeats + 1):
            repo = _seed_repo(label, task, rep)
            started = time.monotonic()
            gid = loop.create_goal(task["title"], task.get("description", task["title"]), str(repo),
                                   quality_commands=list(task.get("checks", [])),
                                   max_iterations=1 if final else task.get("max_iterations") if own_limit else None,
                                   origin="eval",
                                   allowed_paths=task.get("allowed_paths"), protect_tests=task.get("protect_tests", False),
                                   trial=trial or None)
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
                              tokens=int(tokens), seconds=round(time.monotonic() - started, 2), score=score,
                              dimensions=_dimensions(s, g)))
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
        for t in tasks:
            dims = [r.dimensions for r in by_task[t["task"]] if r.dimensions]
            t["compliant"] = sum(d["compliance"]["compliant"] for d in dims) if dims else None
            t["secure"] = sum(d["security"]["secure"] for d in dims) if dims else None
        passed = sum(r.passed for r in runs)
        return {"label": label, "suite": runs[0].suite, "runs": len(runs), "passed": passed,
                "dimensions": dimensions(runs, by_task),
                "pass_rate": round(passed / len(runs), 3),
                "avg_iterations": round(sum(r.iterations for r in runs) / len(runs), 2),
                "cost_per_passed": round(sum(r.cost for r in runs) / passed, 6) if passed else None,
                "solved": sum(t["solved"] for t in tasks), "tasks": len(tasks),
                "total_cost": round(sum(r.cost for r in runs), 6),
                "avg_seconds": round(sum(r.seconds for r in runs) / len(runs), 2), "by_task": tasks}


def _rate(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def dimensions(runs: list[EvalRun], by_task: dict[str, list[EvalRun]]) -> dict[str, Any]:
    """S09-R-05: separate dimensions, each with its sample size `n` (runs, or tasks for reliability); runs recorded
    before the dimensions existed count only for completion, reliability and efficiency. No combined score."""
    dims = [r.dimensions for r in runs if r.dimensions]
    passed = [r for r in runs if r.passed]
    tool = [d["tool_use"] for d in dims]
    calls = sum(t["tool_calls"] for t in tool)
    repeated = {t: rs for t, rs in by_task.items() if len(rs) > 1}
    return {
        "completion": {"n": len(runs), "passed": len(passed), "pass_rate": _rate(len(passed), len(runs))},
        "compliance": {"n": len(dims), "compliant_rate": _rate(sum(d["compliance"]["compliant"] for d in dims), len(dims)),
                       "violations": sum(len(d["compliance"]["violations"]) for d in dims),
                       "contract_failures": sum(len(d["compliance"]["contract_failed"]) for d in dims)},
        "tool_use": {"n": len(tool), "tool_calls": calls, "failed_calls": sum(t["failed_calls"] for t in tool),
                     "accuracy": _rate(calls - sum(t["failed_calls"] for t in tool), calls),
                     "denied_tools": sum(t["denied_tools"] for t in tool),
                     "refused_commands": sum(t["refused_commands"] for t in tool),
                     "failed_runs": sum(t["failed_runs"] for t in tool)},
        # a task is reliable when every repeat ends the same way; pass^k = solved in every repeat
        "reliability": {"n": len(repeated), "repeats": max((len(rs) for rs in by_task.values()), default=0),
                        "consistent_rate": _rate(sum(len({r.passed for r in rs}) == 1 for rs in repeated.values()),
                                                 len(repeated)),
                        "pass_all_rate": _rate(sum(all(r.passed for r in rs) for rs in repeated.values()), len(repeated)),
                        "pass_any_rate": _rate(sum(any(r.passed for r in rs) for rs in repeated.values()), len(repeated))},
        "security": {"n": len(dims), "secure_rate": _rate(sum(d["security"]["secure"] for d in dims), len(dims)),
                     "secrets_found": sum(d["security"]["secret_found"] for d in dims),
                     "failed_checks": sum(len(d["security"]["failed_checks"]) for d in dims),
                     "refused_commands": sum(d["security"]["refused_commands"] for d in dims)},
        "efficiency": {"n": len(runs), "avg_seconds": round(sum(r.seconds for r in runs) / len(runs), 2),
                       "avg_cost": round(sum(r.cost for r in runs) / len(runs), 6),
                       "avg_tokens": round(sum(r.tokens for r in runs) / len(runs)),
                       "avg_iterations": round(sum(r.iterations for r in runs) / len(runs), 2),
                       "cost_per_passed": round(sum(r.cost for r in runs) / len(passed), 6) if passed else None},
    }


# the rate of each dimension `compare` diffs (higher is better for all but efficiency's)
HEADLINE = {"completion": "pass_rate", "compliance": "compliant_rate", "tool_use": "accuracy",
            "reliability": "pass_all_rate", "security": "secure_rate", "efficiency": "avg_cost"}


def compare(a: str, b: str) -> dict[str, Any]:
    sa, sb = summary(a), summary(b)
    dims = {}
    for dim, key in HEADLINE.items():
        va, vb = sa["dimensions"][dim][key], sb["dimensions"][dim][key]
        dims[dim] = {"metric": key, a: va, b: vb, "change": None if va is None or vb is None else round(vb - va, 6),
                     "n": {a: sa["dimensions"][dim]["n"], b: sb["dimensions"][dim]["n"]}}
    ta = {t["task"]: t for t in sa["by_task"]}
    tb = {t["task"]: t for t in sb["by_task"]}
    rows = [{"task": name, a: ta.get(name, {}).get("passed"), b: tb.get(name, {}).get("passed"),
             "change": (tb.get(name, {}).get("passed", 0) or 0) - (ta.get(name, {}).get("passed", 0) or 0)}
            for name in sorted(set(ta) | set(tb))]
    return {"a": sa, "b": sb, "pass_rate_change": round(sb["pass_rate"] - sa["pass_rate"], 3),
            "cost_change": round(sb["total_cost"] - sa["total_cost"], 6), "tasks": rows, "dimensions": dims}


def labels() -> list[dict[str, Any]]:
    with session() as s:
        rows = s.execute(select(EvalRun.label, func.count(), func.sum(case((EvalRun.passed, 1), else_=0)),
                                func.max(EvalRun.created_at)).group_by(EvalRun.label)).all()
    return [{"label": r[0], "runs": r[1], "passed": int(r[2] or 0), "at": r[3].isoformat() if r[3] else None}
            for r in rows]
