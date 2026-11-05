"""Contract Engine: task input/output schemas and acceptance conditions.

Every task runs under a contract. The planner writes it, the engine validates it
before execution, and the Quality Gate checks the acceptance conditions after.

Acceptance condition types:
  contains     {"type": "contains", "value": "..."}       output contains text (case-insensitive)
  regex        {"type": "regex", "pattern": "..."}         output matches a regular expression
  min_length   {"type": "min_length", "value": 200}        output has at least N characters
  json_schema  {"type": "json_schema"}                     output parses as JSON matching output_schema
  file_exists  {"type": "file_exists", "path": "app.py"}   file exists in the worktree
  command      {"type": "command", "run": "pytest -q"}     command exits 0 in the worktree
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, Field, ValidationError, field_validator

ConditionType = Literal["contains", "regex", "min_length", "json_schema", "file_exists", "command"]


class Condition(BaseModel):
    type: ConditionType
    value: str | int | None = None
    pattern: str | None = None
    path: str | None = None
    run: str | None = None

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"invalid regex: {e}") from e
        return v


class TaskBudget(BaseModel):
    cost_usd: float = Field(default=0.0, ge=0)  # 0 = no limit; a run that costs more fails the task
    turns: int = Field(default=0, ge=0)  # agent turns for CLI executors (0 = their default)


class TaskContract(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    title: str = Field(min_length=1, max_length=256)
    role: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = Field(default=5, ge=0, le=9)
    writes: bool = False
    inputs: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    acceptance: list[Condition] = Field(default_factory=lambda: [Condition(type="min_length", value=20)])
    # Task contract minimum fields (guides G-R-01); all optional so older plans stay valid.
    in_scope: list[str] = Field(default_factory=list)  # paths/areas the task works on (told to the agent)
    out_of_scope: list[str] = Field(default_factory=list)  # path globs it must not change: a change fails the task
    risk_level: Literal["low", "medium", "high"] = "medium"  # high adds a security review (ASEAO09 S09-R-02)
    test_plan: str = ""
    budget: TaskBudget = Field(default_factory=lambda: TaskBudget())
    deadline: int = Field(default=0, ge=0)  # seconds the run may take (0 = the command timeout)
    requirement_keys: list[str] = Field(default_factory=list)  # requirement IDs it implements (ASEAO08 S08-R-01)
    # tasks of one iteration that recommend on the same topic are compared; a disagreement is reviewed (S04-R-03)
    decision_topic: str = Field(default="", max_length=128)
    weight: float = Field(default=1.0, ge=0, le=100)  # its share of the goal's progress (ASEAO05 S05-R-05)
    estimated_minutes: int = Field(default=0, ge=0, le=7 * 24 * 60)  # the planner's time estimate (0 = none given)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _valid_schema(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            jsonschema.Draft202012Validator.check_schema(v)
        return v


class ContractError(ValueError):
    pass


def validate(data: dict[str, Any]) -> TaskContract:
    try:
        c = TaskContract.model_validate(data)
    except ValidationError as e:
        raise ContractError(str(e)) from e
    if c.input_schema is not None:
        try:
            jsonschema.validate(c.inputs, c.input_schema)
        except jsonschema.ValidationError as e:
            raise ContractError(f"inputs do not match input_schema: {e.message}") from e
    if any(cond.type == "json_schema" for cond in c.acceptance) and c.output_schema is None:
        raise ContractError("json_schema acceptance needs an output_schema")
    return c


def validate_plan(tasks: list[dict[str, Any]]) -> list[TaskContract]:
    """Validate a whole plan: contracts, unique keys, known dependencies, no cycles."""
    contracts = [validate(t) for t in tasks]
    keys = [c.key for c in contracts]
    if len(set(keys)) != len(keys):
        raise ContractError("task keys must be unique")
    known = set(keys)
    for c in contracts:
        missing = set(c.depends_on) - known
        if missing:
            raise ContractError(f"{c.key} depends on unknown task(s) {sorted(missing)}")
    order = topo_order({c.key: c.depends_on for c in contracts})
    if order is None:
        raise ContractError("task dependencies contain a cycle")
    return contracts


def topo_order(deps: dict[str, list[str]]) -> list[str] | None:
    done: list[str] = []
    state: dict[str, int] = {}

    def visit(k: str) -> bool:
        if state.get(k) == 1:
            return False
        if state.get(k) == 2:
            return True
        state[k] = 1
        for d in deps.get(k, []):
            if not visit(d):
                return False
        state[k] = 2
        done.append(k)
        return True

    for k in deps:
        if not visit(k):
            return None
    return done


def extract_json(text: str) -> Any:
    """Parse JSON from a model answer: a ```json block, or the outermost {...}/[...]."""
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    candidates = [m.group(1)] if m else []
    for open_, close in (("{", "}"), ("[", "]")):
        i, j = text.find(open_), text.rfind(close)
        if i != -1 and j > i:
            candidates.append(text[i:j + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON found in output")


def check(contract: TaskContract, output: str, workdir: Path | None, timeout: int = 600,
          on_refused: Callable[[str, str], None] | None = None, mode: str | None = None) -> list[dict[str, Any]]:
    """`on_refused(cmd, reason)` hears about commands the policy refused (behaviour rules, S14-R-03); `mode` is
    the sandbox mode of command conditions (`sandbox.mode_for`)."""
    results = []
    for cond in contract.acceptance:
        info: dict[str, Any] = {}
        ok, detail = _check_one(cond, contract, output, workdir, timeout, on_refused, mode, info)
        results.append({"condition": cond.model_dump(exclude_none=True), "passed": ok, "detail": detail}
                       | ({"isolation": info["isolation"]} if info.get("isolation") else {}))
    return results


def _check_one(cond: Condition, contract: TaskContract, output: str, workdir: Path | None,
               timeout: int, on_refused: Callable[[str, str], None] | None = None, mode: str | None = None,
               info: dict[str, Any] | None = None) -> tuple[bool, str]:
    if cond.type == "contains":
        ok = str(cond.value or "").lower() in output.lower()
        return ok, "" if ok else f"output does not mention {cond.value!r}"
    if cond.type == "regex":
        ok = re.search(cond.pattern or "", output, re.M) is not None
        return ok, "" if ok else f"output does not match /{cond.pattern}/"
    if cond.type == "min_length":
        n = int(cond.value or 0)
        return len(output.strip()) >= n, f"{len(output.strip())} chars (need {n})"
    if cond.type == "json_schema":
        try:
            jsonschema.validate(extract_json(output), contract.output_schema or {})
            return True, ""
        except (ValueError, jsonschema.ValidationError) as e:
            return False, str(getattr(e, "message", e))
    if workdir is None:
        return False, f"{cond.type} needs a worktree"
    if cond.type == "file_exists":
        target = (workdir / (cond.path or "")).resolve()
        ok = workdir.resolve() in target.parents and target.exists()
        return ok, "" if ok else f"{cond.path} does not exist"
    if cond.type == "command":
        return run_command(cond.run or "true", workdir, timeout, on_refused, mode, info)
    return False, f"unknown condition {cond.type}"


def run_command(cmd: str, workdir: Path, timeout: int,
                on_refused: Callable[[str, str], None] | None = None, mode: str | None = None,
                info: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Run a check command in the worktree: in the L3 sandbox as `mode` (sandbox.MODES, default the company
    setting) allows, with a filtered environment either way. `info` receives the isolation level and its note."""
    from agent_company import sandbox
    from agent_company.governance import audit, check_command
    from agent_company.worktrees import ensure_sandboxed

    cwd = ensure_sandboxed(workdir)
    refused = check_command(cmd)
    if refused:
        audit("quality-gate", "command_refused", cmd[:256], after={"reason": refused}, severity="high")
        if on_refused is not None:
            on_refused(cmd, refused)
        return False, f"`{cmd}` refused by the command policy: {refused}"
    res = sandbox.run(["/bin/sh", "-c", cmd], cwd, timeout, mode=mode, prepare=True)
    if info is not None:
        info.update(isolation=res.isolation, isolation_note=res.note)
    level = f" ({res.isolation})" if res.isolation else ""
    note = f"\n[{res.note}]" if res.note else ""
    if not res.isolation:
        return False, f"`{cmd}` {res.note}"
    if res.timed_out:
        return False, f"`{cmd}` timed out after {timeout}s{level}{note}"
    return res.returncode == 0, f"`{cmd}` exit {res.returncode}{level}{note}\n{res.output[-1500:]}".strip()


def _matches(path: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*").rstrip("/") + "/")


def limits(contract: TaskContract, changed: list[str], cost: float | None) -> list[dict[str, Any]]:
    """The contract's limits, checked after the run like acceptance conditions (G-R-01): files changed
    inside `out_of_scope`, and a run costing more than the task's budget."""
    out = []
    if contract.out_of_scope:
        bad = sorted(p for p in changed if any(_matches(p, pat) for pat in contract.out_of_scope))
        out.append({"condition": {"type": "out_of_scope", "paths": contract.out_of_scope}, "passed": not bad,
                    "detail": ("changed out-of-scope file(s): " + ", ".join(bad)) if bad else ""})
    if contract.budget.cost_usd > 0 and cost is not None:
        over = cost > contract.budget.cost_usd
        out.append({"condition": {"type": "budget", "cost_usd": contract.budget.cost_usd}, "passed": not over,
                    "detail": f"the run cost ${cost:.4f} (task budget ${contract.budget.cost_usd:.4f})" if over else ""})
    return out


_FINDING_LINE = re.compile(r"(?im)^[\s*-]*FINDING:\s*(.+?)\s*$")
_NEXT_LINE = re.compile(r"(?im)^[\s*-]*NEXT(?:[ _]ACTION)?:\s*(.+?)\s*$")
_RECOMMENDATION_LINE = re.compile(r"(?im)^[\s*-]*RECOMMENDATION:\s*(.+?)\s*$")
_BLOCKER_LINE = re.compile(r"(?im)^[\s*-]*BLOCKER:\s*(.+?)\s*$")


def findings(output: str, limit: int = 20) -> list[str]:
    """`FINDING:` lines of a task's output (G-R-01; reviewers use the same format, quality.merge_findings)."""
    return _FINDING_LINE.findall(output or "")[:limit]


def next_actions(output: str, limit: int = 10) -> list[str]:
    """`NEXT:` (or `NEXT ACTION:`) lines of a task's output: follow-up work it names (G-R-01)."""
    return _NEXT_LINE.findall(output or "")[:limit]


def blockers(output: str, limit: int = 3) -> list[str]:
    """`BLOCKER:` lines of a task's output: what stops the work (ASEAO05 S05-R-08); open goals list them as
    project blockers (management.sync)."""
    return [b[:256] for b in _BLOCKER_LINE.findall(output or "")[:limit]]


def recommendation(output: str) -> str:
    """The last `RECOMMENDATION:` line of a task's output, for comparing tasks on one topic (S04-R-03)."""
    found = _RECOMMENDATION_LINE.findall(output or "")
    return found[-1][:500] if found else ""
