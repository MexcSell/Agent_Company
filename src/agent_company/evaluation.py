"""Eval harness: measure the company on a fixed suite of tasks (as /home/thomas/bit/eval does).

A suite is a JSON file:

    {"name": "smoke", "tasks": [
        {"name": "fizzbuzz", "title": "Write fizzbuzz.py ...", "description": "...",
         "files": {"README.md": "seed content"},           # the starting repository
         "checks": ["python -c 'import fizzbuzz'"],        # the task's quality commands
         "max_iterations": 2}
    ]}

A suite may name its `kind` (`benchmark`, the default; `practice` exercises; or a `final` evaluation set,
whose checks never reach an agent: SPEC-13 S13-R-02; or `adversarial`, traps run like a final set: S09-R-05; or
`room` with its `room`, the suite a gated room passes before it is enabled: G-R-04; or `research`, the research
room's source-graded benchmark: G-R-13) and the `skills` it trains or measures. A `research` suite has no checks and
no seed repository: its `documents` are reference documents under `eval-research://` that stay in memory (ADR 0003),
and each task's `expect` (citations, facts, forbidden claims, sections) is graded without a model judge.
`run_suite` hands it to `research_eval.run`: the research room writes a report per task and repeat as
`research.run_report` does, `research.check_claims` grades it against the reference documents, and each run is one
EvalRun (no goal) whose dimensions carry the same keys plus a `research` grading, so summary and compare apply.

Each task runs `repeats` times, each time as a fresh goal (origin "eval") in a new
repository seeded with `files`. A task counts as solved only if every repeat passes.
Per run we record pass/fail, why it ended, iterations, cost, tokens and time, and the dimensions of S09-R-05
(contract compliance, tool use, security behaviour); the summary reports each dimension with its sample size,
reliability across repeats included, and no combined score. Results are kept under a label so two labels (e.g.
before and after a routing change) can be compared. Eval goals never wait for approval and never merge anywhere.
"""

from __future__ import annotations

import ast
import fnmatch
import io
import json
import shlex
import shutil
import tokenize
from uuid import uuid4
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select

from agent_company import loop, sandbox, worktrees
from agent_company.config import REPO_ROOT, settings
from agent_company.db import EvalRun, Goal, Run, log_event, session

SUITES_DIR = REPO_ROOT / "evals"


def generated_dir() -> Path:
    """Suites written by the instructor (exercises.py, S13-R-02): data, not part of the repository."""
    return settings().data_dir / "evals"


