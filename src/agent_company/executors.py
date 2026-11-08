"""Execution Adapter: one interface over the ways an agent's work can run.

  llm          CrewAI `LLM` (Anthropic, OpenAI, OpenRouter, Ollama, ... by model id).
               Write tasks return files as FILE blocks, which are written into the worktree.
  claude_code  `claude -p` in the worktree. Uses your Claude Code login or ANTHROPIC_API_KEY.
  codex        `codex exec` in the worktree with Codex's own sandbox.
  copilot      `copilot -p` (GitHub Copilot CLI) in the worktree; only the granted tools are allowed.
  offline      Deterministic stand-in so the whole pipeline runs and is testable with no model.

Executors that touch files only ever run inside a sandboxed worktree.
"""

from __future__ import annotations

import json
import re
import shlex
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
    # the running agent's permissions and autonomy level; a request without them (an internal summary, a model
    # test) is read-only at G1. `gate` turns them into what the run may use (SPEC-14 S14-R-06)
    permissions: list[str] | None = None
    autonomy: str = ""
    grant: Grant | None = None  # set by Executor.run


@dataclass
class Grant:
    """What one run may use, decided by `gate` before it starts."""

    writes: bool = False
    shell: bool = False
    checks: bool = False  # the run may run the syntax check and the test commands, without writes (EL-R-05)
    mcp: bool = False
    isolation: str = "L0"  # L0 text only, L1 read-only tools, L2 writes in the worktree (S14-R-07)
    permissions: list[str] = field(default_factory=list)  # the permissions this run uses
    dropped: list[str] = field(default_factory=list)  # asked for and not granted
    refused: str = ""  # non-empty: the run does not start


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
    isolation: str = ""  # from the run's Grant
    tools: list[str] = field(default_factory=list)  # the tools the run was allowed
    permissions: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)  # tool calls the CLI refused (Claude Code reports them)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # each call the run made: tool, input, ok
    served_model: str = ""  # the model the provider says answered ("" when it does not say)
    trace: dict[str, Any] = field(default_factory=dict)  # which model did the work and why (Run.trace)


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


# --- the tool calls of a CLI run (SPEC-04 S04-R-12, SPEC-14 S14-R-08) ------------------------------------------
# Claude Code (stream-json) and Codex (--json) report every call as it happens; a run keeps each one's tool, a short
# input (the command, the file, the pattern) with anything credential-like redacted, and whether it failed.

MAX_TOOL_CALLS, TOOL_INPUT_CAP = 200, 200
_INPUT_KEYS = ("command", "file_path", "notebook_path", "path", "pattern", "url", "query", "description", "skill")


def _redact(text: str) -> str:
    from agent_company.checks import PLACEHOLDER, SECRET_PATTERNS

    for _, pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(0) if PLACEHOLDER.search(m.group(0)) else "[redacted]", text)
    return text


def tool_call(tool: str, detail: Any, ok: bool = True) -> dict[str, Any]:
    if isinstance(detail, dict):
        detail = next((detail[k] for k in _INPUT_KEYS if detail.get(k)), json.dumps(detail)[:TOOL_INPUT_CAP * 2])
    return {"tool": str(tool)[:80], "input": _redact(" ".join(str(detail).split()))[:TOOL_INPUT_CAP], "ok": bool(ok)}


