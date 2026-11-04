"""Workspace Claude Code panel (administrators): an interactive Claude Code session in a folder.

Each prompt runs `claude -p --output-format stream-json` in the folder the administrator
opened, with every tool allowed (Bash, file edits, web) - the administrator's own assistant,
like Claude Code in an editor. It is not a goal-loop executor: those stay sandboxed
(`executors.ClaudeCodeExecutor`). Follow-up prompts continue the conversation with
`--resume <session_id>`. Attached images are passed by path for Claude's Read tool.

The stream is normalised into events the dashboard renders by kind:
init, thinking, text, tool_use, tool_result, result, error, log.

The panel also shows what Claude Code is doing on this host - its own answer to `/status`,
`/tasks` and `/agents`: every live Claude Code session (VS Code, terminal, SDK, this panel) is
listed from Claude Code's session registry (`~/.claude/sessions/<pid>.json`, busy/idle), and
its transcript (`~/.claude/projects/<folder>/<session>.jsonl`) is read incrementally for the
model, token use, running subagents and background commands, the todo list and the
conversation itself. Read-only: nothing is sent to those sessions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

ALL_TOOLS = ["Bash", "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "LS", "WebFetch", "WebSearch",
             "NotebookEdit", "TodoWrite", "Task"]
MODELS = ["default", "opus", "sonnet", "haiku"]
MAX_RUNS = 40
MAX_FIELD = 20_000
_SESSION_RE = re.compile(r"[0-9a-fA-F-]{8,64}")
TRANSCRIPT_TAIL = 2_000_000  # bytes read from the end of a transcript the first time it is opened
SESSIONS_TTL = 3.0
MAX_EVENTS = 400  # conversation events kept per watched session
MAX_WATCHED = 64


def available() -> dict[str, Any]:
    exe = shutil.which("claude")
    return {"available": exe is not None, "path": exe or "", "models": MODELS, "version": _version(exe),
            "account": _account()}


_VERSION: dict[str, str] = {}


def _version(exe: str | None) -> str:
    if not exe:
        return ""
    if exe not in _VERSION:
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
        except Exception:  # noqa: BLE001 - the version is informational; never fail the panel over it
            out = ""
        _VERSION[exe] = out.strip().split(" ")[0] if out.strip() else ""
    return _VERSION[exe]


def claude_home() -> Path:
    """Claude Code's own config folder: ~/.claude, or CLAUDE_CONFIG_DIR."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def _account() -> dict[str, str]:
    """How Claude Code signs in on this host (what `/status` shows as the account)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"method": "API key (ANTHROPIC_API_KEY)"}
    home = claude_home()
    files = [home / ".claude.json"] if os.environ.get("CLAUDE_CONFIG_DIR") else [Path.home() / ".claude.json"]
    for f in files:
        try:
            acct = json.loads(f.read_text()).get("oauthAccount") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if acct.get("emailAddress"):
            return {"method": "Claude account", "email": str(acct["emailAddress"]),
                    "organization": str(acct.get("organizationName") or "")}
    return {"method": "not signed in"}


def _clip(value: Any, limit: int = MAX_FIELD) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"\n… ({len(value) - limit} more characters)"
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else {"_truncated": text[:limit]}


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text", "")))
        elif isinstance(c, dict) and c.get("type") == "image":
            parts.append("[image]")
        else:
            parts.append(json.dumps(c, default=str))
    return "\n".join(parts)


def normalise(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one stream-json line into display events."""
    t = raw.get("type")
    parent = raw.get("parent_tool_use_id")
    out: list[dict[str, Any]] = []
    if t == "system" and raw.get("subtype") == "init":
        out.append({"kind": "init", "session_id": raw.get("session_id", ""), "model": raw.get("model", ""),
                    "cwd": raw.get("cwd", ""), "tools": len(raw.get("tools") or []),
                    "permission_mode": raw.get("permissionMode", ""), "version": raw.get("claude_code_version", ""),
                    "api_key_source": raw.get("apiKeySource", ""), "output_style": raw.get("output_style", ""),
                    "mcp_servers": [{"name": str(m.get("name", "")), "status": str(m.get("status", ""))}
                                    for m in raw.get("mcp_servers") or [] if isinstance(m, dict)],
                    "agents": [str(a) for a in raw.get("agents") or []][:100],
                    "slash_commands": [str(c) for c in raw.get("slash_commands") or []][:200]})
    elif t == "assistant":
        for block in (raw.get("message") or {}).get("content") or []:
            bt = block.get("type")
            if bt == "text" and block.get("text"):
                out.append({"kind": "text", "text": _clip(block["text"])})
            elif bt == "thinking" and block.get("thinking"):
                out.append({"kind": "thinking", "text": _clip(block["thinking"])})
            elif bt == "tool_use":
                out.append({"kind": "tool_use", "id": block.get("id", ""), "name": block.get("name", ""),
                            "input": _clip(block.get("input") or {})})
    elif t == "user":
        content = (raw.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                out.append({"kind": "tool_result", "tool_use_id": block.get("tool_use_id", ""),
                            "is_error": bool(block.get("is_error")),
                            "text": _clip(_tool_result_text(block.get("content")), 8000)})
    elif t == "result":
        usage = raw.get("usage") or {}
        out.append({"kind": "result", "text": _clip(str(raw.get("result") or "")), "is_error": bool(raw.get("is_error")),
                    "cost": float(raw.get("total_cost_usd") or 0), "duration_ms": int(raw.get("duration_ms") or 0),
                    "turns": int(raw.get("num_turns") or 0), "session_id": raw.get("session_id", ""),
                    "tokens_in": sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                                      "cache_creation_input_tokens")),
                    "tokens_out": int(usage.get("output_tokens") or 0)})
    for e in out:
        if parent:
            e["parent"] = parent
    return out


