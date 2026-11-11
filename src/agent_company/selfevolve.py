"""Verified self-evolution of the platform's own code (docs/temp/advanced_self_improving_agent_guide.pdf).

`evolution.py` improves behaviour (playbooks); this module improves the implementation, under an improvement
contract (§19):

  analyse    `weaknesses()` reads failed goals, open defects, eval runs and error events and names the
             responsible layer (§4: model, prompt, code, orchestration, tools, data, environment) and component
  contract   objective, component, layer, kind (§8), research proposal (§5), allowed paths, a fixed benchmark
             (manifest and script copied into the contract, §11) and acceptance criteria fixed at creation
  baseline   the benchmark runs on the base commit in a detached worktree (§19 "record current performance")
  build      a goal on the platform project changes only the allowed paths (tests protected, a human decision
             record holds the merge whatever `require_approval` says); Claude Code does the repository work (§16)
  verify     once the goal passes its gate, the platform, not the coding agent (§10), runs the same benchmark on
             the base commit and on the candidate branch, alternating, and compares them with the weighted score
             (§7); a guarded metric that gets worse, any metric worse than `improvement_max_regression`, a score
             gain under `improvement_min_score` or a change to the protected control plane rejects it
  candidates another candidate can be built for the same contract (§14); the best verified one is deployed
  deploy     an administrator approves: the decision is approved and the goal merges (`loop.approve`); the
             supervisor's safe restart checks and swaps in the new code
  monitor    a refused or unhealthy restart is reverted automatically (`improvement_auto_rollback`, §12); the
             benchmark is re-run on the deployed commit, and a regression there notifies the administrators,
             who can roll back with one click (`git revert -m 1` of the merge)

The protected control plane (§12) is `COMPONENTS` with `protected=True` plus `PROTECTED_EXTRA`: no contract
may name it, and a candidate that touches it is rejected even if its goal's scope check missed it.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
import statistics
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company import sandbox, worktrees
from agent_company.config import REPO_ROOT, settings
from agent_company.db import (Defect, EvalRun, Event, EvolutionEvent, Goal, ImprovementContract, Project, log_event,
                              now, session)
from agent_company.performance import OPEN

PKG = "src/agent_company"

# Components of the platform (§2) and who may change them (§12). Every merge still needs a person; `high_risk`
# ones (orchestration, execution, routing: §12 "orchestrator") are recorded as critical decisions.
COMPONENTS: dict[str, dict[str, Any]] = {
    "planning": {"label": "Planning and orchestration", "paths": [f"{PKG}/loop.py", f"{PKG}/scheduler.py",
                 f"{PKG}/workflow.py", f"{PKG}/contracts.py", f"{PKG}/blackboard.py"], "high_risk": True,
                 "improves": "task decomposition, dependencies, scheduling, parallel execution, recovery"},
    "memory": {"label": "Memory and retrieval", "paths": [f"{PKG}/learning.py"],
               "improves": "retrieval, indexing, ranking, deduplication"},
    "research": {"label": "Research and search", "paths": [f"{PKG}/research.py"],
                 "improves": "crawling, source monitoring, research reports"},
    "tools": {"label": "Tool system", "paths": [f"{PKG}/mcp.py", f"{PKG}/catalog.py", f"{PKG}/mcp_servers/*"],
              "improves": "tool selection, adapters, error handling"},
    "execution": {"label": "Execution engine", "paths": [f"{PKG}/executors.py"], "high_risk": True,
                  "improves": "subprocess management, retries, output handling"},
    "routing": {"label": "Model routing", "paths": [f"{PKG}/router.py", f"{PKG}/usage.py"], "high_risk": True,
                "improves": "model choice, fallback, cost"},
    "organisation": {"label": "Registry and organisation", "paths": [f"{PKG}/registry.py", f"{PKG}/org.py",
                     f"{PKG}/agentdefs.py", f"{PKG}/evolution.py"],
                     "improves": "agent selection, staffing, playbook evolution"},
    "management": {"label": "Management and analysis", "paths": [f"{PKG}/management.py", f"{PKG}/performance.py",
                   f"{PKG}/decisions.py", f"{PKG}/projects.py"],
                   "improves": "milestones, risks, metrics, project conversations"},
    "dashboard": {"label": "Dashboard", "paths": [f"{PKG}/static/*"], "improves": "views and interaction"},
    # the protected control plane (§12): permissions, versioning, evaluation, approval, deployment, rollback
    "security": {"label": "Security and permissions", "protected": True,
                 "paths": [f"{PKG}/auth.py", f"{PKG}/governance.py", f"{PKG}/transport.py", f"{PKG}/api.py",
                           f"{PKG}/recruitment.py"]},
    "evaluation": {"label": "Evaluation and quality gate", "protected": True,
                   "paths": [f"{PKG}/quality.py", f"{PKG}/checks.py", f"{PKG}/evaluation.py",
                             f"{PKG}/selfevolve.py", "benchmarks/*", "evals/*"]},
    "deployment": {"label": "Deployment, versioning and recovery", "protected": True,
                   "paths": [f"{PKG}/supervisor.py", f"{PKG}/gitsync.py", f"{PKG}/worktrees.py", f"{PKG}/system.py",
                             "deploy/*"]},
    "data": {"label": "Database and migrations", "protected": True, "paths": [f"{PKG}/db.py", f"{PKG}/prefs.py",
                                                                              f"{PKG}/config.py"]},
    "host": {"label": "Host tools (Developer page, containers, machines)", "protected": True,
             "paths": [f"{PKG}/workspace.py", f"{PKG}/terminal.py", f"{PKG}/containers.py", f"{PKG}/machines.py",
                       f"{PKG}/files.py"]},
}
PROTECTED_EXTRA = [".env*", "pyproject.toml", "uv.lock", ".github/*", "CLAUDE.md", "data/*"]
LAYERS = ["model", "prompt", "code", "orchestration", "tools", "data", "environment"]
KINDS = ["behavioral", "algorithmic", "architectural"]
DEPLOYED = ("monitoring", "stable")

# §4: the responsible layer from what a failure says; the first matching rule wins.
_LAYER_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("environment", re.compile(r"permission denied|no space left|refused \(|stopped \(E\d|emergency|timed? ?out|"
                               r"connection (refused|reset)|not installed|command not found|out of quota|budget",
                               re.IGNORECASE), "permissions, runtime, quotas or infrastructure"),
    ("model", re.compile(r"rate.?limit|\b429\b|overloaded|context (length|window)|invalid api key|no model|"
                         r"not cleared|model .*(unavailable|error)", re.IGNORECASE), "the model or its provider"),
    ("tools", re.compile(r"\bmcp\b|tool (call|error|failed)|adapter|api error|http \d{3}", re.IGNORECASE),
     "a tool, API or adapter"),
    ("orchestration", re.compile(r"dependenc|skipped: a dependency|conflict|handover|deadlock|blocked by|"
                                 r"wrong (agent|role)|re-?plan", re.IGNORECASE), "task allocation or dependencies"),
    ("data", re.compile(r"retriev|knowledge|stale|no (results|sources)|empty (result|page)|quarantined|"
                        r"injection", re.IGNORECASE), "retrieval or input quality"),
    ("code", re.compile(r"traceback|exception|error:|assert|syntax|import ?error|typeerror|keyerror|attributeerror|"
                        r"tests? fail|failed check|pytest|scope|boundary", re.IGNORECASE), "the implementation"),
    ("prompt", re.compile(r"ambiguous|misunderst|unclear|did not follow|instruction|verdict: fail|incomplete",
                          re.IGNORECASE), "incomplete or ambiguous instructions"),
]
ACTIONS = {
    "model": "change the route or model on the Models page; a code contract will not fix it",
    "prompt": "improve the agent's definition or playbook (behavioural evolution)",
    "code": "write an improvement contract for the component",
    "orchestration": "write an improvement contract for planning and orchestration",
    "tools": "write an improvement contract for the tool system, or fix the tool's server",
    "data": "write an improvement contract for memory or research, or review the knowledge items",
    "environment": "fix the host, quotas or permissions (System page); not an agent change",
}


def _spawn(fn: Callable[[], Any]) -> None:  # tests replace it
    threading.Thread(target=fn, daemon=True, name="selfevolve").start()


_busy: set[int] = set()
_busy_lock = threading.Lock()


def _event(s: Any, stage: str, cid: int, title: str, **detail: Any) -> None:
    s.add(EvolutionEvent(stage=stage[:16], agent_id=None, title=title[:500], detail={"contract": cid, **detail}))


# --- components, layers, weaknesses -----------------------------------------------------------------------

def _match(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*").rstrip("/") + "/")


def protected_paths() -> list[str]:
    return [p for c in COMPONENTS.values() if c.get("protected") for p in c["paths"]] + PROTECTED_EXTRA


def is_protected(path: str) -> bool:
    return any(_match(path, p) for p in protected_paths())


def component_of(path: str) -> str | None:
    for name, c in COMPONENTS.items():
        if any(_match(path, p) for p in c["paths"]):
            return name
    return None


def components() -> list[dict[str, Any]]:
    return [{"name": k, "label": c["label"], "paths": c["paths"], "protected": bool(c.get("protected")),
             "high_risk": bool(c.get("high_risk")), "improves": c.get("improves", "")} for k, c in COMPONENTS.items()]


def diagnose_layer(text: str) -> dict[str, str]:
    """§4: which layer a failure belongs to, and what to change there."""
    for layer, pattern, why in _LAYER_RULES:
        m = pattern.search(text or "")
        if m:
            return {"layer": layer, "reason": f"{why} ('{m.group(0)}')", "action": ACTIONS[layer]}
    return {"layer": "code", "reason": "no specific signal; treated as an implementation gap", "action": ACTIONS["code"]}


_MODULE = re.compile(r"agent_company/([a-z_]+\.py)")


def weaknesses(days: int = 30, limit: int = 12) -> list[dict[str, Any]]:
    """§3 analyse performance, identify weakness: recent failures grouped by layer and component, most first."""
    since = now() - timedelta(days=days)
    found: list[tuple[str, str]] = []  # (text, source)
    with session() as s:
        for g in s.scalars(select(Goal).where(Goal.status == "failed", Goal.created_at >= since)):
            found.append((f"{g.title}\n{g.result or ''}"[:4000], f"goal #{g.id}"))
        for d in s.scalars(select(Defect).where(Defect.status.in_(OPEN))):
            found.append((f"failed check {d.check}: {d.detail}"[:4000], f"defect #{d.id}"))
        for r in s.scalars(select(EvalRun).where(EvalRun.passed.is_(False), EvalRun.created_at >= since)):
            found.append((f"{r.end_reason} {r.status} {r.task}", f"eval {r.label}/{r.task}"))
        for e in s.scalars(select(Event).where(Event.kind == "error", Event.created_at >= since)
                           .order_by(Event.id.desc()).limit(200)):
            found.append((e.message, f"event #{e.id}"))
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for text, source in found:
        diag = diagnose_layer(text)
        comp = next((c for c in (component_of(f"{PKG}/{m}") for m in _MODULE.findall(text)) if c), None)
        if comp is None:
            comp = {"orchestration": "planning", "data": "memory", "tools": "tools", "model": "routing"}.get(
                diag["layer"], "")
        g = groups.setdefault((diag["layer"], comp), {"layer": diag["layer"], "component": comp, "count": 0,
                                                       "examples": [], "action": diag["action"], "reasons": Counter()})
        g["count"] += 1
        g["reasons"][diag["reason"].split(" (")[0]] += 1
        if len(g["examples"]) < 3:
            g["examples"].append({"source": source, "text": text.strip().splitlines()[0][:200] if text.strip() else ""})
    out = sorted(groups.values(), key=lambda g: -g["count"])[:limit]
    for g in out:
        g["reasons"] = [r for r, _ in g["reasons"].most_common(2)]
        g["contract_possible"] = bool(g["component"]) and not COMPONENTS.get(g["component"], {}).get("protected") \
            and g["layer"] not in {"model", "environment", "prompt"}
    return out


# --- benchmarks ---------------------------------------------------------------------------------------------

def _bench_dir(repo: Path | str) -> Path:
    return Path(repo) / "benchmarks"


def list_benchmarks(repo: Path | str = REPO_ROOT) -> list[dict[str, Any]]:
    out = []
    for p in sorted(_bench_dir(repo).glob("*.json")):
        try:
            m = json.loads(p.read_text())
            out.append({"name": m.get("name", p.stem), "component": m.get("component", ""),
                        "description": m.get("description", ""), "weights": m.get("weights", {}),
                        "guard": m.get("guard", [])})
        except (OSError, ValueError):
            continue
    return out


def load_benchmark(repo: Path | str, name: str) -> dict[str, Any]:
    """The manifest plus the script's text, copied into the contract so a candidate cannot redefine success."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise ValueError("benchmark names are letters, digits, '-' and '_'")
    path = _bench_dir(repo) / f"{name}.json"
    if not path.exists():
        raise ValueError(f"no benchmark {name} in {_bench_dir(repo)}")
    m = json.loads(path.read_text())
    script = _bench_dir(repo) / m.get("script", f"{name}.py")
    if script.resolve().parent != _bench_dir(repo).resolve() or not script.exists():
        raise ValueError(f"benchmark {name}: script {m.get('script')} not found next to the manifest")
    weights = {str(k): float(v) for k, v in (m.get("weights") or {}).items()}
    if not weights:
        raise ValueError(f"benchmark {name} has no metric weights")
    text = script.read_text()
    return {"name": m.get("name", name), "component": m.get("component", ""), "description": m.get("description", ""),
            "weights": weights, "guard": [g for g in m.get("guard", []) if g in weights],
            "floors": {str(k): float(v) for k, v in (m.get("floors") or {}).items()},
            "repeats": max(1, min(10, int(m.get("repeats", 3)))), "script_name": script.name, "script": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest()}