def _capped(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(calls) <= MAX_TOOL_CALLS:
        return calls
    return calls[:MAX_TOOL_CALLS] + [{"tool": "…", "input": f"{len(calls) - MAX_TOOL_CALLS} more call(s) not kept",
                                      "ok": True}]


def _events(stdout: str) -> list[dict[str, Any]]:
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def parse_claude_stream(stdout: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(the result event, the tool calls) of `claude -p --output-format stream-json --verbose`; the single object of
    `--output-format json` is read as the result."""
    result: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for ev in _events(stdout):
        kind = ev.get("type")
        if kind == "result" or (kind is None and "result" in ev):
            result = ev
        elif kind in ("assistant", "user"):
            for block in (ev.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    c = tool_call(block.get("name") or "?", block.get("input") or "")
                    calls.append(c)
                    by_id[str(block.get("id"))] = c
                elif block.get("type") == "tool_result" and block.get("is_error"):
                    if str(block.get("tool_use_id")) in by_id:
                        by_id[str(block.get("tool_use_id"))]["ok"] = False
    return result, _capped(calls)


def claude_served(stdout: str) -> str:
    """The model the API answered with in a stream-json run: its assistant messages' `model` (the session's init
    event names the requested one, the fallback when no message came)."""
    served = init = ""
    for ev in _events(stdout):
        if ev.get("type") == "assistant":
            served = str((ev.get("message") or {}).get("model") or "") or served
        elif ev.get("type") == "system" and ev.get("subtype") == "init":
            init = str(ev.get("model") or "")
    return served or init


class StuckWatch:
    """FW-R-02 (after OpenHands' stuck detection, Magentic-One's `max_stalls`, mini-swe-agent's limits): reads a CLI's
    event stream line by line while it runs and names why the run must end: the same tool call (tool + input)
    `repeat_limit` times in a row, or the same call failing with the same error `error_limit` times. 0 = that rule off.
    `kind` is "claude" (stream-json) or "codex" (`exec --json`)."""

    def __init__(self, kind: str, repeat_limit: int, error_limit: int) -> None:
        from collections import Counter

        self.kind, self.repeat_limit, self.error_limit = kind, repeat_limit, error_limit
        self.last, self.repeats, self.reason = "", 0, ""
        self.errors: Counter[str] = Counter()
        self.calls: dict[str, tuple[str, str]] = {}  # tool_use id -> (key, tool name)

    @property
    def active(self) -> bool:
        return self.repeat_limit > 0 or self.error_limit > 0

    def _call(self, name: str, key: str) -> None:
        self.repeats = self.repeats + 1 if key == self.last else 1
        self.last = key
        if 0 < self.repeat_limit <= self.repeats:
            self.reason = f"stuck: {name} repeated {self.repeats} times in a row with the same input"

    def _failed(self, name: str, key: str, error: str) -> None:
        k = key + "\0" + " ".join(error.split())[:300]
        self.errors[k] += 1
        if 0 < self.error_limit <= self.errors[k]:
            self.reason = f"stuck: {name} failed {self.errors[k]} times with the same error"

    def feed(self, line: str) -> str:
        """The reason to stop after this line, or ""."""
        line = line.strip()
        if self.reason or not line.startswith("{"):
            return self.reason
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return ""
        if not isinstance(ev, dict):
            return ""
        if self.kind == "codex":
            item = ev.get("item") or {}
            if ev.get("type") == "item.completed" and item.get("type") == "command_execution":
                key = "command\0" + str(item.get("command") or "")
                self._call("command", key)
                if not self.reason and item.get("exit_code") not in (0, None):
                    self._failed("command", key, f"{item.get('exit_code')} {str(item.get('aggregated_output') or '')[-300:]}")
            return self.reason
        for block in (ev.get("message") or {}).get("content") or [] if ev.get("type") in ("assistant", "user") else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "?")
                key = name + "\0" + json.dumps(block.get("input"), sort_keys=True, default=str)
                self.calls[str(block.get("id"))] = (key, name)
                self._call(name, key)
            elif block.get("type") == "tool_result" and block.get("is_error"):
                key, name = self.calls.get(str(block.get("tool_use_id")), ("", ""))
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content, default=str)
                if key:
                    self._failed(name, key, text)
            if self.reason:
                break
        return self.reason


def stuck_watch(kind: str) -> StuckWatch:
    from agent_company.config import settings

    s = settings()
    return StuckWatch(kind, int(getattr(s, "stuck_repeat_limit", 5)), int(getattr(s, "stuck_error_limit", 3)))


def parse_codex_stream(stdout: str) -> dict[str, Any] | None:
    """text, usage, tool calls and error of `codex exec --json`; None when stdout holds no events (plain text)."""
    events = _events(stdout)
    if not any(str(ev.get("type", "")).startswith(("thread.", "turn.", "item.")) for ev in events):
        return None
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    error = ""
    for ev in events:
        kind = ev.get("type")
        item = ev.get("item") or {}
        if kind == "item.completed":
            k = item.get("type")
            failed = item.get("status") == "failed"
            if k == "agent_message":
                texts.append(str(item.get("text") or ""))
            elif k == "command_execution":
                calls.append(tool_call("command", item.get("command") or "",
                                       not failed and item.get("exit_code") in (0, None)))
            elif k == "file_change":
                calls += [tool_call(f"file_{ch.get('kind') or 'change'}", ch.get("path") or "", not failed)
                          for ch in item.get("changes") or [] if isinstance(ch, dict)]
            elif k == "mcp_tool_call":
                calls.append(tool_call(f"mcp:{item.get('server') or '?'}/{item.get('tool') or '?'}",
                                       item.get("arguments") or "", not failed))
            elif k == "web_search":
                calls.append(tool_call("web_search", item.get("query") or ""))
            elif k == "error":
                error = str(item.get("message") or "")
        elif kind == "turn.completed":
            usage = ev.get("usage") or {}
        elif kind == "turn.failed":
            error = str((ev.get("error") or {}).get("message") or "turn failed")
        elif kind == "error":
            error = str(ev.get("message") or "")
    return {"text": texts[-1] if texts else "", "usage": usage, "tool_calls": _capped(calls), "error": error}


# --- the tool gateway (SPEC-14 S14-R-02, S14-R-06, S14-R-07) ----------------------------------------
# Every run passes `Executor.run`, so every path is covered: agent permission -> autonomy level -> emergency
# state -> approval -> run with limits -> recorded on the run (usage.record) and audited when something is
# withheld (loop._execute). The Workspace terminal and Claude Code panel are not executors and never pass here.

# Autonomy levels of agents (SPEC-14 §13). G5 (unbounded) is reserved to humans and cannot be given to an agent.
AUTONOMY = {
    "G0": "observe: no runs",
    "G1": "recommend: text and read-only tools; no file changes, no commands",
    "G2": "low-risk operations: changes in the goal's worktree and the test/inspect commands",
    "G3": "approved workflows: G2 and the MCP tools an administrator granted",
    "G4": "bounded autonomy: the tools of G3; the highest level an agent can have",
}


@dataclass(frozen=True)
class ToolContract:
    """What an executor can do, who may use it and its limits."""

    capabilities: tuple[str, ...]
    max_seconds: int  # a hard cap above the configured command timeout
    roles: tuple[str, ...] = ()  # empty: every role
    requires_approval: bool = False  # a run needs `data["approved"]` (none of today's executors)
    write_needs_shell: bool = False  # it cannot change files without also running commands


TOOL_CONTRACTS = {
    "llm": ToolContract(("text", "file_blocks"), 7200),
    "claude_code": ToolContract(("text", "read", "edit", "bash", "mcp"), 7200),
    "codex": ToolContract(("text", "read", "edit", "commands"), 7200, write_needs_shell=True),
    "copilot": ToolContract(("text", "read", "edit", "commands"), 7200),
    "offline": ToolContract(("text", "file_blocks"), 600),
}
CLI_KINDS = {"claude_code", "codex", "copilot"}


def level(autonomy: str) -> int:
    """The number of an autonomy level; anything that is not G0..G4 counts as G1."""
    return int(autonomy[1]) if re.fullmatch(r"G[0-4]", autonomy or "") else 1


def tools_for(permissions: list[str] | None, autonomy: str) -> list[str]:
    """The Claude Code tools an agent can be given at most (a writing run with MCP granted): what its definition
    file and its exported subagent name. `network` adds none: agents get no web tools."""
    n, perms = level(autonomy), set(permissions or [])
    if n == 0:
        return []
    writes = "write" in perms and n >= 2
    return (["Read", "Grep", "Glob"] + (["Edit", "Write"] if writes else [])
            + (["Bash"] if writes and "shell" in perms else []) + (["MCP tools granted in Tools & MCP"] if n >= 3 else []))


def gate(req: Request, kind: str) -> Grant:
    """What this run may use: the request's wishes cut down to the agent's permissions and autonomy level."""
    contract = TOOL_CONTRACTS.get(kind, ToolContract(("text",), 600))
    perms = set(req.permissions if req.permissions is not None else ["read"])
    n = level(req.autonomy)
    if contract.roles and req.role not in contract.roles:
        return Grant(refused=f"refused by the tool gateway: {kind} is not allowed for the role {req.role}")
    if n == 0:
        return Grant(refused="refused by the tool gateway: autonomy G0 (observe) runs nothing")
    if contract.requires_approval and not req.data.get("approved"):
        return Grant(refused=f"refused by the tool gateway: {kind} runs need an approval")
    dropped: list[str] = []
    # the CLI General Manager's configure step edits .claude/ (everything else is put back by
    # specs.revert_outside_config); it is the only write an agent without `write` may make
    configure = req.purpose == "configure" and req.role == "general_manager"
    writes = req.writes and req.workdir is not None
    if writes and "write" not in perms and not configure:
        writes = False
        dropped.append("write: no write permission")
    if writes and n < 2:
        writes = False
        dropped.append(f"write: autonomy G{n}")
    shell_ok = kind in CLI_KINDS and "shell" in perms and n >= 2
    if writes and contract.write_needs_shell and not shell_ok:
        writes = False
        dropped.append(f"write: {kind} cannot change files without running commands, and shell is not granted")
    # commands (Claude Code's test/inspect allow-list, Codex's workspace-write) only come with a writing run, as
    # before the gateway; Codex's read-only sandbox may still run read-only commands, which is Codex's own design
    shell = shell_ok and writes
    if writes and kind in CLI_KINDS and not shell and not configure:  # configure only edits .claude/
        dropped.append("shell: no shell permission")
    # a run without writes (a QA review) may still run the check list: syntax check, pytest, read-only git
    checks = shell_ok and req.workdir is not None and not configure and CHECK_OK
    if not req.writes and kind in CLI_KINDS and "shell" in perms and not checks:  # a writing request says why above
        dropped.append(f"checks: autonomy G{n}" if n < 2 else "check: path not usable in a rule" if not CHECK_OK
                       else "checks: no worktree" if req.workdir is None else "checks: configure step")
    servers, _ = req.data.get("mcp") or ({}, [])
    mcp = kind == "claude_code" and bool(servers.get("mcpServers")) and n >= 3
    if servers.get("mcpServers") and kind == "claude_code" and not mcp:
        dropped.append(f"mcp: autonomy G{n}")
    used = sorted({"read"} | ({"write"} if writes else set()) | ({"shell"} if shell or checks else set()))
    if configure and writes and "write" not in perms:
        used = [p if p != "write" else "write:.claude" for p in used]
    isolation = "L2" if writes else "L1" if kind in CLI_KINDS else "L0"
    return Grant(writes=writes, shell=shell, checks=checks, mcp=mcp, isolation=isolation, permissions=used,
                 dropped=dropped)


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
        grant, wanted = Grant(refused="not gated"), req.writes
        try:
            grant = gate(req, self.kind)
            if grant.refused:
                raise PermissionError(grant.refused)
            from agent_company.governance import stopped

            # also for runs that do not pass the loop; an administrator's model test is not autonomous work
            why = stopped(agent_id=req.agent_id, executor=self.kind) if req.purpose != "test" else None
            if why:
                raise PermissionError(f"emergency stop: {why}")
            req.grant, req.writes = grant, grant.writes
            self.timeout = min(self.timeout, TOOL_CONTRACTS.get(self.kind, ToolContract((), 600)).max_seconds)
            res = self._run(req)
        except Exception as e:  # noqa: BLE001 - an executor failure is a task failure, not a crash
            res = Result(text="", ok=False, error=f"{type(e).__name__}: {e}")
        finally:
            req.writes = wanted  # the next executor of a fallback chain is gated on its own
        res.duration = time.monotonic() - start
        res.isolation, res.permissions, res.dropped = grant.isolation, grant.permissions, grant.dropped
        if not res.tools:
            res.tools = self.tools(grant) if not grant.refused else []
        c = self.choice
        res.trace = {"requested": c.requested, "model": c.name, "model_id": c.model_id, "provider": c.provider,
                     "base_url": c.base_url, "route": c.reason[:300], "served": res.served_model}
        return res

    def tools(self, grant: Grant) -> list[str]:
        """The tools a run with this grant is given (recorded on the run, S14-R-08)."""
        return ["text"] + (["file_blocks"] if grant.writes else [])

    def _run(self, req: Request) -> Result:
        raise NotImplementedError

    def _spawn(self, cmd: list[str], req: Request, cwd: Path, env: dict[str, str] | None = None,
               watch: StuckWatch | None = None) -> subprocess.CompletedProcess[str]:
        """subprocess.run with a timeout, an output limit and a tracked PID (its own process group), so a
        timeout or an emergency stop ends the CLI and everything it started. With `watch`, stdout is read line by
        line and a run the watch finds stuck is ended (FW-R-02); its reason is left on the watch."""
        import signal

        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env, start_new_session=True)
        _track(proc.pid, cmd[0], self.kind, req)
        try:
            if watch is not None and watch.active:
                out, err = self._watched(proc, watch, req)
            else:
                out, err = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _signal_group(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        finally:
            _untrack(proc.pid)
        if proc.returncode is not None and proc.returncode < 0:
            err = (err or "") + f"\nterminated by signal {-proc.returncode}"
        if len(out) > MAX_OUTPUT:  # keep the head and the tail: a stream's result event comes last
            half = MAX_OUTPUT // 2
            out = out[:half] + "\n[output cut]\n" + out[-half:]
            err = (err or "") + f"\noutput cut to {MAX_OUTPUT} characters"
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


    def _watched(self, proc: subprocess.Popen[str], watch: StuckWatch, req: Request) -> tuple[str, str]:
        """Read a running CLI's stdout line by line through `watch`; end its process group once the watch names a
        reason (SIGTERM, SIGKILL 5 s later) or the timeout passes."""
        import signal
        import threading

        err: list[str] = []
        reader = threading.Thread(target=lambda: err.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
        reader.start()
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            _signal_group(proc.pid, signal.SIGKILL)

        timer = threading.Timer(self.timeout, expire)
        timer.daemon = True
        timer.start()
        lines: list[str] = []
        try:
            for line in proc.stdout or []:
                lines.append(line)
                if not watch.reason and watch.feed(line):
                    from agent_company.db import log_event

                    log_event("executor", f"{self.kind} run ended: {watch.reason}", goal_id=req.goal_id,
                              agent_id=req.agent_id)
                    _signal_group(proc.pid, signal.SIGTERM)
                    killer = threading.Timer(5, _signal_group, (proc.pid, signal.SIGKILL))
                    killer.daemon = True
                    killer.start()
            proc.wait()
        finally:
            timer.cancel()
        reader.join(10)
        if timed_out.is_set():
            raise subprocess.TimeoutExpired(proc.args, self.timeout)
        return "".join(lines), "".join(err)


class LLMExecutor(Executor):
    kind = "llm"

    def _run(self, req: Request) -> Result:
        from crewai import LLM

        import os

        from agent_company.router import key_for

        kwargs: dict[str, Any] = {"timeout": self.timeout}
        key = key_for(self.choice.name)  # the model's own key (Models page), else its variable's
        if self.choice.base_url:  # OpenAI-compatible server: llama-server, LM Studio, vLLM, ...
            kwargs["base_url"] = self.choice.base_url
            kwargs["api_key"] = key or ("" if self.choice.api_key_env else "local")
        elif key:
            kwargs["api_key"] = key
        served = _ServedModel()
        try:
            llm = LLM(model=self.choice.model_id, interceptor=served, **kwargs)
        except Exception:  # noqa: BLE001 - a provider class without interceptors: the run matters more than its trace
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
                      output_tokens=u.completion_tokens or estimate_tokens(text), files=files,
                      served_model=served.model)


def _ServedModel() -> Any:  # noqa: N802 - builds the interceptor class lazily (crewai imports slowly)
    """A crewai transport interceptor that keeps the `model` of the provider's JSON answers: who actually served."""
    import httpx
    from crewai.llms.hooks.base import BaseInterceptor

    class Served(BaseInterceptor[httpx.Request, httpx.Response]):
        model = ""

        def on_outbound(self, message: httpx.Request) -> httpx.Request:
            return message

        def on_inbound(self, message: httpx.Response) -> httpx.Response:
            if message.status_code < 300 and "json" in message.headers.get("content-type", ""):
                message.read()
                try:
                    self.model = str(json.loads(message.content).get("model") or "") or self.model
                except (ValueError, AttributeError):
                    pass
            return message

        async def aon_outbound(self, message: httpx.Request) -> httpx.Request:
            return message

        async def aon_inbound(self, message: httpx.Response) -> httpx.Response:
            return message

    return Served()


EDIT_CHECK = Path(__file__).with_name("edit_check.py")  # the server's copy, never the worktree's
CHECK_CMD = f"python3 -I {EDIT_CHECK}"
CHECK_OK = bool(re.fullmatch(r"[A-Za-z0-9_./-]+", str(EDIT_CHECK)))  # rule syntax has no quoting
CHECK_HINT = (f"Syntax check: run `{CHECK_CMD} <files>` (node --check for each .js file, compile() for each .py "
              "file); a raw node command is refused.")

# Claude Code may run tests, the syntax check and inspect git, nothing else, through Bash. `node` only through the
# platform's wrapper: a `Bash(node --check:*)` prefix rule would also allow -r/--require/--import/--loader.
# pytest as the planner writes it in acceptance commands: `python3 -B -m pytest` leaves no __pycache__ (V20)
PYTEST_FORMS = ["pytest", "python -m pytest", "python3 -m pytest", "python3 -B -m pytest"]
CLAUDE_BASH = [*(f"Bash({p}:*)" for p in PYTEST_FORMS), "Bash(uv run:*)", "Bash(npm test:*)",
               "Bash(npm run:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(ls:*)"]
# a run without writes: no `uv run:*`/`npm run:*`/`npm test:*`, which run any code (and so could write files)
CLAUDE_CHECK = [*(f"Bash({p}:*)" for p in PYTEST_FORMS), "Bash(uv run pytest:*)", "Bash(git status:*)",
                "Bash(git diff:*)", "Bash(ls:*)"]
if CHECK_OK:
    CLAUDE_BASH.append(f"Bash({CHECK_CMD}:*)")
    CLAUDE_CHECK.insert(0, f"Bash({CHECK_CMD}:*)")
# `git diff --output=<path>` writes a file; a deny rule is a prefix too, so `git diff HEAD --output=x` is a known gap
CLAUDE_CHECK_DENY = ["Bash(git diff --output:*)"]


def claude_env(choice: Choice) -> dict[str, str] | None:
    """Claude Code at another address: a claude_code model with a base URL (and optionally its key's variable),
    or with its own sign-in (`account`: its CLAUDE_CONFIG_DIR, where the CLI also refreshes the token)."""
    import os

    if not choice.base_url and not choice.api_key_env and not choice.account:
        return None
    env = dict(os.environ)
    if choice.account:
        from agent_company.router import claude_config_dir

        env["CLAUDE_CONFIG_DIR"] = str(claude_config_dir(choice.account))
    if choice.base_url:
        env["ANTHROPIC_BASE_URL"] = choice.base_url
    if choice.api_key_env and os.environ.get(choice.api_key_env):
        env["ANTHROPIC_API_KEY"] = os.environ[choice.api_key_env]
    return env


def codex_env(choice: Choice) -> dict[str, str] | None:
    """Codex with its own ChatGPT sign-in: a codex model's `account` is its CODEX_HOME, data/codex-accounts/<account>/
    (where the CLI also refreshes the token)."""
    import os

    if not choice.account:
        return None
    from agent_company.router import codex_home

    return {**os.environ, "CODEX_HOME": str(codex_home(choice.account))}


# EL-R-05: after each edit the file is parsed (never run) and a syntax error goes back to the agent at once
EDIT_CHECK_SETTINGS = {"hooks": {"PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [
    {"type": "command", "timeout": 30,
     "command": f"python3 -I {shlex.quote(str(Path(__file__).with_name('edit_check.py')))}"}]}]}}