def load_suite(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() and (SUITES_DIR / f"{path}.json").exists():
        p = SUITES_DIR / f"{path}.json"
    elif not p.exists() and (generated_dir() / f"{Path(str(path)).name}.json").exists():
        p = generated_dir() / f"{Path(str(path)).name}.json"
    data = json.loads(p.read_text())
    if not isinstance(data.get("tasks"), list) or not data["tasks"]:
        raise ValueError("a suite needs a non-empty 'tasks' list")
    for t in data["tasks"]:
        if not t.get("name") or not t.get("title"):
            raise ValueError("every task needs a name and a title")
    data.setdefault("name", p.stem)
    if data.get("kind") == "research":
        _check_research(data)
    return data


RESEARCH_SCHEME = "eval-research://"
RESEARCH_CATEGORIES = ("find", "verify", "summarise", "compare", "tests", "recommend")


def _check_research(data: dict[str, Any]) -> None:
    """A research suite (G-R-13): reference documents under the reserved scheme, tasks of a known category that use
    only those documents and cite only theirs."""
    docs = data.get("documents")
    if not isinstance(docs, list) or not docs:
        raise ValueError("a research suite needs a non-empty 'documents' list")
    urls = {}
    for d in docs:
        if not d.get("id") or d["id"] in urls or not str(d.get("url", "")).startswith(RESEARCH_SCHEME) \
                or not d.get("text"):
            raise ValueError(f"every reference document needs a unique id, an {RESEARCH_SCHEME} url and a text")
        urls[d["id"]] = d["url"]
    for t in data["tasks"]:
        exp = t.get("expect")
        if t.get("category") not in RESEARCH_CATEGORIES or not t.get("question") or not isinstance(exp, dict):
            raise ValueError(f"research task {t['name']} needs a category, a question and an expect")
        if not t.get("documents") or any(i not in urls for i in t["documents"]):
            raise ValueError(f"research task {t['name']} names an unknown reference document")
        if any(u not in {urls[i] for i in t["documents"]} for u in exp.get("cite") or []):
            raise ValueError(f"research task {t['name']} expects a citation of a document it is not given")


def usable(suite: dict[str, Any]) -> bool:
    """A generated final set certifies agents only once an administrator approved it."""
    gen = suite.get("generated")
    return not gen or suite.get("kind") != "final" or bool(gen.get("approved_by"))


def list_suites() -> list[dict[str, Any]]:
    """The repository's suites by name, then generated ones newest first (so hand-written suites win a skill)."""
    out = []
    gen = generated_dir()
    paths = sorted(SUITES_DIR.glob("*.json")) + (sorted(gen.glob("*.json"), reverse=True) if gen.is_dir() else [])
    for p in paths:
        try:
            s = load_suite(p)
            out.append({"name": s["name"], "file": p.name, "tasks": [t["name"] for t in s["tasks"]],
                        "kind": s.get("kind", "benchmark"), "skills": list(s.get("skills") or []),
                        "generated": bool(s.get("generated")), "approved": usable(s),
                        **({"by": s["generated"].get("by"), "at": s["generated"].get("at"),
                            "dropped": len(s["generated"].get("dropped") or [])} if s.get("generated") else {})})
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


def label_cost(label: str) -> float:
    with session() as s:
        return float(s.scalar(select(func.coalesce(func.sum(EvalRun.cost), 0.0)).where(EvalRun.label == label)) or 0.0)


def run_suite(suite: str | Path, label: str, repeats: int = 3, only: list[str] | None = None,
              trial: dict[str, Any] | None = None, budget_usd: float = 0.0) -> dict[str, Any]:
    """`trial` goes to every goal (experiment side, model overrides, trainee). A `final` suite hides its checks and
    gets one iteration: a second one would hand the agents the failed checks as findings. An `adversarial` suite
    (S09-R-05) is run the same way, so a failed trap check never tells the agents what the trap was.
    `budget_usd` (0 = none, ASEAO10 S10-R-02): no further run starts once the label's runs cost that much; the
    summary then says `stopped_on_budget` (one run may take it over the budget).
    A `room` suite (G-R-04) or a `room` in `trial` is a room trial: that gated room's disabled agents are staffed for
    these goals (and only these), so the enablement gate can measure the room before anyone enables it."""
    from agent_company import performance

    data = load_suite(suite)
    if data.get("kind") == "research":  # no seed repository and no checks: graded reports, never a coding goal
        from agent_company import research_eval

        stopped = research_eval.run(data, label, repeats, only, budget_usd, trial=trial)
        return _finished(label, budget_usd, stopped)
    tasks = [t for t in data["tasks"] if not only or t["name"] in only]
    final = data.get("kind") in {"final", "adversarial"}
    trial = dict(trial or {}) | ({"hide_checks": True} if final else {})
    room = data.get("room") if data.get("kind") == "room" else None
    if room and trial.get("room") not in (None, room):
        raise ValueError(f"suite {data['name']} measures room {room}, not {trial['room']}")
    if room:
        trial["room"] = room
    if trial.get("room") and trial["room"] not in performance.gated_rooms():
        raise ValueError(f"{trial['room']} is not a room of a gated role ({', '.join(performance.gated_rooms())})")
    own_limit = final or not trial.get("strategy")  # a strategy side's iteration limit is under test (S04-R-13)
    stopped = False
    for task in tasks:
        for rep in range(1, repeats + 1):
            if budget_usd > 0 and label_cost(label) >= budget_usd:
                stopped = True
                break
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
        if stopped:
            break
    return _finished(label, budget_usd, stopped)


def _finished(label: str, budget_usd: float, stopped: bool) -> dict[str, Any]:
    if stopped:
        log_event("eval", f"{label}: stopped on its budget of ${budget_usd:.2f}")
        try:
            return summary(label) | {"stopped_on_budget": True}
        except KeyError:  # not one run fitted
            return {"label": label, "runs": 0, "passed": 0, "pass_rate": None, "total_cost": 0.0,
                    "stopped_on_budget": True}
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
    # G-R-11: tasks a solved in every repeat that b ran and did not; the share of a's solved tasks they are
    solved = [n for n, t in ta.items() if t["solved"]]
    broken = sorted(n for n in solved if n in tb and not tb[n]["solved"])
    return {"a": sa, "b": sb, "pass_rate_change": round(sb["pass_rate"] - sa["pass_rate"], 3),
            "cost_change": round(sb["total_cost"] - sa["total_cost"], 6), "tasks": rows, "dimensions": dims,
            "regressions": broken, "regression_rate": _rate(len(broken), len(solved))}


def labels() -> list[dict[str, Any]]:
    with session() as s:
        rows = s.execute(select(EvalRun.label, func.count(), func.sum(case((EvalRun.passed, 1), else_=0)),
                                func.max(EvalRun.created_at)).group_by(EvalRun.label)).all()
    return [{"label": r[0], "runs": r[1], "passed": int(r[2] or 0), "at": r[3].isoformat() if r[3] else None}
            for r in rows]


# --- G-R-14: the scheduled benchmark review (research guide §8: daily collection, weekly benchmark review) ---------

SCHEDULES = {"daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}
SCHEDULED = "scheduled-"  # label prefix of the scheduled runs: scheduled-<suite>-<UTC stamp>
_review_lock = threading.Lock()


def _scheduled_labels(suite: str) -> list[dict[str, Any]]:
    return sorted((x for x in labels() if x["label"].startswith(f"{SCHEDULED}{suite}-")), key=lambda x: x["at"] or "")


def review_due(at: datetime | None = None) -> bool:
    """Whether the scheduled review should run now: a schedule is set, a budget too (0 = never), and the last
    scheduled run of the suite is older than the schedule's interval."""
    s = settings()
    if s.benchmark_schedule not in SCHEDULES or s.benchmark_budget_usd <= 0:
        return False
    last = _scheduled_labels(s.benchmark_suite)
    if not last or not last[-1]["at"]:
        return True
    when = datetime.fromisoformat(last[-1]["at"])
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return ((at or datetime.now(timezone.utc)) - when).total_seconds() >= SCHEDULES[s.benchmark_schedule]


def scheduled_review(at: datetime | None = None, force: bool = False) -> dict[str, Any] | None:
    """Run the configured suite once within its budget and compare it with the previous scheduled run (G-R-11):
    an administrator is notified of the pass rate, the cost and every task that no longer passes. Nothing runs
    without a schedule and a budget (`force` skips only the interval), or under an E5 stop."""
    from agent_company.governance import notify, stopped

    s = settings()
    if s.benchmark_budget_usd <= 0 or not (force or review_due(at)):
        return None
    if why := stopped():
        log_event("eval", f"scheduled benchmark review skipped: {why}")
        return None
    if not _review_lock.acquire(blocking=False):
        return None
    try:
        suite = s.benchmark_suite
        previous = _scheduled_labels(suite)
        label = f"{SCHEDULED}{suite}-{(at or datetime.now(timezone.utc)).strftime('%Y%m%d%H%M%S')}"
        summary = run_suite(suite, label, repeats=1, budget_usd=s.benchmark_budget_usd)
        if not summary.get("runs"):
            return summary
        out: dict[str, Any] = {"label": label, "summary": summary, "previous": None, "regressions": []}
        line = (f"{suite}: {summary['passed']}/{summary['runs']} passed ({summary['pass_rate']:.0%}), "
                f"${summary['total_cost']:.4f}" + (" (stopped on its budget)" if summary.get("stopped_on_budget") else ""))
        if previous:
            cmp = compare(previous[-1]["label"], label)
            out |= {"previous": previous[-1]["label"], "regressions": cmp["regressions"],
                    "pass_rate_change": cmp["pass_rate_change"]}
            line += f"; {cmp['pass_rate_change']:+.0%} against {previous[-1]['label']}"
            if cmp["regressions"]:
                line += f"; no longer passing: {', '.join(cmp['regressions'])}"
        with session() as db:
            notify("Scheduled benchmark review", line, level="warning" if out["regressions"] else "info",
                   source="evaluation", admin_only=True, s=db)
            log_event("eval", f"scheduled benchmark review {label}: {line}", s=db)
        return out
    finally:
        _review_lock.release()


class BenchmarkThread(threading.Thread):
    """Checks every few minutes whether the scheduled benchmark review is due (G-R-14)."""

    def __init__(self, every: int = 900) -> None:
        super().__init__(daemon=True, name="benchmark-review")
        self.every = every
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(self.every):
            try:
                scheduled_review()
            except Exception as e:  # noqa: BLE001 - a broken suite must not stop the schedule
                log_event("error", f"scheduled benchmark review failed: {type(e).__name__}: {e}")


MUTATION_KINDS = ("remove_assert", "flip_boundary")
TEST_FILE = ("tests/*", "*/tests/*", "test_*.py", "*/test_*.py", "*_test.py",
             "conftest.py", "*/conftest.py")


def _mutate(text: str, site: dict[str, Any]) -> str | None:
    lines = text.splitlines(keepends=True)
    i = site["line"] - 1
    if site["kind"] == "remove_assert":
        lines[i] = lines[i][:site["col"]] + "pass\n"
        for j in range(i + 1, site["end_line"]):
            lines[j] = "\n"
    else:
        col, old = site["col"], site["token"]
        replacement = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}[old]
        lines[i] = lines[i][:col] + replacement + lines[i][col + len(old):]
    changed = "".join(lines)
    try:
        ast.parse(changed)
    except SyntaxError:
        return None
    return changed if changed != text else None


def _sites(root: Path, files: list[str], kinds: tuple[str, ...], skipped: list) -> list[dict[str, Any]]:
    sites = []
    for rel in sorted(files):
        if any(fnmatch.fnmatchcase(rel, pattern) for pattern in TEST_FILE):
            continue
        target = root / rel
        if target.is_symlink() or root.resolve() not in target.resolve().parents:
            skipped.append({"path": rel, "line": 0, "kind": "", "reason": "unsafe path"})
            continue
        try:
            text = target.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeError) as exc:
            skipped.append({"path": rel, "line": 0, "kind": "", "reason": str(exc)})
            continue
        lines = text.splitlines()
        spans = []
        for node in ast.walk(tree):
            if "remove_assert" in kinds and isinstance(node, ast.Assert):
                prefix = lines[node.lineno - 1].encode()[:node.col_offset].decode()
                suffix = lines[node.end_lineno - 1].encode()[node.end_col_offset:].decode().strip()
                if not prefix.strip() and (not suffix or suffix.startswith("#")):
                    sites.append({"path": rel, "line": node.lineno, "col": len(prefix),
                                  "kind": "remove_assert", "end_line": node.end_lineno})
            if "flip_boundary" in kinds and isinstance(node, ast.Compare):
                if any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in node.ops):
                    spans.append(((node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset)))
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type != tokenize.OP or token.string not in ("<", "<=", ">", ">="):
                continue
            start = (token.start[0], len(lines[token.start[0] - 1][:token.start[1]].encode()))
            end = (token.end[0], len(lines[token.end[0] - 1][:token.end[1]].encode()))
            if any(a <= start and end <= b for a, b in spans):
                sites.append({"path": rel, "line": token.start[0], "col": token.start[1],
                              "kind": "flip_boundary", "token": token.string})
    return sorted(sites, key=lambda s: (s["path"], s["line"], s["col"], kinds.index(s["kind"])))