def _work_dir(cid: int) -> Path:
    d = settings().data_dir / "improvement" / str(cid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_benchmark(tree: Path, bench: dict[str, Any], work: Path, extra_env: dict[str, str] | None = None
                  ) -> dict[str, Any]:
    """One run of the contract's copy of the benchmark against the source tree `tree` (its src/ first on the
    path, a fresh DATA_DIR). `extra_env` sets settings of the measured run (a retrieval experiment's weights).
    Returns the JSON the script printed."""
    script = work / f"bench-{bench['sha256'][:12]}-{bench['script_name']}"
    if not script.exists():
        script.write_text(bench["script"])
    data = work / f"run-{time.time_ns()}"
    data.mkdir()
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(data), "PYTHONPATH": str(tree / "src"),
           "DATA_DIR": str(data), "BENCH_DATA_DIR": str(data), "EXECUTOR": "offline", "PYTHONDONTWRITEBYTECODE": "1",
           **{k: v for k, v in (extra_env or {}).items() if k.startswith("RETRIEVAL_")}}  # never PATH, DATA_DIR …
    timeout = settings().command_timeout
    try:  # SE-R-22: in the L3 sandbox, the tree and the script read-only, only the fresh DATA_DIR writable
        r = sandbox.run([sys.executable, str(script)], tree, timeout, writable=[data], readonly=[tree, script],
                        env=env)
    finally:
        shutil.rmtree(data, ignore_errors=True)
    if not r.isolation:
        raise RuntimeError(f"benchmark {bench['name']} {r.note}")
    if r.timed_out:
        raise RuntimeError(f"benchmark {bench['name']} timed out after {timeout}s")
    if r.returncode:
        raise RuntimeError(f"benchmark {bench['name']} exited {r.returncode}: {r.output[-1500:]}")
    line = next((x for x in reversed(r.output.strip().splitlines()) if x.startswith("{")), "")
    try:
        out = json.loads(line)
    except ValueError as e:
        raise RuntimeError(f"benchmark {bench['name']} printed no JSON result") from e
    src = out.get("source")
    if src and not Path(src).resolve().is_relative_to(tree.resolve()):
        raise RuntimeError(f"benchmark measured {src}, not the tree under test {tree}")
    missing = [k for k in bench["weights"] if not isinstance(out.get("metrics", {}).get(k), (int, float))]
    if missing:
        raise RuntimeError(f"benchmark {bench['name']} did not report {', '.join(missing)}")
    return out