class Run:
    def __init__(self, prompt: str, cwd: str, owner: str, session_id: str, model: str, images: list[dict[str, Any]]):
        self.id = uuid.uuid4().hex[:12]
        self.prompt, self.cwd, self.owner, self.model = prompt, cwd, owner, model
        self.session_id = session_id
        self.images = images
        self.status = "running"
        self.events: list[dict[str, Any]] = []
        self.started, self.ended = time.time(), 0.0
        self.proc: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()

    def add(self, *events: dict[str, Any]) -> None:
        with self.lock:
            for e in events:
                self.events.append({"n": len(self.events), "at": time.time(), **e})

    def to_dict(self, after: int = 0) -> dict[str, Any]:
        with self.lock:
            return {"id": self.id, "prompt": self.prompt, "cwd": self.cwd, "status": self.status,
                    "session_id": self.session_id, "model": self.model, "started": self.started,
                    "ended": self.ended, "images": [{"id": i["id"], "name": i["name"]} for i in self.images],
                    "events": self.events[after:], "next": len(self.events)}


RUNS: OrderedDict[str, Run] = OrderedDict()
_runs_lock = threading.Lock()


def _command(run: Run) -> list[str]:
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--allowedTools", ",".join(ALL_TOOLS)]
    for folder in sorted({str(Path(i["path"]).parent) for i in run.images}):
        cmd += ["--add-dir", folder]
    if run.session_id:
        cmd += ["--resume", run.session_id]
    if run.model and run.model != "default":
        cmd += ["--model", run.model]
    return cmd


def _prompt_text(run: Run) -> str:
    if not run.images:
        return run.prompt
    files = "\n".join(f"- {i['path']} ({i['name']})" for i in run.images)
    return f"{run.prompt}\n\nAttached files (open them with the Read tool):\n{files}"


