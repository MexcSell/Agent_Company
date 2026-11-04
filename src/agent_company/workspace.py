"""Workspace Claude Code panel (administrators): an interactive Claude Code session in a folder.

Each prompt runs `claude -p --output-format stream-json` in the folder the administrator
opened, with every tool allowed (Bash, file edits, web) - the administrator's own assistant,
like Claude Code in an editor. It is not a goal-loop executor: those stay sandboxed
(`executors.ClaudeCodeExecutor`). Follow-up prompts continue the conversation with
`--resume <session_id>`. Attached images are passed by path for Claude's Read tool.

The stream is normalised into events the dashboard renders by kind:
init, thinking, text, tool_use, tool_result, result, error, log.
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
from pathlib import Path
from typing import Any

ALL_TOOLS = ["Bash", "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "LS", "WebFetch", "WebSearch",
             "NotebookEdit", "TodoWrite", "Task"]
MODELS = ["default", "opus", "sonnet", "haiku"]
MAX_RUNS = 40
MAX_FIELD = 20_000
_SESSION_RE = re.compile(r"[0-9a-fA-F-]{8,64}")


def available() -> dict[str, Any]:
    exe = shutil.which("claude")
    return {"available": exe is not None, "path": exe or "", "models": MODELS}


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
                    "cwd": raw.get("cwd", ""), "tools": len(raw.get("tools") or [])})
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
        out.append({"kind": "result", "text": _clip(str(raw.get("result") or "")), "is_error": bool(raw.get("is_error")),
                    "cost": float(raw.get("total_cost_usd") or 0), "duration_ms": int(raw.get("duration_ms") or 0),
                    "turns": int(raw.get("num_turns") or 0), "session_id": raw.get("session_id", "")})
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