def _median(runs: list[dict[str, Any]]) -> dict[str, float]:
    keys = runs[0]["metrics"].keys() if runs else []
    return {k: round(statistics.median(float(r["metrics"][k]) for r in runs), 6) for k in keys
            if all(isinstance(r["metrics"].get(k), (int, float)) for r in runs)}


def _checkout(repo: Path | str, commit: str, name: str) -> Path:
    path = settings().worktrees_dir / f"evolve-{name}-{time.time_ns()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    worktrees.git(repo, "worktree", "add", "-q", "--detach", str(path), commit)
    return path


def measure(repo: Path | str, commits: dict[str, str], bench: dict[str, Any], work: Path) -> dict[str, Any]:
    """Run the benchmark `repeats` times on each commit, alternating sides so drift hits both alike."""
    trees = {side: _checkout(repo, sha, side) for side, sha in commits.items()}
    runs: dict[str, list[dict[str, Any]]] = {side: [] for side in commits}
    try:
        for _ in range(bench["repeats"]):
            for side, tree in trees.items():
                runs[side].append(run_benchmark(tree, bench, work))
    finally:
        for tree in trees.values():
            worktrees.remove(repo, tree)
    return {side: {"metrics": _median(rs), "classes": rs[-1].get("classes", {}), "runs": len(rs),
                   "cases": rs[-1].get("cases", [])[:40]} for side, rs in runs.items()}


