"""Execution Adapter: one interface over the ways an agent's work can run.

  llm          CrewAI `LLM` (Anthropic, OpenAI, OpenRouter, Ollama, ... by model id).
               Write tasks return files as FILE blocks, which are written into the worktree.
  claude_code  `claude -p` in the worktree. Uses your Claude Code login or ANTHROPIC_API_KEY.
  codex        `codex exec` in the worktree with Codex's own sandbox.
  offline      Deterministic stand-in so the whole pipeline runs and is testable with no model.

Executors that touch files only ever run inside a sandboxed worktree.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_company.router import Choice
from agent_company.worktrees import ensure_sandboxed, write_files


@dataclass
class Request:
    purpose: str  # plan|task|review|lesson|research
    role: str
    system: str
    prompt: str
    workdir: Path | None = None
    writes: bool = False
    data: dict[str, Any] = field(default_factory=dict)  # structured hints for the offline executor


@dataclass
class Result:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None  # None: compute from catalog prices
    ok: bool = True
    error: str = ""
    files: list[str] = field(default_factory=list)
    duration: float = 0.0
    session_id: str = ""


FILE_BLOCK_HELP = (
    "To create or replace a file, write it as a block exactly like this (one block per file):\n"
    "=== FILE: relative/path.ext ===\n<full file contents>\n=== END FILE ===\n"
)
_FILE_RE = re.compile(r"^=== FILE: (.+?) ===\n(.*?)^=== END FILE ===", re.S | re.M)


def parse_file_blocks(text: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2) for m in _FILE_RE.finditer(text)}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class Executor:
    kind = "base"

    def __init__(self, choice: Choice, timeout: int = 1800):
        self.choice = choice
        self.timeout = timeout

    def run(self, req: Request) -> Result:
        start = time.monotonic()
        try:
            res = self._run(req)
        except Exception as e:  # noqa: BLE001 - an executor failure is a task failure, not a crash
            res = Result(text="", ok=False, error=f"{type(e).__name__}: {e}")
        res.duration = time.monotonic() - start
        return res

    def _run(self, req: Request) -> Result:
        raise NotImplementedError


class LLMExecutor(Executor):
    kind = "llm"

    def _run(self, req: Request) -> Result:
        from crewai import LLM

        import os

        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.choice.base_url:  # OpenAI-compatible server: llama-server, LM Studio, vLLM, ...
            kwargs["base_url"] = self.choice.base_url
            kwargs["api_key"] = os.environ.get(self.choice.api_key_env, "") if self.choice.api_key_env else "local"
        elif self.choice.api_key_env and os.environ.get(self.choice.api_key_env):
            kwargs["api_key"] = os.environ[self.choice.api_key_env]
        llm = LLM(model=self.choice.model_id, **kwargs)
        system = req.system + ("\n\n" + FILE_BLOCK_HELP if req.writes else "")
        text = llm.call([{"role": "system", "content": system}, {"role": "user", "content": req.prompt}])
        text = text if isinstance(text, str) else str(text)
        u = llm.get_token_usage_summary()
        files: list[str] = []
        if req.writes and req.workdir is not None:
            blocks = parse_file_blocks(text)
            if blocks:
                files = write_files(req.workdir, blocks)
        return Result(text=text, input_tokens=u.prompt_tokens or estimate_tokens(system + req.prompt),
                      output_tokens=u.completion_tokens or estimate_tokens(text), files=files)


# Claude Code may run tests and inspect git, nothing else, through Bash.
CLAUDE_BASH = ["Bash(pytest:*)", "Bash(python -m pytest:*)", "Bash(uv run:*)", "Bash(npm test:*)",
               "Bash(npm run:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(ls:*)"]


class ClaudeCodeExecutor(Executor):
    kind = "claude_code"

    def _run(self, req: Request) -> Result:
        if req.workdir is None:
            raise RuntimeError("Claude Code needs a worktree")
        cwd = ensure_sandboxed(req.workdir)
        tools = ["Read", "Grep", "Glob"] + (["Edit", "Write", *CLAUDE_BASH] if req.writes else [])
        cmd = ["claude", "-p", req.prompt, "--output-format", "json",
               "--append-system-prompt", req.system, "--allowedTools", ",".join(tools)]
        if req.writes:
            cmd += ["--permission-mode", "acceptEdits"]
        if self.choice.model_id:
            cmd += ["--model", self.choice.model_id]
        resume = req.data.get("resume")
        if resume and re.fullmatch(r"[0-9a-fA-F-]{8,64}", resume):
            cmd += ["--resume", resume]  # continue this agent's earlier session on the same goal
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=self.timeout)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return Result(text=proc.stdout, ok=False, error=(proc.stderr or proc.stdout)[-2000:])
        u = data.get("usage") or {}
        inp = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(
            u.get("cache_creation_input_tokens") or 0)
        return Result(text=str(data.get("result") or ""), input_tokens=inp,
                      output_tokens=int(u.get("output_tokens") or 0),
                      cost=float(data.get("total_cost_usd") or 0.0),
                      ok=not data.get("is_error") and proc.returncode == 0,
                      error="" if proc.returncode == 0 else (proc.stderr or str(data.get("result")))[-2000:],
                      session_id=str(data.get("session_id") or ""))


class CodexExecutor(Executor):
    kind = "codex"

    def _run(self, req: Request) -> Result:
        if req.workdir is None:
            raise RuntimeError("Codex needs a worktree")
        cwd = ensure_sandboxed(req.workdir)
        prompt = req.system + "\n\n" + req.prompt
        cmd = ["codex", "exec", "--skip-git-repo-check", "-C", str(cwd),
               "--sandbox", "workspace-write" if req.writes else "read-only"]
        if self.choice.model_id:
            cmd += ["--model", self.choice.model_id]
        proc = subprocess.run([*cmd, prompt], cwd=cwd, capture_output=True, text=True,
                              timeout=self.timeout)
        text = proc.stdout.strip()
        return Result(text=text, input_tokens=estimate_tokens(prompt), output_tokens=estimate_tokens(text),
                      ok=proc.returncode == 0, error=proc.stderr[-2000:] if proc.returncode else "")


def standard_plan(goal: str) -> dict[str, Any]:
    """Specification -> implementation -> review: the offline plan and the fallback plan."""
    tasks = [
        {"key": "spec", "title": "Write the specification", "role": "architect",
         "description": f"Write a short technical specification for: {goal}",
         "skills": ["architecture"], "acceptance": [{"type": "min_length", "value": 40}]},
        {"key": "build", "title": "Implement the specification", "role": "developer",
         "description": f"Implement: {goal}. Record what you did and how to verify it in WORKLOG.md.",
         "depends_on": ["spec"], "writes": True,
         "skills": ["python"], "acceptance": [{"type": "file_exists", "path": "WORKLOG.md"}]},
        {"key": "review", "title": "Review the implementation", "role": "qa",
         "description": "Review the change against the specification. End with VERDICT: PASS or VERDICT: FAIL.",
         "depends_on": ["build"], "skills": ["review"],
         "acceptance": [{"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}]},
    ]
    return {"summary": f"Plan for {goal}", "tasks": tasks}


class OfflineExecutor(Executor):
    """Produces plausible, deterministic output so the pipeline can run without a model."""

    kind = "offline"

    def _run(self, req: Request) -> Result:
        fn = getattr(self, f"_{req.purpose}", self._task)
        text = fn(req)
        files: list[str] = []
        if req.writes and req.workdir is not None:
            blocks = parse_file_blocks(text)
            if blocks:
                files = write_files(req.workdir, blocks)
        return Result(text=text, input_tokens=estimate_tokens(req.system + req.prompt),
                      output_tokens=estimate_tokens(text), cost=0.0, files=files)

    def _plan(self, req: Request) -> str:
        return json.dumps(standard_plan(req.data.get("goal", "the goal")), indent=2)

    def _task(self, req: Request) -> str:
        title = req.data.get("title", "task")
        feedback = req.data.get("feedback") or ""
        if req.role == "qa" or req.purpose == "review":
            return (f"Review of '{title}' (offline simulator).\n- Checked the worklog against the "
                    "specification.\n- No blocking issues found.\nVERDICT: PASS")
        if req.writes:
            proposal = ("\nPROPOSAL: Add regression tests for this change :: the change has no automated tests yet\n"
                        if "propose" in req.prompt.lower() else "")
            body = (f"# Worklog\n\nTask: {title}\n\n{req.data.get('description', '')}\n\n"
                    + (f"Addressed feedback:\n{feedback}\n" if feedback else "")
                    + "\n(Written by the offline simulator; configure a model for real code.)\n")
            return f"Implemented '{title}'.\n=== FILE: WORKLOG.md ===\n{body}=== END FILE ===\n{proposal}"
        return (f"## {title}\n\nOffline simulator output for the {req.role} role.\n\n"
                f"{req.data.get('description', '')}\n\nKey points:\n1. Keep the scope small.\n"
                "2. Define acceptance tests first.\n3. Report evidence, not claims.\n")

    def _triage(self, req: Request) -> str:
        return f"APPROVE\n'{req.data.get('title', 'the proposal')}' is small and serves the project."

    def _test(self, req: Request) -> str:
        return "OK"

    def _lesson(self, req: Request) -> str:
        outcome = req.data.get("outcome", "success")
        title = req.data.get("title", "task")
        if outcome == "success":
            return f"For tasks like '{title}', the approach used passed the quality gate; reuse it."
        return f"'{title}' failed the gate: {req.data.get('feedback', 'see report')[:200]}. Check this first next time."

    def _report(self, req: Request) -> str:
        docs = req.data.get("documents") or []
        findings = "\n".join(f"{i}. {d['title'] or d['url']} is relevant [{i}]." for i, d in enumerate(docs, 1))
        sources = "\n".join(f"[{i}] {d['title']} - {d['url']} - {d['category']}" for i, d in enumerate(docs, 1))
        return (f"## Objective\n{req.data.get('question', '')}\n\n## Findings\n{findings or 'No material was found.'}\n\n"
                "## Gaps\nThe offline simulator does not read the material; configure a model for a real report.\n\n"
                f"## Recommendation\nRead the sources below.\n\n## Sources\n{sources or '(none)'}\n")

    def _research(self, req: Request) -> str:
        docs = req.data.get("documents") or []
        if not docs:
            return "No collected material matched this goal yet."
        lines = [f"- {d['title'] or d['url']}: {d['text'][:160].strip()}" for d in docs[:5]]
        return "Relevant material:\n" + "\n".join(lines)


EXECUTORS: dict[str, type[Executor]] = {
    "llm": LLMExecutor, "claude_code": ClaudeCodeExecutor, "codex": CodexExecutor,
    "offline": OfflineExecutor,
}


def for_choice(choice: Choice, timeout: int = 1800) -> Executor:
    return EXECUTORS[choice.kind](choice, timeout)