def _run_checks(root: Path, checks: list[str], timeout: int, mode: str | None) -> list[dict[str, Any]]:
    results = []
    for check in checks:
        out = sandbox.run(shlex.split(check), root, timeout, mode=mode, prepare=True)
        infra = out.infra or ((out.note or "check did not start") if not out.isolation else "")
        results.append({"check": check, "ok": not infra and bool(out.isolation)
                        and out.returncode == 0 and not out.timed_out,
                        "exit_code": out.returncode, "timed_out": out.timed_out,
                        "infra": infra, "level": out.isolation, "tail": out.output[-300:]})
    return results


def _mutation_level(runs: list[dict[str, Any]]) -> str:
    levels = [r["level"] for r in runs if r["level"]]
    return "L2" if "L2" in levels else ("L3" if levels else "")


def mutation_test(repo: str | Path, base: str, head: str = "HEAD", checks: list[str] | None = None,
                  kinds: tuple[str, ...] = MUTATION_KINDS, max_mutations: int = 20, timeout: int = 120,
                  mode: str | None = None) -> dict[str, Any]:
    """Measure EL-R-08 check sensitivity in a disposable worktree, without changing the source repo."""
    if not checks or any(not shlex.split(check) for check in checks):
        raise ValueError("mutation testing needs non-empty checks")
    if any(kind not in MUTATION_KINDS for kind in kinds):
        raise ValueError("unknown mutation kind")
    if max_mutations < 0:
        raise ValueError("max_mutations must be non-negative")
    repo = Path(repo).resolve()
    result = {"repo": str(repo), "base": base, "head": head, "baseline": [], "sites": 0,
              "truncated": False, "skipped": [], "mutations": [], "per_check": {},
              "overall": {"mutations": 0, "caught": 0, "share": None, "infra": 0}, "level": ""}
    try:
        for key, ref in (("base", base), ("head", head)):
            result[key] = worktrees.git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except worktrees.WorktreeError as exc:
        result["error"] = str(exc)
        return result
    path = settings().worktrees_dir / f"mutation-{uuid4().hex[:12]}"
    path.parent.mkdir(parents=True, exist_ok=True)
    all_runs = []
    try:
        worktrees.git(repo, "worktree", "add", "--detach", "-q", str(path), result["head"])
        worktrees.ensure_sandboxed(path)
        mode = mode or sandbox.mode_for()
        baseline = _run_checks(path, checks, timeout, mode)
        result["baseline"] = baseline
        all_runs.extend(baseline)
        result["level"] = _mutation_level(all_runs)
        for run in baseline:
            if not run["ok"]:
                reason = "did not start (infra)" if run["infra"] else "failed"
                result["error"] = f"baseline: {run['check']} {reason}"
                return result
        files = worktrees.git(path, "diff", "--name-only", "-z", "--diff-filter=AM",
                              result["base"], result["head"], "--", "*.py").split("\0")
        sites = _sites(path, [f for f in files if f], kinds, result["skipped"])
        result["sites"] = len(sites)
        result["truncated"] = len(sites) > max_mutations
        table = {check: {"mutations": 0, "caught": 0, "share": None} for check in checks}
        result["per_check"] = table
        for site in sites[:max_mutations]:
            target = path / site["path"]
            changed = _mutate(target.read_text(encoding="utf-8"), site)
            if changed is None:
                result["skipped"].append({k: site[k] for k in ("path", "line", "kind")} |
                                         {"reason": "invalid or unchanged mutation"})
                continue
            try:
                target.write_text(changed, encoding="utf-8")
                runs = _run_checks(path, checks, timeout, mode)
            finally:
                worktrees.git(path, "checkout", "--", site["path"])
            all_runs.extend(runs)
            caught = [r["check"] for r in runs if not r["infra"] and not r["ok"]]
            ran = [r for r in runs if not r["infra"]]
            mutation = {k: site[k] for k in ("kind", "path", "line", "col")}
            mutation.update(caught_by=caught, survived=bool(ran) and not caught,
                            timed_out=[r["check"] for r in runs if r["timed_out"]],
                            infra=[r["check"] for r in runs if r["infra"]], level=_mutation_level(runs))
            result["mutations"].append(mutation)
            for run in ran:
                row = table[run["check"]]
                row["mutations"] += 1
                row["caught"] += int(not run["ok"])
                row["share"] = _rate(row["caught"], row["mutations"])
            overall = result["overall"]
            overall["mutations"] += int(bool(ran))
            overall["caught"] += int(bool(caught))
            overall["infra"] += int(bool(mutation["infra"]))
            overall["share"] = _rate(overall["caught"], overall["mutations"])
        result["level"] = _mutation_level(all_runs)
        return result
    finally:
        try:
            worktrees.remove(repo, path)
        finally:
            shutil.rmtree(path, ignore_errors=True)
            worktrees.git(repo, "worktree", "prune", check=False)