def compare(base: dict[str, float], cand: dict[str, float], bench: dict[str, Any],
            acceptance: dict[str, Any]) -> dict[str, Any]:
    """§7 weighted score and §11 verdict. Each metric's relative change (against a floor so tiny baselines do
    not explode) is weighted; negative weights are costs (lower is better). Score = weighted mean in percent."""
    rows, total, weight_sum, problems = [], 0.0, 0.0, []
    max_reg = float(acceptance.get("max_regression", 25)) / 100
    for k, w in bench["weights"].items():
        b, c = float(base.get(k, 0.0)), float(cand.get(k, 0.0))
        rel = (c - b) / max(abs(b), bench.get("floors", {}).get(k, 0.01))
        benefit = rel if w >= 0 else -rel  # > 0 means better
        total += abs(w) * benefit
        weight_sum += abs(w)
        guard = k in bench.get("guard", [])
        status = "better" if benefit > 1e-9 else "worse" if benefit < -1e-9 else "same"
        if guard and benefit < -1e-9:
            problems.append(f"critical regression: {k} {b:g} -> {c:g} (guarded)")
        elif benefit < -max_reg:
            problems.append(f"regression: {k} {b:g} -> {c:g} ({benefit:+.0%})")
        rows.append({"metric": k, "baseline": b, "candidate": c, "change_pct": round(100 * benefit, 2),
                     "weight": w, "guard": guard, "status": status})
    score = round(100 * total / weight_sum, 2) if weight_sum else 0.0
    need = float(acceptance.get("min_score", 2.0))
    if score < need:
        problems.append(f"score {score:+.2f}% is below the required {need:+.2f}%")
    return {"score_pct": score, "required_pct": need, "metrics": rows, "problems": problems, "accepted": not problems}


# --- contracts -----------------------------------------------------------------------------------------------

def _row(c: ImprovementContract) -> dict[str, Any]:
    bench = {k: v for k, v in (c.benchmark or {}).items() if k != "script"}
    return {"id": c.id, "title": c.title, "objective": c.objective, "component": c.component,
            "component_label": COMPONENTS.get(c.component, {}).get("label", c.component),
            "high_risk": bool(COMPONENTS.get(c.component, {}).get("high_risk")), "layer": c.layer, "kind": c.kind,
            "proposal": c.proposal or {}, "repo_path": c.repo_path, "project_id": c.project_id,
            "base_branch": c.base_branch, "allowed_paths": c.allowed_paths or [], "benchmark": bench,
            "acceptance": c.acceptance or {}, "baseline_commit": c.baseline_commit, "baseline": c.baseline or {},
            "candidates": c.candidates or [], "status": c.status, "deployment": c.deployment or {},
            "monitor": c.monitor or {}, "rollback_sha": c.rollback_sha, "note": c.note,
            "created_by": c.created_by, "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat() if c.updated_at else None}


def _platform() -> Project | None:
    with session() as s:
        return s.scalars(select(Project).where(Project.protected)).first()


def create(title: str, objective: str, component: str, benchmark: str, layer: str = "code",
           kind: str = "algorithmic", proposal: dict[str, Any] | None = None, allowed_paths: list[str] | None = None,
           repo_path: str = "", project_id: int | None = None, created_by: int | None = None) -> dict[str, Any]:
    """Write an improvement contract (§19). The benchmark and acceptance criteria are fixed here (§11)."""
    if component not in COMPONENTS:
        raise ValueError(f"unknown component {component}; one of {', '.join(COMPONENTS)}")
    if COMPONENTS[component].get("protected"):
        raise ValueError(f"{COMPONENTS[component]['label']} is part of the protected control plane (§12); "
                         "it is changed by people, not by an improvement contract")
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {', '.join(LAYERS)}")
    if layer in {"model", "environment"}:
        raise ValueError(f"a {layer}-layer weakness is not fixed by changing code: {ACTIONS[layer]}")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not title.strip() or not objective.strip():
        raise ValueError("a contract needs a title and an objective")
    if not repo_path:
        p = _platform()
        if p is None:
            raise ValueError("no platform project")
        repo_path, project_id = p.path, p.id
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"{repo} is not a git repository")
    paths = [p.strip() for p in (allowed_paths or COMPONENTS[component]["paths"] + ["tests/*"]) if p.strip()]
    bad = [p for p in paths if is_protected(p) or any(_match(q.rstrip("*").rstrip("/") or q, p)
                                                     for q in protected_paths())]
    if bad:
        raise ValueError(f"allowed paths reach the protected control plane: {', '.join(bad)}")
    bench = load_benchmark(repo, benchmark)
    st = settings()
    with session() as s:
        c = ImprovementContract(
            title=title.strip()[:256], objective=objective.strip(), component=component, layer=layer, kind=kind,
            proposal={k: str(v)[:4000] for k, v in (proposal or {}).items() if v},
            repo_path=str(repo), project_id=project_id,
            base_branch=worktrees.git(repo, "rev-parse", "--abbrev-ref", "HEAD"), allowed_paths=paths,
            benchmark=bench, acceptance={"min_score": st.improvement_min_score,
                                         "max_regression": st.improvement_max_regression},
            created_by=created_by)
        s.add(c)
        s.flush()
        _event(s, "contract", c.id, f"contract #{c.id}: {c.title} ({c.component}, {c.layer} layer)")
        log_event("evolution", f"improvement contract #{c.id} written: {c.title}", s=s)
        return _row(c)


def get(cid: int) -> dict[str, Any]:
    with session() as s:
        c = s.get(ImprovementContract, cid)
        if c is None:
            raise KeyError(f"contract {cid}")
        return _row(c)


def contracts(limit: int = 100) -> list[dict[str, Any]]:
    with session() as s:
        return [_row(c) for c in s.scalars(select(ImprovementContract).order_by(ImprovementContract.id.desc())
                                           .limit(limit))]