def start(prompt: str, cwd: str, owner: str, session_id: str = "", model: str = "",
          images: list[dict[str, Any]] | None = None) -> Run:
    if not prompt.strip():
        raise ValueError("write a prompt first")
    if not available()["available"]:
        raise RuntimeError("the `claude` command is not installed on the server (npm i -g @anthropic-ai/claude-code)")
    folder = Path(cwd or Path.home()).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    if session_id and not _SESSION_RE.fullmatch(session_id):
        raise ValueError("bad session id")
    if model and model not in MODELS and not re.fullmatch(r"[\w.\-\[\]]{2,80}", model):
        raise ValueError("bad model name")
    run = Run(prompt, str(folder), owner, session_id, model, images or [])
    with _runs_lock:
        RUNS[run.id] = run
        while len(RUNS) > MAX_RUNS:
            old = RUNS.popitem(last=False)[1]
            if old.status == "running":
                stop(old.id)
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}}
    try:
        run.proc = subprocess.Popen(_command(run), cwd=folder, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
    except OSError as e:
        run.status, run.ended = "failed", time.time()
        run.add({"kind": "error", "text": f"could not start Claude Code: {e}"})
        return run
    threading.Thread(target=_pump, args=(run, _prompt_text(run)), daemon=True, name=f"claude-{run.id}").start()
    return run


def _pump(run: Run, text: str) -> None:
    proc = run.proc
    assert proc is not None and proc.stdin is not None and proc.stdout is not None
    stderr: list[str] = []
    err_thread = threading.Thread(target=lambda: stderr.extend(proc.stderr or []), daemon=True)
    err_thread.start()
    try:
        proc.stdin.write(text)
        proc.stdin.close()
    except OSError:
        pass
    got_result = False
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            run.add({"kind": "log", "text": _clip(line, 4000)})
            continue
        events = normalise(raw)
        for e in events:
            if e["kind"] in {"init", "result"} and e.get("session_id"):
                run.session_id = e["session_id"]
            got_result = got_result or e["kind"] == "result"
        run.add(*events)
    code = proc.wait()
    err_thread.join(timeout=2)
    if run.status == "stopped":
        run.add({"kind": "error", "text": "stopped"})
    elif code != 0 and not got_result:
        run.add({"kind": "error", "text": ("".join(stderr).strip() or f"Claude Code exited with code {code}")[-4000:]})
    if run.status == "running":
        run.status = "done" if code == 0 else "failed"
    run.ended = time.time()


def get(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if run is None:
        raise KeyError(f"run {run_id}")
    return run


def stop(run_id: str) -> Run:
    run = get(run_id)
    if run.status == "running" and run.proc is not None:
        run.status = "stopped"
        try:
            os.killpg(run.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    return run


def stop_all() -> None:
    for run_id, run in list(RUNS.items()):
        if run.status == "running":
            stop(run_id)


# --- Claude Code sessions on this host (/status, /tasks, /agents) ----------------------------

_NOTICE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
_TAG_RE = re.compile(r"<(task-id|tool-use-id|status|summary)>(.*?)</\1>", re.S)
_sessions_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_watch: OrderedDict[str, _Transcript] = OrderedDict()
_watch_lock = threading.Lock()


def _alive(pid: int, proc_start: str) -> bool:
    """The registry keeps files of sessions that crashed: the pid must exist and be the same process."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        if Path("/proc/self/stat").exists():
            return False
        try:  # no /proc (macOS): only the pid can be checked
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False
    except OSError:
        return False
    fields = stat.rsplit(")", 1)[-1].split()
    return not proc_start or (len(fields) > 19 and fields[19] == str(proc_start))


def sessions(refresh: bool = False) -> list[dict[str, Any]]:
    """Every live Claude Code session on this host, busy ones first (from ~/.claude/sessions)."""
    global _sessions_cache
    at, cached = _sessions_cache
    if not refresh and time.time() - at < SESSIONS_TTL:
        return cached
    found: dict[str, dict[str, Any]] = {}
    for f in (claude_home() / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            pid = int(d.get("pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        sid = str(d.get("sessionId") or "")
        if not pid or not _SESSION_RE.fullmatch(sid) or not _alive(pid, str(d.get("procStart") or "")):
            continue
        s = {"pid": pid, "session_id": sid, "cwd": str(d.get("cwd") or ""), "name": str(d.get("name") or sid[:8]),
             "status": str(d.get("status") or "idle"), "entrypoint": str(d.get("entrypoint") or ""),
             "kind": str(d.get("kind") or ""), "version": str(d.get("version") or ""),
             "started": (d.get("startedAt") or 0) / 1000, "updated": (d.get("updatedAt") or 0) / 1000}
        if sid not in found or s["updated"] > found[sid]["updated"]:  # resumed in a newer process
            found[sid] = s
    out = sorted(found.values(), key=lambda s: (s["status"] != "busy", -s["updated"]))
    _sessions_cache = (time.time(), out)
    return out


def _ts(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class _Transcript:
    """Incremental reader of one session transcript (~/.claude/projects/<folder>/<session>.jsonl)."""

    def __init__(self, path: Path):
        self.path, self.offset, self.n = path, 0, 0
        self.events: list[dict[str, Any]] = []
        self.model = self.permission_mode = self.git_branch = self.version = self.cwd = self.entrypoint = ""
        self.last_prompt, self.last_at = "", 0.0
        self.context_tokens = self.output_tokens = 0
        self.message_id = ""  # one line per content block, each repeating the message's usage
        self.pending: OrderedDict[str, dict[str, Any]] = OrderedDict()  # tool calls without a result yet
        self.agents: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.tasks: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.todos: list[dict[str, Any]] = []
        self.task_list: OrderedDict[str, dict[str, Any]] = OrderedDict()  # TaskCreate / TaskUpdate
        self.agent_types: list[str] = []
        self.partial_history = False

    def refresh(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.offset:  # rewritten: start again
            self.__init__(self.path)  # type: ignore[misc]
        if size == self.offset:
            return
        with self.path.open("rb") as f:
            start = self.offset
            if start == 0 and size > TRANSCRIPT_TAIL:
                start, self.partial_history = size - TRANSCRIPT_TAIL, True
            f.seek(start)
            data = f.read(size - start)
        if start and start != self.offset:
            data = data[data.find(b"\n") + 1:]  # drop the cut line
            start = size - len(data)
        end = data.rfind(b"\n") + 1  # a line still being written waits for the next poll
        self.offset = start + end
        for line in data[:end].splitlines():
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if isinstance(raw, dict):
                self._line(raw)

    def _add(self, e: dict[str, Any], at: float) -> None:
        self.n += 1
        self.events.append({"n": self.n, "at": at, **e})
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]

    def _line(self, raw: dict[str, Any]) -> None:
        t, at = raw.get("type"), _ts(raw.get("timestamp"))
        if raw.get("isSidechain"):
            return
        if t in {"user", "assistant"}:
            self.last_at = max(self.last_at, at)
            for key, attr in (("permissionMode", "permission_mode"), ("gitBranch", "git_branch"), ("version", "version"),
                              ("cwd", "cwd"), ("entrypoint", "entrypoint")):
                if raw.get(key):
                    setattr(self, attr, str(raw[key]))
        if t == "assistant":
            msg = raw.get("message") or {}
            if msg.get("model") and not str(msg["model"]).startswith("<"):
                self.model = str(msg["model"])
            usage = msg.get("usage") or {}
            if usage and msg.get("id") != self.message_id:
                self.message_id = str(msg.get("id") or "")
                self.context_tokens = sum(int(usage.get(k) or 0) for k in
                                          ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                self.output_tokens += int(usage.get("output_tokens") or 0)
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self._tool_use(block, at)
        elif t == "user":
            self._user(raw, at)
        elif t == "system" and raw.get("subtype") == "compact_boundary":
            self._add({"kind": "log", "text": "Conversation compacted"}, at)
        elif t == "attachment":
            a = raw.get("attachment") or {}
            if a.get("type") == "agent_listing_delta":
                gone = set(a.get("removedTypes") or [])
                self.agent_types = [x for x in self.agent_types if x not in gone]
                self.agent_types += [str(x) for x in a.get("addedTypes") or [] if str(x) not in self.agent_types]
            elif a.get("type") == "model" and a.get("model"):
                self.model = self.model or str(a["model"])
        if t in {"assistant", "user"}:
            for e in normalise({"type": t, "message": raw.get("message")}):
                self._add(e, at)

    def _tool_use(self, block: dict[str, Any], at: float) -> None:
        name, inp, tid = str(block.get("name") or ""), block.get("input") or {}, str(block.get("id") or "")
        short = {k: _clip(v, 1000) for k, v in inp.items()} if isinstance(inp, dict) else {}  # keeps `command` readable
        self.pending[tid] = {"id": tid, "name": name, "input": short, "since": at}
        while len(self.pending) > 50:
            self.pending.popitem(last=False)
        if name in {"Agent", "Task"} and isinstance(inp, dict):
            self.agents[tid] = {"id": tid, "type": str(inp.get("subagent_type") or "general-purpose"),
                                "description": str(inp.get("description") or ""), "status": "running",
                                "background": bool(inp.get("run_in_background")), "model": str(inp.get("model") or ""),
                                "started": at, "ended": 0.0, "tokens": 0, "tools": 0, "agent_id": ""}
        elif name == "TodoWrite" and isinstance(inp, dict):
            self.todos = [{"content": str(x.get("content") or ""), "status": str(x.get("status") or "pending")}
                          for x in inp.get("todos") or [] if isinstance(x, dict)]
        elif name == "TaskUpdate" and isinstance(inp, dict):
            item = self.task_list.get(str(inp.get("taskId") or ""))
            if item is not None:
                for k in ("status", "subject"):
                    if inp.get(k):
                        item[k] = str(inp[k])
                if item["status"] == "deleted":
                    self.task_list.pop(item["id"], None)

    def _user(self, raw: dict[str, Any], at: float) -> None:
        content = (raw.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        texts = [str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        for text in texts:
            for notice in _NOTICE_RE.findall(text):
                self._notice(dict(_TAG_RE.findall(notice)), at)
        prompt = "\n".join(x for x in texts if x.strip() and not x.lstrip().startswith("<")).strip()
        has_results = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
        if prompt and not raw.get("isMeta") and not has_results:
            self.last_prompt = prompt[:2000]
            self._add({"kind": "prompt", "text": _clip(prompt, 8000)}, at)
        result = raw.get("toolUseResult")
        for b in blocks:
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            tid = str(b.get("tool_use_id") or "")
            call = self.pending.pop(tid, None) or {}
            self._result(tid, call, result if isinstance(result, dict) else {}, bool(b.get("is_error")),
                         _tool_result_text(b.get("content")), at)

    def _result(self, tid: str, call: dict[str, Any], res: dict[str, Any], is_error: bool, text: str, at: float) -> None:
        inp = call.get("input") if isinstance(call.get("input"), dict) else {}
        agent = self.agents.get(tid)
        if agent is not None:
            agent.update(agent_id=str(res.get("agentId") or ""), model=str(res.get("resolvedModel") or agent["model"]),
                         tokens=int(res.get("totalTokens") or 0), tools=int(res.get("totalToolUseCount") or 0))
            status = str(res.get("status") or "")
            if is_error:
                agent.update(status="failed", ended=at)
            elif status in {"completed", ""} and not agent["background"]:
                agent.update(status="completed", ended=at)
            elif status not in {"async_launched", "running", "launched"} and status:
                agent.update(status=status, ended=at if status in {"completed", "failed", "killed"} else 0.0)
        if res.get("backgroundTaskId"):
            task = str(res["backgroundTaskId"])
            self.tasks[task] = {"id": task, "type": "command", "command": str(inp.get("command") or "")[:500],
                                "description": str(inp.get("description") or ""), "status": "running",
                                "started": at, "ended": 0.0, "summary": ""}
        name = call.get("name")
        if name == "TaskStop" and not is_error:
            target = str(inp.get("task_id") or inp.get("shell_id") or res.get("task_id") or "")
            self._notice({"task-id": target, "status": "stopped"}, at)
        elif name == "TaskCreate" and not is_error:
            task = res.get("task") if isinstance(res.get("task"), dict) else {}
            m = re.search(r"#(\w+)", text)
            item_id = str(task.get("id") or (m.group(1) if m else len(self.task_list) + 1))
            self.task_list[item_id] = {"id": item_id, "subject": str(inp.get("subject") or task.get("subject") or ""),
                                       "status": "pending"}

    def _notice(self, tags: dict[str, str], at: float) -> None:
        task_id, status = tags.get("task-id", "").strip(), tags.get("status", "").strip() or "completed"
        summary = tags.get("summary", "").strip()
        if task_id in self.tasks:
            self.tasks[task_id].update(status=status, ended=at, summary=summary[:500])
        for agent in self.agents.values():
            if task_id and task_id in {agent["agent_id"], agent["id"]} or tags.get("tool-use-id", "").strip() == agent["id"]:
                agent.update(status=status, ended=at)
        if task_id or summary:
            self._add({"kind": "notice", "task_id": task_id, "status": status, "text": summary or status}, at)


def _transcript_path(session_id: str) -> Path | None:
    hits = sorted((claude_home() / "projects").glob(f"*/{session_id}.jsonl"), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def _watched(session_id: str) -> _Transcript | None:
    with _watch_lock:
        tr = _watch.get(session_id)
        if tr is None or not tr.path.exists():
            path = _transcript_path(session_id)
            if path is None:
                return None
            tr = _watch[session_id] = _Transcript(path)
            while len(_watch) > MAX_WATCHED:
                _watch.popitem(last=False)
        _watch.move_to_end(session_id)
        tr.refresh()
        return tr


def _agent_files(folder: Path) -> list[dict[str, str]]:
    """Agents defined as markdown with front matter in <folder>/agents/*.md."""
    out = []
    for f in sorted(folder.glob("agents/*.md"))[:100]:
        try:
            head = f.read_text(errors="replace")[:4000]
        except OSError:
            continue
        meta = dict(re.findall(r"^(name|description|model):\s*(.+)$", head.split("\n---", 1)[0], re.M))
        out.append({"name": meta.get("name", f.stem).strip(), "description": meta.get("description", "").strip()[:300],
                    "model": meta.get("model", "").strip()})
    return out


def session_status(session_id: str, after: int = 0) -> dict[str, Any]:
    """What `/status`, `/tasks` and `/agents` would show for one session, plus its new conversation events."""
    if not _SESSION_RE.fullmatch(session_id or ""):
        raise ValueError("bad session id")
    live = next((s for s in sessions() if s["session_id"] == session_id), None)
    tr = _watched(session_id)
    if tr is None and live is None:
        raise KeyError(f"session {session_id}")
    with _watch_lock:  # one consistent snapshot: `next` must match the events returned
        snap = {
            "cwd": tr.cwd, "model": tr.model, "permission_mode": tr.permission_mode, "git_branch": tr.git_branch,
            "version": tr.version, "entrypoint": tr.entrypoint, "context_tokens": tr.context_tokens,
            "output_tokens": tr.output_tokens, "last_prompt": tr.last_prompt, "last_activity": tr.last_at,
            "partial_history": tr.partial_history, "todos": [dict(t) for t in tr.todos],
            "task_list": [dict(t) for t in tr.task_list.values()], "agent_types": list(tr.agent_types),
            "events": [e for e in tr.events if e["n"] > after], "next": tr.n,
        } if tr else {"cwd": "", "model": "", "permission_mode": "", "git_branch": "", "version": "", "entrypoint": "",
                      "context_tokens": 0, "output_tokens": 0, "last_prompt": "", "last_activity": 0.0,
                      "partial_history": False, "todos": [], "task_list": [], "agent_types": [], "events": [], "next": 0}
        agents = [dict(a) for a in tr.agents.values()] if tr else []
        tasks = [dict(t) for t in tr.tasks.values()] if tr else []
        pending = list(tr.pending.values()) if tr else []
    cwd = snap["cwd"] or (live or {}).get("cwd", "")
    # Subagents keep their own transcript and a meta file that says when they finished.
    if tr is not None:
        metas: dict[str, dict[str, Any]] = {}
        for f in (tr.path.parent / session_id / "subagents").glob("agent-*.meta.json"):
            try:
                m = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(m, dict) and m.get("toolUseId"):
                metas[str(m["toolUseId"])] = m
        for a in agents:
            m = metas.get(a["id"])
            if m and m.get("finished") and a["status"] == "running":
                a["status"] = "completed"
    if live is None:  # the process is gone: what was still running did not report back
        for item in agents + tasks:
            if item["status"] == "running":
                item["status"] = "ended"
    status = (live or {}).get("status") or "ended"
    current = None
    if status == "busy":
        open_calls = [c for c in pending if c["name"] not in {"Agent", "Task"} or c["id"] not in
                      {a["id"] for a in agents if a["background"]}]
        current = open_calls[-1] if open_calls else {"name": "", "input": {}, "since": snap["last_activity"]}
    configured = _agent_files(claude_home()) + (_agent_files(Path(cwd) / ".claude") if cwd else [])
    return {
        **snap, "session_id": session_id, "live": live is not None, "status": status, "process": live,
        "transcript": str(tr.path) if tr else "", "cwd": cwd,
        "version": snap["version"] or (live or {}).get("version", ""),
        "entrypoint": (live or {}).get("entrypoint") or snap["entrypoint"],
        "current": current, "agents": agents, "tasks": tasks, "configured_agents": configured,
    }