def with_check_hint(system: str, grant: Grant) -> str:
    """The system prompt plus the syntax check's command line (an absolute path) when the run may run it."""
    return f"{system}\n\n{CHECK_HINT}" if (grant.shell or grant.checks) and CHECK_OK else system


class ClaudeCodeExecutor(Executor):
    kind = "claude_code"

    def tools(self, grant: Grant) -> list[str]:
        """--allowedTools: Edit/Write need `write`, Bash needs `shell` (SEC-011), MCP tools autonomy G3; a run
        without writes gets at most the check list."""
        return (["Read", "Grep", "Glob"] + (["Edit", "Write"] if grant.writes else [])
                + (list(CLAUDE_BASH) if grant.shell else list(CLAUDE_CHECK) if grant.checks else []))

    def _run(self, req: Request) -> Result:
        if req.workdir is None:
            raise RuntimeError("Claude Code needs a worktree")
        cwd = ensure_sandboxed(req.workdir)
        grant = req.grant or Grant()
        tools = self.tools(grant)
        cmd = ["claude", "-p", req.prompt, "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", with_check_hint(req.system, grant), "--allowedTools", ",".join(tools)]
        if grant.checks and not grant.shell:
            cmd += ["--disallowedTools", ",".join(CLAUDE_CHECK_DENY)]
        if req.writes:
            cmd += ["--permission-mode", "acceptEdits", "--settings", json.dumps(EDIT_CHECK_SETTINGS)]
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
        servers, mcp_tools = (req.data.get("mcp") or ({}, [])) if grant.mcp else ({}, [])
        if servers.get("mcpServers"):  # the agent's granted servers from Tools & MCP, allowed tools only
            import tempfile

            fd, mcp_file = tempfile.mkstemp(prefix="ac-mcp-", suffix=".json")
            with open(fd, "w") as f:
                json.dump(servers, f)
            cmd += ["--mcp-config", mcp_file, "--strict-mcp-config"]
            cmd[cmd.index("--allowedTools") + 1] += "," + ",".join(mcp_tools)
            tools = [*tools, *mcp_tools]
        watch = stuck_watch("claude")
        try:
            proc = self._spawn(cmd, req, cwd, env=claude_env(self.choice), watch=watch)
        finally:
            if mcp_file:
                Path(mcp_file).unlink(missing_ok=True)
        data, calls = parse_claude_stream(proc.stdout)
        served = claude_served(proc.stdout)
        if watch.reason:  # FW-R-02: ended because it repeated itself; the retry is told why
            return Result(text=str((data or {}).get("result") or ""), ok=False, error=watch.reason, tools=tools,
                          tool_calls=calls, session_id=str((data or {}).get("session_id") or ""), served_model=served)
        if data is None:  # no result event: the CLI failed before it finished
            return Result(text=proc.stdout[-20_000:], ok=False, error=(proc.stderr or proc.stdout)[-2000:],
                          tools=tools, tool_calls=calls, served_model=served)
        u = data.get("usage") or {}
        inp = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(
            u.get("cache_creation_input_tokens") or 0)
        return Result(text=str(data.get("result") or ""), input_tokens=inp,
                      output_tokens=int(u.get("output_tokens") or 0),
                      cost=float(data.get("total_cost_usd") or 0.0),
                      ok=not data.get("is_error") and proc.returncode == 0,
                      error="" if proc.returncode == 0 else (proc.stderr or str(data.get("result")))[-2000:],
                      session_id=str(data.get("session_id") or ""), tools=tools, tool_calls=calls, served_model=served,
                      denied_tools=sorted({str(d.get("tool_name") or "?") for d in data.get("permission_denials") or []
                                           if isinstance(d, dict)}))