def _set(cid: int, **fields: Any) -> None:
    with session() as s:
        c = s.get(ImprovementContract, cid)
        for k, v in fields.items():
            setattr(c, k, v)


def _guarded(cid: int, fn: Callable[[int], Any]) -> Callable[[], None]:
    def run() -> None:
        try:
            fn(cid)
        except Exception as e:  # noqa: BLE001 - the contract records why, nothing waits forever
            log_event("error", f"improvement contract #{cid}: {type(e).__name__}: {e}")
            with session() as s:
                c = s.get(ImprovementContract, cid)
                if c is not None and c.status in {"baseline", "verifying"}:
                    c.status, c.note = "failed", f"{type(e).__name__}: {e}"[:2000]
                    _event(s, "failed", cid, f"contract #{cid}: {e}"[:500])
        finally:
            with _busy_lock:
                _busy.discard(cid)
    return run


def _launch(cid: int, fn: Callable[[int], Any]) -> bool:
    with _busy_lock:
        if cid in _busy:
            return False
        _busy.add(cid)
    _spawn(_guarded(cid, fn))
    return True


def start(cid: int) -> dict[str, Any]:
    """Record the baseline (first time) and build a candidate. Again after a verdict: another candidate (§14)."""
    from agent_company.governance import emergency

    if emergency():
        raise ValueError("the emergency stop is on")
    with session() as s:
        c = s.get(ImprovementContract, cid)
        if c is None:
            raise KeyError(f"contract {cid}")
        if c.status not in {"draft", "verified", "rejected", "failed"}:
            raise ValueError(f"contract is {c.status}")
        c.status, c.note = "baseline", ""
    _launch(cid, _baseline_and_build)
    return get(cid)


def _baseline_and_build(cid: int) -> None:
    c = get(cid)
    repo = Path(c["repo_path"])
    head = worktrees.git(repo, "rev-parse", c["base_branch"])
    with session() as s:
        bench = s.get(ImprovementContract, cid).benchmark
    if c["baseline_commit"] != head or not c["baseline"]:
        base = measure(repo, {"baseline": head}, bench, _work_dir(cid))["baseline"]
        with session() as s:
            row = s.get(ImprovementContract, cid)
            row.baseline_commit, row.baseline = head, base
            _event(s, "baseline", cid, f"contract #{cid}: baseline at {head[:10]}: "
                   + ", ".join(f"{k} {v:g}" for k, v in base["metrics"].items()), metrics=base["metrics"])
        c = get(cid)
    gid, did = _build(c)
    with session() as s:
        row = s.get(ImprovementContract, cid)
        row.candidates = [*(row.candidates or []), {"n": len(row.candidates or []) + 1, "goal_id": gid,
                                                    "decision_id": did, "verdict": "building",
                                                    "at": now().isoformat()}]
        row.status = "building"
        _event(s, "build", cid, f"contract #{cid}: candidate {len(row.candidates)} is goal #{gid}", goal_id=gid)
    from agent_company import loop

    with _busy_lock:  # the goal runs on its own now; its end starts the verification
        _busy.discard(cid)
    loop.enqueue(gid)


def _goal_text(c: dict[str, Any]) -> str:
    b = c["benchmark"]
    prop = c["proposal"]
    lines = [f"IMPROVEMENT CONTRACT #{c['id']} (verified self-evolution)", "",
             f"Objective: {c['objective']}",
             f"Component: {c['component_label']} ({c['component']}); layer: {c['layer']}; kind: {c['kind']}.",
             f"Allowed modifications: {', '.join(c['allowed_paths'])}. Existing tests are protected; add new ones.",
             "Protected (never change): " + ", ".join(protected_paths()) + ".", ""]
    for key in ("limitation", "solution", "affected_files", "benefits", "risks", "tests", "targets"):
        if prop.get(key):
            lines.append(f"{key.replace('_', ' ').capitalize()}: {prop[key]}")
    base = c["baseline"].get("metrics", {})
    lines += ["", (f"Benchmark '{b['name']}' ({b.get('description', '')}) is fixed: a copy taken when this contract "
                   "was written decides, so editing benchmarks/ changes nothing (and is outside your scope)."),
              "Baseline: " + ", ".join(f"{k} {v:g}" for k, v in base.items()),
              "Weights (negative = lower is better): " + ", ".join(f"{k} {w:g}" for k, w in b["weights"].items()),
              f"Guarded (must not get worse): {', '.join(b.get('guard', [])) or 'none'}.",
              (f"Acceptance: weighted score at least {c['acceptance'].get('min_score')}% better, no metric more "
               f"than {c['acceptance'].get('max_regression')}% worse, every quality command passes.")]
    weak = {k: v for k, v in (c["baseline"].get("classes") or {}).items()
            if isinstance(v, dict) and min((x for x in v.values() if isinstance(x, (int, float))), default=1) < 1}
    if weak:
        lines.append("Weakest cases at baseline: " + "; ".join(f"{k}: {json.dumps(v)}" for k, v in weak.items()))
    lines += ["", "Change the actual source code in small, reviewable steps. Optimise the capability, not code volume."]
    return "\n".join(lines)


def _build(c: dict[str, Any]) -> tuple[int, int]:
    from agent_company import decisions, loop

    with session() as s:
        p = s.get(Project, c["project_id"]) if c["project_id"] else None
        checks = list(p.quality_commands or []) if p else []
    gid = loop.create_goal(f"Improve {c['component']}: {c['title']}"[:256], _goal_text(c), c["repo_path"],
                           quality_commands=checks, created_by=c.get("created_by"), project_id=c["project_id"],
                           origin="improvement", allowed_paths=c["allowed_paths"], protect_tests=True,
                           topic="self-evolution")
    d = decisions.record(gid, f"Deploy improvement #{c['id']}: {c['title']}"[:256],
                         "Merge only after the platform's benchmark has verified this candidate against the baseline "
                         "(Evolution -> Code evolution).", reasoning="Verified self-evolution: the coding agent is "
                         "not the judge of its own change (guide §10-§12).",
                         alternatives=["keep the current code (the baseline)"],
                         category="critical" if COMPONENTS[c["component"]].get("high_risk") else "technical",
                         requires_human_approval=True,
                         criteria=["benchmark score of the candidate against the baseline commit",
                                   "the benchmark's guards", "the goal's quality gate"],
                         limitations=["the fixed benchmark covers only what its manifest measures",
                                      "behaviour after the deploy is checked by monitoring, not by the benchmark"])
    return gid, d["id"]


