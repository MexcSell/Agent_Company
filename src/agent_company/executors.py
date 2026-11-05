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
    agent_id: int | None = None  # who runs it and for which goal: an emergency stop kills by these
    goal_id: int | None = None


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


MAX_OUTPUT = 5_000_000  # characters of stdout an executor run may produce (SPEC-14 S14-R-07)


def _track(pid: int, program: str, kind: str, req: Request) -> None:
    from agent_company.db import LiveProcess, session

    try:
        with session() as s:
            s.merge(LiveProcess(pid=pid, program=Path(program).name[:64], kind=kind, agent_id=req.agent_id,
                                goal_id=req.goal_id))
    except Exception:  # noqa: BLE001 - tracking must never stop the work
        pass


def _untrack(pid: int) -> None:
    from agent_company.db import LiveProcess, session

    try:
        with session() as s:
            row = s.get(LiveProcess, pid)
            if row is not None:
                s.delete(row)
    except Exception:  # noqa: BLE001
        pass


def _signal_group(pid: int, sig: int) -> None:
    import os

    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def terminate(agent_id: int | None = None, goal_id: int | None = None, everything: bool = False,
              grace: float = 3.0) -> list[int]:
    """Terminate tracked executor processes (and their children) of an agent, a goal, or all of them.
    Works from any process: the PIDs are in the database. Returns the PIDs signalled."""
    import os
    import signal

    from sqlalchemy import select

    from agent_company.db import LiveProcess, session

    with session() as s:
        q = select(LiveProcess)
        if not everything:
            if agent_id is None and goal_id is None:
                return []
            if agent_id is not None:
                q = q.where(LiveProcess.agent_id == agent_id)
            if goal_id is not None:
                q = q.where(LiveProcess.goal_id == goal_id)
        rows = [(r.pid, r.program) for r in s.scalars(q)]
    hit = []
    for pid, program in rows:
        try:
            argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
        except OSError:
            _untrack(pid)  # already gone
            continue
        if Path(argv0).name != program:
            _untrack(pid)  # the PID belongs to another program now
            continue
        _signal_group(pid, signal.SIGTERM)
        hit.append(pid)
    deadline = time.monotonic() + grace
    for pid in hit:
        while time.monotonic() < deadline and Path(f"/proc/{pid}").exists():
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:  # our own child: reap it
                    break
            except ChildProcessError:
                pass
            time.sleep(0.05)
        if Path(f"/proc/{pid}").exists():
            _signal_group(pid, signal.SIGKILL)
    return hit


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

    def _spawn(self, cmd: list[str], req: Request, cwd: Path,
               env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        """subprocess.run with a timeout, an output limit and a tracked PID (its own process group), so a
        timeout or an emergency stop ends the CLI and everything it started."""
        import signal

        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                start_new_session=True)
        _track(proc.pid, cmd[0], self.kind, req)
        try:
            out, err = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _signal_group(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        finally:
            _untrack(proc.pid)
        if proc.returncode is not None and proc.returncode < 0:
            err = (err or "") + f"\nterminated by signal {-proc.returncode}"
        if len(out) > MAX_OUTPUT:
            out, err = out[:MAX_OUTPUT], (err or "") + f"\noutput cut at {MAX_OUTPUT} characters"
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


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


def claude_env(choice: Choice) -> dict[str, str] | None:
    """Claude Code at another address: a claude_code model with a base URL (and optionally its key's variable)."""
    import os

    if not choice.base_url and not choice.api_key_env:
        return None
    env = dict(os.environ)
    if choice.base_url:
        env["ANTHROPIC_BASE_URL"] = choice.base_url
    if choice.api_key_env and os.environ.get(choice.api_key_env):
        env["ANTHROPIC_API_KEY"] = os.environ[choice.api_key_env]
    return env


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
        if int(req.data.get("max_turns") or 0) > 0:  # the task contract's turn budget (G-R-01)
            cmd += ["--max-turns", str(int(req.data["max_turns"]))]
        resume = req.data.get("resume")
        if resume and re.fullmatch(r"[0-9a-fA-F-]{8,64}", resume):
            cmd += ["--resume", resume]  # continue this agent's earlier session on the same goal
        elif self.choice.session_id:  # a model pinned to a session: start from its context, never change it
            cmd += ["--resume", self.choice.session_id, "--fork-session"]
        mcp_file = None
        servers, mcp_tools = req.data.get("mcp") or ({}, [])
        if servers.get("mcpServers"):  # the agent's granted servers from Tools & MCP, allowed tools only
            import tempfile

            fd, mcp_file = tempfile.mkstemp(prefix="ac-mcp-", suffix=".json")
            with open(fd, "w") as f:
                json.dump(servers, f)
            cmd += ["--mcp-config", mcp_file, "--strict-mcp-config"]
            cmd[cmd.index("--allowedTools") + 1] += "," + ",".join(mcp_tools)
        try:
            proc = self._spawn(cmd, req, cwd, env=claude_env(self.choice))
        finally:
            if mcp_file:
                Path(mcp_file).unlink(missing_ok=True)
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
        proc = self._spawn([*cmd, prompt], req, cwd)
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
         "description": ("Review the change against the specification: verify the logic first (trace the data flow "
                         "of the normal, an edge and an error case), then the checklist. End with an OUTCOME line "
                         "and VERDICT: PASS or VERDICT: FAIL."),
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
            return (f"Review of '{title}' (offline simulator).\n\n## Logic verification\n"
                    "- Normal case: the worklog records the change and how to verify it.\n"
                    "- Edge case: an empty specification still yields a worklog entry.\n"
                    "- Error case: a missing worklog fails the file_exists condition.\n\n"
                    "## Checklist\n- Contract satisfied; no interfaces, dependencies or security surface changed.\n\n"
                    "OUTCOME: APPROVED\nVERDICT: PASS")
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

    def _summary(self, req: Request) -> str:
        return req.data.get("draft", "")

    def _lesson(self, req: Request) -> str:
        outcome = req.data.get("outcome", "success")
        title = req.data.get("title", "task")
        if outcome == "success":
            return f"For tasks like '{title}', the approach used passed the quality gate; reuse it."
        return f"'{title}' failed the gate: {req.data.get('feedback', 'see report')[:200]}. Check this first next time."

    def _study(self, req: Request) -> str:
        kept = [ln for ln in str(req.data.get("current", "")).splitlines() if ln.startswith("- ")][:6]
        learned = [f"- {x.strip()[:160]}" for x in req.data.get("lessons", [])[:3]]
        read = [f"- Apply what '{d['title'] or d['url']}' teaches where it fits." for d in req.data.get("documents", [])[:1]]
        lines = list(dict.fromkeys(kept + learned + read)) or ["- Read the acceptance conditions before starting."]
        return "\n".join(lines[:10])

    def _mentor(self, req: Request) -> str:
        practice = req.data.get("practice") or {}
        study = ", ".join(f"K{x['id']}" for x in req.data.get("materials", [])[:3]) or "the plan's knowledge items"
        return (f"{practice.get('passed', 0)} of {practice.get('runs', 0)} practice runs on "
                f"'{req.data.get('skill', 'the skill')}' passed. Study {study}, then repeat the exercises that failed "
                "before the final evaluation.")

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
