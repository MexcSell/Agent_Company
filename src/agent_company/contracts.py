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
import subprocess
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


def check(contract: TaskContract, output: str, workdir: Path | None, timeout: int = 600) -> list[dict[str, Any]]:
    results = []
    for cond in contract.acceptance:
        ok, detail = _check_one(cond, contract, output, workdir, timeout)
        results.append({"condition": cond.model_dump(exclude_none=True), "passed": ok, "detail": detail})
    return results


def _check_one(cond: Condition, contract: TaskContract, output: str, workdir: Path | None,
               timeout: int) -> tuple[bool, str]:
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
        return run_command(cond.run or "true", workdir, timeout)
    return False, f"unknown condition {cond.type}"


def run_command(cmd: str, workdir: Path, timeout: int) -> tuple[bool, str]:
    from agent_company.worktrees import ensure_sandboxed

    from agent_company.governance import audit, check_command

    cwd = ensure_sandboxed(workdir)
    refused = check_command(cmd)
    if refused:
        audit("quality-gate", "command_refused", cmd[:256], after={"reason": refused}, severity="high")
        return False, f"`{cmd}` refused by the command policy: {refused}"
    try:
        import os

        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, f"`{cmd}` timed out after {timeout}s"
    tail = (proc.stdout + proc.stderr)[-1500:]
    return proc.returncode == 0, f"`{cmd}` exit {proc.returncode}\n{tail}".strip()