def _contract_of(goal_id: int) -> tuple[int, int] | None:
    """(contract id, candidate index) of an improvement goal."""
    with session() as s:
        for c in s.scalars(select(ImprovementContract).where(ImprovementContract.status.not_in(["cancelled"]))):
            for i, cand in enumerate(c.candidates or []):
                if cand.get("goal_id") == goal_id:
                    return c.id, i
    return None


def _update_candidate(s: Any, c: ImprovementContract, i: int, **fields: Any) -> None:
    cands = [dict(x) for x in c.candidates or []]
    cands[i].update(fields)
    c.candidates = cands


def on_goal_finished(goal_id: int) -> None:
    """Called when a goal ends. An improvement goal that passed its gate is verified; one that failed fails."""
    found = _contract_of(goal_id)
    if not found:
        return
    cid, i = found
    with session() as s:
        g = s.get(Goal, goal_id)
        c = s.get(ImprovementContract, cid)
        if c.candidates[i].get("verdict") != "building":
            return
        if g.status == "awaiting_approval":
            _update_candidate(s, c, i, verdict="verifying")
            c.status = "verifying"
        elif g.status in {"failed", "rejected", "cancelled", "approved"}:
            _update_candidate(s, c, i, verdict="failed", reason=(g.result or g.status)[:500])
            c.status = "verified" if any(x.get("verdict") == "accepted" for x in c.candidates) else "failed"
            _event(s, "failed", cid, f"contract #{cid}: candidate goal #{goal_id} ended {g.status}", goal_id=goal_id)
            return
        else:
            return
    _launch(cid, verify)


def verify(cid: int) -> dict[str, Any]:
    """§11: the platform measures base and candidate on the same fixed benchmark and decides."""
    from agent_company import decisions, loop
    from agent_company.governance import notify

    c = get(cid)
    i = next((n for n, x in enumerate(c["candidates"]) if x.get("verdict") == "verifying"), None)
    if i is None:
        return {}
    cand = c["candidates"][i]
    repo = Path(c["repo_path"])
    with session() as s:
        g = s.get(Goal, cand["goal_id"])
        branch, base_branch = g.branch, g.base_branch or c["base_branch"]
        bench = s.get(ImprovementContract, cid).benchmark
    head = worktrees.git(repo, "rev-parse", branch)
    base = worktrees.git(repo, "merge-base", base_branch, branch)
    changed = [x for x in worktrees.git(repo, "diff", "--name-only", f"{base}...{head}").splitlines() if x]
    touched = [x for x in changed if is_protected(x) and not x.startswith((".claude/", ".mcp.json"))]
    m = measure(repo, {"baseline": base, "candidate": head}, bench, _work_dir(cid))
    report = compare(m["baseline"]["metrics"], m["candidate"]["metrics"], bench, c["acceptance"])
    if touched:
        report["problems"].insert(0, "changes the protected control plane: " + ", ".join(touched))
        report["accepted"] = False
    if not changed:
        report["problems"].insert(0, "the candidate changed no file")
        report["accepted"] = False
    report.update(base_commit=base, candidate_commit=head, changed_files=changed, classes={
        side: m[side]["classes"] for side in m}, runs=m["candidate"]["runs"])
    verdict = "accepted" if report["accepted"] else "rejected"
    with session() as s:
        row = s.get(ImprovementContract, cid)
        _update_candidate(s, row, i, verdict=verdict, commit=head, report=report, verified_at=now().isoformat())
        row.status = "verified" if any(x.get("verdict") == "accepted" for x in row.candidates) else "rejected"
        _event(s, "verify", cid, f"contract #{cid}: candidate {i + 1} {verdict}, score {report['score_pct']:+.2f}% "
               f"(needs {report['required_pct']:+.2f}%)", goal_id=cand["goal_id"], score=report["score_pct"])
        notify(f"Improvement #{cid} candidate {i + 1} {verdict}",
               f"{c['title']}: weighted score {report['score_pct']:+.2f}%. "
               + ("Waiting for an administrator to deploy it." if report["accepted"] else "; ".join(report["problems"])),
               level="success" if report["accepted"] else "warning", source="evolution", goal_id=cand["goal_id"],
               admin_only=True, s=s)
    if not report["accepted"]:
        try:
            decisions.decide(cand["decision_id"], False, "benchmark", "; ".join(report["problems"])[:2000])
        except (KeyError, ValueError):
            pass
        try:
            loop.reject(cand["goal_id"], actor="benchmark")
        except ValueError:
            pass
    return report


def merge_block(goal_id: int) -> str | None:
    """Why an improvement goal may not merge yet (loop.approve asks), or None."""
    found = _contract_of(goal_id)
    if not found:
        return None
    cid, i = found
    c = get(cid)
    if c["candidates"][i].get("verdict") != "accepted":
        return (f"improvement contract #{cid}: candidate {i + 1} is {c['candidates'][i].get('verdict')}; only a "
                "candidate the benchmark verified can be deployed")
    best = _best(c)
    if best is not None and best != i:
        return f"improvement contract #{cid}: candidate {best + 1} scored higher; deploy that one"
    return None


def _best(c: dict[str, Any]) -> int | None:
    ok = [(x["report"]["score_pct"], n) for n, x in enumerate(c["candidates"]) if x.get("verdict") == "accepted"]
    return max(ok)[1] if ok else None