# Codex's own ways to the web, outside its sandbox's network block: web search, the ChatGPT apps server
# (`codex_apps` search_service.web_run), a browser and computer use. Agents get no web tools (`tools_for`); the research
# room's web goes through the platform's tools (research_agent.py). Verification phase 3, V12.
CODEX_NO_WEB = ["-c", 'web_search="disabled"', "--disable", "apps", "--disable", "browser_use",
                "--disable", "computer_use"]


class CodexExecutor(Executor):
    kind = "codex"

    def tools(self, grant: Grant) -> list[str]:
        return ["sandbox:workspace-write" if grant.writes else "sandbox:read-only"]

    def _run(self, req: Request) -> Result:
        if req.workdir is None:
            raise RuntimeError("Codex needs a worktree")
        cwd = ensure_sandboxed(req.workdir)
        prompt = req.system + "\n\n" + req.prompt
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", str(cwd),
               "--sandbox", "workspace-write" if req.writes else "read-only", *CODEX_NO_WEB]
        if self.choice.model_id:
            cmd += ["--model", self.choice.model_id]
        watch = stuck_watch("codex")
        proc = self._spawn([*cmd, prompt], req, cwd, env=codex_env(self.choice), watch=watch)
        parsed = parse_codex_stream(proc.stdout)
        if watch.reason:  # FW-R-02
            return Result(text="", input_tokens=estimate_tokens(prompt), ok=False, error=watch.reason,
                          tool_calls=(parsed or {}).get("tool_calls") or [])
        if parsed is None:  # a Codex without --json events: its plain final message
            text = proc.stdout.strip()
            return Result(text=text, input_tokens=estimate_tokens(prompt), output_tokens=estimate_tokens(text),
                          ok=proc.returncode == 0, error=proc.stderr[-2000:] if proc.returncode else "")
        text, u = parsed["text"].strip(), parsed["usage"]
        ok = proc.returncode == 0 and not parsed["error"]
        return Result(text=text, input_tokens=int(u.get("input_tokens") or 0) or estimate_tokens(prompt),
                      output_tokens=int(u.get("output_tokens") or 0) or estimate_tokens(text), ok=ok,
                      error="" if ok else (parsed["error"] or proc.stderr[-2000:] or "codex failed")[-2000:],
                      tool_calls=parsed["tool_calls"])