def deploy(cid: int, actor: str) -> dict[str, Any]:
    """Approve the best verified candidate: its decision, then the merge (which records the deployment)."""
    from agent_company import decisions, loop

    c = get(cid)
    if c["status"] != "verified":
        raise ValueError(f"contract is {c['status']}, not verified")
    i = _best(c)
    cand = c["candidates"][i]
    try:
        decisions.decide(cand["decision_id"], True, actor, f"benchmark score {cand['report']['score_pct']:+.2f}%")
    except ValueError:
        pass  # already approved
    out = loop.approve(cand["goal_id"], actor=actor)
    return get(cid) | {"merge_commit": out["merge_commit"]}


def on_merged(goal_id: int, sha: str, actor: str) -> None:
    """loop.approve merged an improvement goal: record the deployment and start monitoring."""
    found = _contract_of(goal_id)
    if not found:
        return
    cid, i = found
    with session() as s:
        c = s.get(ImprovementContract, cid)
        c.deployment = {"goal_id": goal_id, "candidate": i + 1, "sha": sha, "at": time.time(), "actor": actor,
                        "check": "waiting for the safe restart"}
        c.status, c.monitor = "monitoring", {}
        _update_candidate(s, c, i, verdict="deployed")
        _event(s, "deploy", cid, f"contract #{cid}: candidate {i + 1} deployed ({sha[:10]}) by {actor}", goal_id=goal_id)


def rollback(cid: int, actor: str, reason: str = "") -> dict[str, Any]:
    """§12 recovery: revert the deployment's merge commit on the base branch (a new commit; history stays)."""
    from agent_company.gitsync import identity_for
    from agent_company.governance import notify

    c = get(cid)
    if c["status"] not in DEPLOYED or not c["deployment"].get("sha"):
        raise ValueError(f"contract is {c['status']}; nothing deployed to roll back")
    repo, sha = Path(c["repo_path"]), c["deployment"]["sha"]
    current = worktrees.git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if current != c["base_branch"]:
        raise ValueError(f"{repo} is on {current}, not {c['base_branch']}")
    if worktrees.git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError(f"{repo} has uncommitted changes; commit or stash them first")
    parents = worktrees.git(repo, "rev-list", "--parents", "-n", "1", sha).split()
    args = ["revert", "--no-edit"] + (["-m", "1"] if len(parents) > 2 else []) + [sha]
    try:
        worktrees.git(repo, *args, env=worktrees.identity_env(identity_for(c.get("created_by"))))
    except worktrees.WorktreeError as e:
        worktrees.git(repo, "revert", "--abort", check=False)
        raise ValueError(f"the revert of {sha[:10]} conflicts; the repository is unchanged ({e})") from e
    new = worktrees.git(repo, "rev-parse", "HEAD")
    why = reason or "rolled back"
    with session() as s:
        row = s.get(ImprovementContract, cid)
        row.status, row.rollback_sha, row.note = "rolled_back", new, why[:2000]
        _event(s, "rollback", cid, f"contract #{cid}: deployment {sha[:10]} reverted ({new[:10]}) by {actor}: {why}"[:500])
        notify(f"Improvement #{cid} rolled back", f"{c['title']}: {why}", level="warning", source="evolution",
               admin_only=True, s=s)
    return get(cid)


def cancel(cid: int, actor: str) -> dict[str, Any]:
    from agent_company import loop

    c = get(cid)
    if c["status"] in DEPLOYED or c["status"] in {"rolled_back", "cancelled"}:
        raise ValueError(f"contract is {c['status']}")
    for cand in c["candidates"]:
        if cand.get("verdict") in {"building", "verifying", "accepted"}:
            with session() as s:
                st = s.get(Goal, cand["goal_id"]).status
            try:
                loop.reject(cand["goal_id"], actor) if st == "awaiting_approval" else loop.cancel(cand["goal_id"], actor)
            except ValueError:
                pass
    with session() as s:
        row = s.get(ImprovementContract, cid)
        row.status = "cancelled"
        _event(s, "cancel", cid, f"contract #{cid} cancelled by {actor}")
    return get(cid)


# --- monitoring --------------------------------------------------------------------------------------------

def _monitor(cid: int) -> None:
    """After a deployment: did the safe restart take it, and does the deployed commit keep its benchmark?"""
    from agent_company import supervisor
    from agent_company.governance import notify

    c = get(cid)
    dep, mon = dict(c["deployment"]), dict(c["monitor"])
    i = dep["candidate"] - 1
    changed = (c["candidates"][i].get("report") or {}).get("changed_files", [])
    needs_restart = any(f.startswith("src/") and f.endswith(".py") for f in changed)  # the supervisor watches *.py
    st = supervisor.read_state()
    if st and st.get("running") and needs_restart:
        failed_at = st.get("last_error_at") or 0
        if failed_at > dep["at"]:
            dep["check"] = f"refused: {st.get('last_error')}"[:500]
            _set(cid, deployment=dep)
            problem = ""
            if settings().improvement_auto_rollback:
                try:
                    rollback(cid, "monitor", f"the safe restart refused the deployment: {st.get('last_error')}"[:500])
                    return
                except ValueError as e:  # e.g. uncommitted changes: stop retrying, a person takes over
                    problem = f"automatic rollback failed: {e}"
            _set(cid, monitor={**mon, "problems": [f"the safe restart refused the deployment: {st.get('last_error')}"
                                                   [:500]] + ([problem] if problem else [])})
            with session() as s:
                notify(f"Improvement #{cid}: deployment refused", f"{st.get('last_error')}"[:800] + (
                       f" {problem}" if problem else " Roll it back on Evolution -> Code evolution."),
                       level="error", source="evolution", admin_only=True, s=s)
            return
        if (st.get("last_restart") or 0) <= dep["at"]:
            if time.time() - dep["at"] > 3 * 3600:
                dep["check"] = "no restart within 3 hours; restart the service (System page)"
                _set(cid, deployment=dep)
            return  # the restart waits for running goals
        dep["check"] = "running the new code"
    elif not needs_restart:
        dep["check"] = "live (no Python change, no restart needed)"
    else:
        dep["check"] = "not supervised: restart the service to load it"
    if "metrics" not in mon:
        with session() as s:
            bench = s.get(ImprovementContract, cid).benchmark
        got = measure(c["repo_path"], {"deployed": dep["sha"]}, bench, _work_dir(cid))["deployed"]
        verified = (c["candidates"][i].get("report") or {}).get("metrics", [])
        expected = {r["metric"]: r["candidate"] for r in verified}
        guard = compare(expected, got["metrics"], bench, {"min_score": -1e9, "max_regression": 100 *
                                                          max(0.1, c["acceptance"].get("max_regression", 25) / 100)})
        mon = {"metrics": got["metrics"], "problems": guard["problems"], "at": now().isoformat()}
    status = "monitoring" if mon["problems"] else "stable"
    with session() as s:
        row = s.get(ImprovementContract, cid)
        row.deployment, row.monitor = dep, mon
        if status != row.status:
            row.status = status
            if mon["problems"]:
                _event(s, "monitor", cid, f"contract #{cid}: deployed commit regressed: {'; '.join(mon['problems'])}")
                notify(f"Improvement #{cid} regressed after deployment", "; ".join(mon["problems"])
                       + " — roll it back on Evolution -> Code evolution.", level="error", source="evolution",
                       admin_only=True, s=s)
            else:
                _event(s, "stable", cid, f"contract #{cid}: deployment {dep['sha'][:10]} is stable")


def advance() -> int:
    """Move every contract on (the server calls it every half minute): finished goals the hook missed, work a
    restart cut off, deployments to monitor. Returns how many steps it started."""
    from agent_company.governance import emergency

    if emergency():
        return 0
    touched = 0
    with session() as s:
        rows = [(c.id, c.status, c.candidates or [], c.monitor or {}) for c in s.scalars(
            select(ImprovementContract).where(ImprovementContract.status.in_(
                ["baseline", "building", "verifying", "monitoring"])))]
    for cid, status, cands, mon in rows:
        if cid in _busy:
            continue
        if status == "baseline":
            touched += _launch(cid, _baseline_and_build)
        elif status == "building":
            for cand in cands:
                if cand.get("verdict") == "building":
                    on_goal_finished(cand["goal_id"])
                    touched += 1
        elif status == "verifying":
            touched += _launch(cid, verify)
        elif status == "monitoring" and not mon.get("problems"):
            touched += _launch(cid, _monitor)
    return touched


class Monitor(threading.Thread):
    def __init__(self, interval: float = 30.0) -> None:
        super().__init__(daemon=True, name="selfevolve-monitor")
        self.interval = interval
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                advance()
            except Exception as e:  # noqa: BLE001 - logged; the next tick tries again
                log_event("error", f"self-evolution monitor: {e}")


# --- §7 agent score ----------------------------------------------------------------------------------------

# Weights depend on the agent's purpose (§7): research favours quality, coding correctness and reliability.
_W_RESEARCH = {"completion": 2, "quality": 4, "reliability": 1, "efficiency": 1, "latency": 0.5, "cost": 1}
_W_CODING = {"completion": 3, "quality": 4, "reliability": 2, "efficiency": 1.5, "latency": 0.5, "cost": 0.5}
_W_REVIEW = {"completion": 3, "quality": 3, "reliability": 3, "efficiency": 1, "latency": 0.5, "cost": 0.5}
_W_PLANNING = {"completion": 2, "quality": 4, "reliability": 2, "efficiency": 2, "latency": 0.5, "cost": 1}
_W_DEFAULT = {"completion": 3, "quality": 3, "reliability": 2, "efficiency": 1, "latency": 0.5, "cost": 0.5}
ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "default": _W_DEFAULT,
    **dict.fromkeys(("research", "external_data", "analyst", "comprehensive_analyst", "student"), _W_RESEARCH),
    **dict.fromkeys(("developer", "frontend", "devops", "tech_scout"), _W_CODING),
    **dict.fromkeys(("qa", "security", "network_security", "security_guard", "tech_introducer"), _W_REVIEW),
    **dict.fromkeys(("general_manager", "architect"), _W_PLANNING),
    **dict.fromkeys(("administrator", "agent_manager", "instructor", "product", "sales"), _W_DEFAULT),
}


def agent_score(m: dict[str, Any]) -> float | None:
    """0-100: w1 completion + w2 quality + w3 reliability + w4 efficiency - w5 latency - w6 cost, each term on
    0..1 (latency: share of 10 minutes per task; cost: share of $1 per task), normalised by the weights."""
    if not m.get("tasks"):
        return None
    w = ROLE_WEIGHTS.get(m.get("role", ""), ROLE_WEIGHTS["default"])
    terms = {"completion": m.get("completion_rate"), "quality": m.get("goal_pass_rate"),
             "reliability": m.get("reliability"),
             "efficiency": None if m.get("rework_rate") is None else 1 - m["rework_rate"]}
    costs = {"latency": None if m.get("avg_task_seconds") is None else min(1.0, m["avg_task_seconds"] / 600),
             "cost": min(1.0, (m.get("cost") or 0) / m["tasks"])}
    gain = sum(w[k] * v for k, v in terms.items() if v is not None)
    loss = sum(w[k] * v for k, v in costs.items() if v is not None)
    top = sum(w[k] for k, v in terms.items() if v is not None)
    return round(100 * max(0.0, gain - loss) / top, 1) if top else None


def overview() -> dict[str, Any]:
    st = settings()
    return {"components": components(), "protected_paths": protected_paths(), "layers": LAYERS, "kinds": KINDS,
            "benchmarks": list_benchmarks(_platform().path if _platform() else REPO_ROOT),
            "contracts": contracts(), "weaknesses": weaknesses(),
            "acceptance": {"min_score": st.improvement_min_score, "max_regression": st.improvement_max_regression,
                           "auto_rollback": st.improvement_auto_rollback}}