# Copilot may run tests, the syntax check and inspect git, nothing else, through its shell tool (as CLAUDE_BASH).
COPILOT_SHELL = [*(f"shell({p})" for p in PYTEST_FORMS), "shell(uv run)", "shell(npm test)", "shell(npm run)",
                 "shell(git status)", "shell(git diff)", "shell(ls)"]
COPILOT_CHECK = [*(f"shell({p})" for p in PYTEST_FORMS), "shell(uv run pytest)", "shell(git status)",
                 "shell(git diff)", "shell(ls)"]
if CHECK_OK:
    COPILOT_SHELL.append(f"shell({CHECK_CMD})")
    COPILOT_CHECK.insert(0, f"shell({CHECK_CMD})")
COPILOT_CHECK_DENY = ["shell(git diff --output)"]  # as CLAUDE_CHECK_DENY


class CopilotExecutor(Executor):
    """`copilot -p` (GitHub Copilot CLI) in the worktree, on the server's GitHub sign-in. In this mode a tool that is
    not allowed is refused, so the grant decides: `write` for edits, COPILOT_SHELL for commands."""
    kind = "copilot"

    def tools(self, grant: Grant) -> list[str]:
        return (["write"] if grant.writes else []) + (
            list(COPILOT_SHELL) if grant.shell else list(COPILOT_CHECK) if grant.checks else [])

    def _run(self, req: Request) -> Result:
        if req.workdir is None:
            raise RuntimeError("Copilot needs a worktree")
        cwd = ensure_sandboxed(req.workdir)
        grant = req.grant or Grant()
        tools = self.tools(grant)
        prompt = with_check_hint(req.system, grant) + "\n\n" + req.prompt
        cmd = ["copilot", "-p", prompt]
        for t in tools:
            cmd += ["--allow-tool", t]
        cmd += ["--deny-tool", "shell(git push)"]
        for t in COPILOT_CHECK_DENY if grant.checks and not grant.shell else []:
            cmd += ["--deny-tool", t]
        if self.choice.model_id:
            cmd += ["--model", self.choice.model_id]
        proc = self._spawn(cmd, req, cwd)
        text = proc.stdout.strip()
        return Result(text=text, input_tokens=estimate_tokens(prompt), output_tokens=estimate_tokens(text),
                      ok=proc.returncode == 0, error=(proc.stderr or proc.stdout)[-2000:] if proc.returncode else "",
                      tools=tools)


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
        from agent_company.strategies import fallback_plan

        return json.dumps(fallback_plan(req.data.get("goal", "the goal"), req.data.get("kind", "")), indent=2)

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

    def _exercises(self, req: Request) -> str:
        """Two small exercises whose checks fail on the seed and pass with the solution (exercises.py)."""
        final = req.data.get("kind") == "final"
        mod = (("vowels", "count_vowels", "count_vowels('Banana') == 3 and count_vowels('') == 0"),
                          ("totals", "running_total", "running_total([1, 2, 3]) == [1, 3, 6]")) if final else \
            (("text_util", "reverse_words", "reverse_words('a b c') == 'c b a'"),
             ("pal", "is_palindrome", "is_palindrome('Level') and not is_palindrome('ab')"))
        bodies = {"count_vowels": "def count_vowels(s):\n    return sum(c in 'aeiou' for c in s.lower())\n",
                  "running_total": "def running_total(xs):\n    out, t = [], 0\n    for x in xs:\n        t += x\n"
                                   "        out.append(t)\n    return out\n",
                  "reverse_words": "def reverse_words(s):\n    return ' '.join(reversed(s.split()))\n",
                  "is_palindrome": "def is_palindrome(s):\n    s = s.lower()\n    return s == s[::-1]\n"}
        tasks = [{"name": f.replace("_", "-"), "title": f"Add {f}() to {m}.py",
                  "description": f"Implement {f} in {m}.py so that {c}.",
                  "files": {f"{m}.py": f'"""{m} helpers."""\n'}, "solution": {f"{m}.py": bodies[f]},
                  "checks": [f'python3 -c "from {m} import {f}; assert {c}"']} for m, f, c in mod]
        return "```json\n" + json.dumps({"tasks": tasks}) + "\n```"

    def _contradiction(self, req: Request) -> str:
        return "VERDICT: UNSURE — the offline simulator does not judge knowledge; a person decides."

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
            lesson = f"For tasks like '{title}', the approach used passed the quality gate; reuse it."
        else:
            lesson = f"'{title}' failed the gate: {req.data.get('feedback', 'see report')[:200]}. Check this first next time."
        actions = (req.data.get("facts") or {}).get("actions", [])
        return "```json\n" + json.dumps({
            "reusable_lesson": lesson,
            "successful_approach": "; ".join(actions[:2]) if outcome == "success" else "",
            "limitations": "Observed on one task of the offline simulator."}) + "\n```"

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

    def _rca(self, req: Request) -> str:
        """Root-cause analysis (rca.py): the most repeated failing check as the confirmed cause, failed runs as
        contributing; one corrective action."""
        refs = [ref for ref, _ in req.data.get("items") or []]
        watch = list(req.data.get("watch") or [])
        lines = [f"CONFIRMED: code: '{c}' kept failing | check:{c}" for c in watch[:1] if f"check:{c}" in refs]
        runs = [r for r in refs if r.startswith("run:")][:2]
        lines.append("CONTRIBUTING: " + ("model runs failed | " + ", ".join(runs) if runs
                                         else "the plan may have been too large for the iterations given"))
        if watch:
            lines += [f"ACTION: Make '{watch[0]}' pass before new work :: it failed in every iteration",
                      f"FOLLOW_UP: '{watch[0]}' passes in the action's goal"]
        return "\n".join(lines)

    def _report(self, req: Request) -> str:
        docs = req.data.get("documents") or []

        def quote(d: dict[str, Any]) -> str:  # the first words of the stored passage, for the claim check
            words = " ".join(str(d.get("text", "")).replace('"', " ").split())[:90]
            return words.rsplit(" ", 1)[0] if len(words) == 90 and " " in words else words

        findings = "\n".join(f"{i}. {d['title'] or d['url']}: {quote(d)} [{i}] \"{quote(d)}\"" if len(quote(d)) >= 8
                             else f"{i}. {d['title'] or d['url']} is relevant [{i}]." for i, d in enumerate(docs, 1))
        sources = "\n".join(f"[{i}] {d['title']} - {d['url']} - {d['category']}" for i, d in enumerate(docs, 1))
        return (f"## Objective\n{req.data.get('question', '')}\n\n## Findings\n{findings or 'No material was found.'}\n\n"
                "## Gaps\nThe offline simulator does not read the material; configure a model for a real report.\n\n"
                f"## Recommendation\nRead the sources below.\n\n## Sources\n{sources or '(none)'}\n")

    def _report_review(self, req: Request) -> str:
        """Agrees with the claims an excerpt backs (research.check_claims), approves when any is left."""
        claims = req.data.get("claims") or []
        lines = [f"C{c['n']}: AGREE" if c.get("support") != "unsupported" else
                 f"C{c['n']}: DISAGREE no excerpt from a stored source backs it" for c in claims]
        ok = any(c.get("support") != "unsupported" for c in claims)
        return "\n".join([*lines, "VERDICT: APPROVE" if ok else "VERDICT: REJECT no claim is backed by its sources"])

    def _research(self, req: Request) -> str:
        docs = req.data.get("documents") or []
        if not docs:
            return "No collected material matched this goal yet."
        lines = [f"- {d['title'] or d['url']}: {d['text'][:160].strip()}" for d in docs[:5]]
        return "Relevant material:\n" + "\n".join(lines)


EXECUTORS: dict[str, type[Executor]] = {
    "llm": LLMExecutor, "claude_code": ClaudeCodeExecutor, "codex": CodexExecutor, "copilot": CopilotExecutor,
    "offline": OfflineExecutor,
}


def for_choice(choice: Choice, timeout: int = 1800) -> Executor:
    return EXECUTORS[choice.kind](choice, timeout)
