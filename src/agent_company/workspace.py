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
conversation itself. Nothing is sent to those sessions; the only way the panel acts on one is
`stop_session` (SIGTERM to its process, after the administrator confirms), which is audited.

The history window lists every session that has a transcript, running or not (`history`):
its title (renamed, AI-written or first prompt), folder and last activity. An ended session can
be opened read-only, resumed in this panel (`--resume`), or deleted with its transcript.

Each conversation of the panel gets a short title: `-p` sessions get no AI title from Claude
Code, so the first prompt is summarised by a quick Haiku call (`make_title`) and passed as
`--name` on every prompt, which records it in the transcript like a rename.
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
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ALL_TOOLS = ["Bash", "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "LS", "WebFetch", "WebSearch",
             "NotebookEdit", "TodoWrite", "Task"]
MODELS = ["default", "opus", "sonnet", "haiku"]
# Reasoning effort per agent ("default" leaves it to the CLI's own setting): `claude --effort`, Codex's
# `model_reasoning_effort`. Copilot has none.
EFFORTS = ["default", "low", "medium", "high", "xhigh", "max"]
CODEX_EFFORTS = ["default", "low", "medium", "high"]
MAX_RUNS = 40
MAX_FIELD = 20_000
_SESSION_RE = re.compile(r"[0-9a-fA-F-]{8,64}")
TRANSCRIPT_TAIL = 2_000_000  # bytes read from the end of a transcript the first time it is opened
SESSIONS_TTL = 3.0
MAX_EVENTS = 400  # conversation events kept per watched session
MAX_WATCHED = 64
TITLE_MODEL = "haiku"
_helper_pids: set[int] = set()  # title helpers register as sessions too: never listed


CODEX_MODELS = ["default"]
# Agents the panel can talk to. Copilot is listed so the panel says how to get it; it needs its CLI.
PROVIDERS = {"claude": "Claude Code", "codex": "Codex", "copilot": "GitHub Copilot"}


def available() -> dict[str, Any]:
    exe = shutil.which("claude")
    codex, copilot = shutil.which("codex"), shutil.which("copilot")
    catalog = router_models()
    return {"available": exe is not None, "path": exe or "", "models": MODELS, "version": _version(exe),
            "account": _account(),
            "providers": [
                {"id": "claude", "label": "Claude Code", "available": exe is not None, "models": MODELS,
                 "catalog": catalog["claude"], "efforts": EFFORTS, "version": _version(exe),
                 "install": "npm i -g @anthropic-ai/claude-code"},
                {"id": "codex", "label": "Codex", "available": codex is not None, "models": CODEX_MODELS,
                 "catalog": catalog["codex"], "efforts": CODEX_EFFORTS, "version": _version(codex),
                 "install": "npm i -g @openai/codex, then `codex login`"},
                {"id": "copilot", "label": "GitHub Copilot", "available": copilot is not None, "models": ["default"],
                 "efforts": ["default"], "version": _version(copilot), "install": "npm i -g @github/copilot, then `copilot` to sign in"}]}


_ROUTER_KIND = {"claude_code": "claude", "codex": "codex"}


def router_models() -> dict[str, list[dict[str, Any]]]:
    """The Models page's Claude Code and Codex models, offered beside the CLI's own aliases: one with its own sign-in
    (account) or another address (base URL) runs with it. Agents' gates (ENABLE_CLAUDE_CODE) do not apply here."""
    from sqlalchemy import select

    from agent_company import router
    from agent_company.db import ModelEntry, session

    out: dict[str, list[dict[str, Any]]] = {"claude": [], "codex": []}
    with session() as s:
        rows = s.scalars(select(ModelEntry).where(ModelEntry.kind.in_(list(_ROUTER_KIND)), ModelEntry.enabled.is_(True))
                         .order_by(ModelEntry.id)).all()
        for m in rows:
            why = ""
            if m.account and not (router.claude_config_dir(m.account) / ".credentials.json").is_file():
                why = f"no Claude Code sign-in in data/claude-accounts/{m.account}"
            out[_ROUTER_KIND[m.kind]].append({"name": m.name, "model_id": m.model_id, "account": m.account or "",
                                              "remote": bool(m.base_url), "available": not why, "reason": why})
    return out


def _router_choice(name: str, provider: str) -> Any:
    from sqlalchemy import select

    from agent_company import router
    from agent_company.db import ModelEntry, session

    with session() as s:
        m = s.scalars(select(ModelEntry).where(ModelEntry.name == name)).first()
        if m is None or not m.enabled or _ROUTER_KIND.get(m.kind) != provider:
            raise ValueError(f"no {PROVIDERS[provider]} model {name!r} on the Models page")
        return router._choice(m, "chosen in the panel")


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
    elif t == "system" and raw.get("subtype") == "compact_boundary":
        meta = raw.get("compact_metadata") or {}
        out.append({"kind": "compact", "pre_tokens": int(meta.get("pre_tokens") or 0),
                    "post_tokens": int(meta.get("post_tokens") or 0), "trigger": str(meta.get("trigger") or "")})
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
    elif t == "user" and not raw.get("isReplay"):
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


def normalise_codex(raw: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """`codex exec --json` events in the panel's kinds (tool calls become Bash / Edit / tool cards)."""
    t = raw.get("type")
    out: list[dict[str, Any]] = []
    if t == "thread.started":
        state["session_id"] = str(raw.get("thread_id") or "")
        out.append({"kind": "init", "session_id": state["session_id"], "model": "Codex", "cwd": state.get("cwd", ""),
                    "tools": 0})
    elif t in ("item.started", "item.completed"):
        it = raw.get("item") or {}
        kind, iid = it.get("type"), str(it.get("id") or "")
        done = t == "item.completed"
        if kind == "agent_message" and done and it.get("text"):
            out.append({"kind": "text", "text": _clip(str(it["text"]))})
            state["last_text"] = str(it["text"])
        elif kind == "reasoning" and done and it.get("text"):
            out.append({"kind": "thinking", "text": _clip(str(it["text"]))})
        elif kind == "command_execution":
            started = state.setdefault("started", set())
            if iid not in started:
                started.add(iid)
                out.append({"kind": "tool_use", "id": iid, "name": "Bash", "input": {"command": str(it.get("command", ""))}})
            if done:
                out.append({"kind": "tool_result", "tool_use_id": iid, "is_error": (it.get("exit_code") or 0) != 0,
                            "text": _clip(str(it.get("aggregated_output") or ""), 8000)})
        elif kind == "file_change" and done:
            for ch in it.get("changes") or []:
                out.append({"kind": "tool_use", "id": f"{iid}:{ch.get('path')}", "name": "Edit" if ch.get("kind") == "update" else "Write",
                            "input": {"file_path": str(ch.get("path", "")), "change": str(ch.get("kind", ""))}})
                out.append({"kind": "tool_result", "tool_use_id": f"{iid}:{ch.get('path')}", "is_error": False,
                            "text": str(ch.get("kind", "changed"))})
        elif kind in ("mcp_tool_call", "web_search") and done:
            name = "WebSearch" if kind == "web_search" else f"{it.get('server', 'mcp')}.{it.get('tool', '')}"
            out.append({"kind": "tool_use", "id": iid, "name": name, "input": {"query": it.get("query")} if kind == "web_search" else it.get("arguments") or {}})
            out.append({"kind": "tool_result", "tool_use_id": iid, "is_error": bool(it.get("error")), "text": _clip(str(it.get("result") or it.get("error") or ""), 8000)})
        elif kind == "todo_list" and done:
            out.append({"kind": "tool_use", "id": iid, "name": "TodoWrite", "input": {"todos": [
                {"content": str(x.get("text", "")), "status": "completed" if x.get("completed") else "pending"} for x in it.get("items") or []]}})
        elif kind == "error":
            out.append({"kind": "error", "text": str(it.get("message") or "error")})
    elif t == "turn.completed":
        u = raw.get("usage") or {}
        out.append({"kind": "result", "text": state.get("last_text", ""), "is_error": False, "cost": 0.0,
                    "duration_ms": int((time.time() - state.get("t0", time.time())) * 1000), "turns": 1,
                    "session_id": state.get("session_id", ""), "tokens_in": int(u.get("input_tokens") or 0),
                    "tokens_out": int(u.get("output_tokens") or 0)})
    elif t in ("turn.failed", "error"):
        out.append({"kind": "error", "text": str((raw.get("error") or {}).get("message") or raw.get("message") or "Codex failed")})
    return out


class Run:
    def __init__(self, prompt: str, cwd: str, owner: str, session_id: str, model: str, images: list[dict[str, Any]],
                 title: str = "", provider: str = "claude", system: str = "", effort: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.provider, self.system, self.effort = provider, system, effort
        self.prompt, self.cwd, self.owner, self.model, self.title = prompt, cwd, owner, model, title
        self.resume = bool(session_id)
        self.session_id = session_id or str(uuid.uuid4())  # known before Claude Code starts
        self.images = images
        self.status = "running"
        self.events: list[dict[str, Any]] = []
        self.started, self.ended = time.time(), 0.0
        self.proc: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()
        # A Models-page model: its name, the CLI's environment (own sign-in, base URL) and its Claude config folder.
        self.router_model = ""
        self.env: dict[str, str] | None = None
        self.home: Path | None = None

    def add(self, *events: dict[str, Any]) -> None:
        with self.lock:
            for e in events:
                self.events.append({"n": len(self.events), "at": time.time(), **e})

    def to_dict(self, after: int = 0) -> dict[str, Any]:
        with self.lock:
            return {"id": self.id, "prompt": self.prompt, "cwd": self.cwd, "status": self.status, "provider": self.provider,
                    "session_id": self.session_id, "title": self.title, "model": self.model, "router_model": self.router_model,
                    "effort": self.effort, "started": self.started,
                    "ended": self.ended, "images": [{"id": i["id"], "name": i["name"]} for i in self.images],
                    "events": self.events[after:], "next": len(self.events)}


RUNS: OrderedDict[str, Run] = OrderedDict()
_runs_lock = threading.Lock()


def _command(run: Run) -> list[str]:
    if run.provider == "codex":  # the administrator's own Codex: writes in the folder, like Codex in an editor
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-s", "workspace-write"]
        for folder in sorted({str(Path(i["path"]).parent) for i in run.images}):
            cmd += ["--add-dir", folder]
        if run.model and run.model != "default":
            cmd += ["-m", run.model]
        if run.effort:
            cmd += ["-c", f"model_reasoning_effort={run.effort}"]
        return cmd + (["resume", run.session_id, "-"] if run.resume else ["-"])
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--allowedTools", ",".join(ALL_TOOLS)]
    for folder in sorted({str(Path(i["path"]).parent) for i in run.images}):
        cmd += ["--add-dir", folder]
    cmd += ["--resume" if run.resume else "--session-id", run.session_id]
    if run.title:
        cmd += ["--name", run.title]
    if run.model and run.model != "default":
        cmd += ["--model", run.model]
    if run.effort:
        cmd += ["--effort", run.effort]
    if run.system:
        cmd += ["--append-system-prompt", run.system]
    return cmd


def _prompt_text(run: Run) -> str:
    prompt = run.prompt
    if run.provider == "codex" and run.system and not run.resume:  # Codex has no system-prompt flag: first prompt carries it
        prompt = f"{run.system}\n\n---\n\n{prompt}"
    if not run.images:
        return prompt
    files = "\n".join(f"- {i['path']} ({i['name']})" for i in run.images)
    return f"{prompt}\n\nAttached files (open them with the Read tool):\n{files}"


def _env(base: dict[str, str] | None = None) -> dict[str, str]:
    return {k: v for k, v in (base or os.environ).items() if k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}}


def _homes() -> list[Path]:
    """Where Claude Code keeps transcripts: this host's sign-in, then the Models page's own sign-ins."""
    from agent_company.config import settings

    acc = settings().data_dir / "claude-accounts"
    return [claude_home()] + (sorted(p for p in acc.iterdir() if p.is_dir()) if acc.is_dir() else [])


def _carry_transcript(session_id: str, home: Path) -> bool:
    """A conversation that moves to another sign-in takes its transcript along: the newest copy of it goes into
    `home`, where `--resume` looks. False when it has none yet."""
    hits = [p for h in _homes() for p in (h / "projects").glob(f"*/{session_id}.jsonl")]
    if not hits:
        return False
    src = max(hits, key=lambda p: p.stat().st_mtime)
    dst = home / "projects" / src.parent.name / src.name
    if src != dst:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return True


def _fallback_title(prompt: str) -> str:
    line = next((x.strip() for x in prompt.splitlines() if x.strip()), "")
    line = re.sub(r"\s+", " ", line)
    return line if len(line) <= 48 else line[:47].rsplit(" ", 1)[0] + "…"


def make_title(prompt: str) -> str:
    """A short, meaningful name for a conversation from its first prompt (Haiku, else the first line)."""
    exe = shutil.which("claude")
    if exe:
        ask = ("Write a short title (2 to 6 words, no quotes, no trailing period) naming the task in this request "
               "to a coding assistant. Answer with the title only.\n\n<request>\n" + prompt[:4000] + "\n</request>")
        try:
            proc = subprocess.Popen([exe, "-p", "--model", TITLE_MODEL, "--no-session-persistence", "--max-turns", "1",
                                     "--tools", "", "--output-format", "text"], cwd=Path.home(), env=_env(), text=True,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            _helper_pids.add(proc.pid)
            try:
                out, _ = proc.communicate(ask, timeout=45)
            except subprocess.TimeoutExpired:
                proc.kill()
                out = ""
            finally:
                _helper_pids.discard(proc.pid)
            line = next((x.strip() for x in (out or "").splitlines() if x.strip()), "")
            line = line.strip("\"'`*# ").rstrip(".")
            if proc.returncode == 0 and 0 < len(line) <= 80 and not line.lower().startswith(("error", "i ")):
                return line
        except OSError:
            pass
    return _fallback_title(prompt)


def _title_later(run: Run) -> None:
    try:
        title = make_title(run.prompt)
    except Exception:  # noqa: BLE001 - a title is cosmetic; never let it break a run
        title = _fallback_title(run.prompt)
    if not run.title:
        run.title = title


def _run_title(session_id: str) -> str:
    """The title this server gave a conversation whose transcript does not carry it yet (first prompt)."""
    return next((r.title for r in reversed(list(RUNS.values())) if r.session_id == session_id and r.title), "")


def start(prompt: str, cwd: str, owner: str, session_id: str = "", model: str = "",
          images: list[dict[str, Any]] | None = None, title: str = "", provider: str = "claude", system: str = "",
          effort: str = "", router_model: str = "") -> Run:
    if not prompt.strip():
        raise ValueError("write a prompt first")
    if provider not in PROVIDERS:
        raise ValueError(f"unknown agent {provider!r}")
    prov = next(p for p in available()["providers"] if p["id"] == provider)
    if not prov["available"]:
        raise RuntimeError(f"{prov['label']} is not installed on the server ({prov['install']})")
    if provider == "copilot":
        raise RuntimeError("GitHub Copilot's CLI has no streaming JSON mode this panel can read yet")
    folder = Path(cwd or Path.home()).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    if session_id and not _SESSION_RE.fullmatch(session_id):
        raise ValueError("bad session id")
    # A run outlives a service restart (its own process group) while RUNS does not: resuming a
    # conversation that is still working would put two processes on one transcript.
    busy = next((s for s in sessions(refresh=True) if s["session_id"] == session_id), None) if session_id and provider == "claude" else None
    if busy is not None:
        raise ValueError(f"this conversation is still running in another process (pid {busy['pid']}): "
                         "wait for it to finish, or stop it from the sessions window")
    choice = _router_choice(router_model, provider) if router_model else None
    if choice is not None:
        model = choice.model_id
    if model and model not in MODELS and not re.fullmatch(r"[\w.\-\[\]]{2,80}", model):
        raise ValueError("bad model name")
    effort = "" if effort == "default" else effort
    if effort and effort not in (CODEX_EFFORTS if provider == "codex" else EFFORTS):
        raise ValueError(f"unknown effort {effort!r} for {PROVIDERS[provider]}")
    title = re.sub(r"\s+", " ", title or "").strip()[:80]
    run = Run(prompt, str(folder), owner, session_id, model, images or [], title, provider, system[:20000], effort)
    if choice is not None:
        from agent_company.executors import claude_env
        from agent_company.router import claude_config_dir

        run.router_model = choice.name
        if provider == "claude":
            extra = claude_env(choice)
            run.env = _env(extra) if extra else None
            run.home = claude_config_dir(choice.account) if choice.account else None
    # A conversation whose first prompt failed before Claude Code wrote anything starts afresh under its id; one
    # that continues under another sign-in takes its transcript there.
    run.resume = bool(session_id) and (provider == "codex" or _carry_transcript(session_id, run.home or claude_home()))
    if provider == "codex" and not session_id:
        run.session_id = ""  # Codex names its thread; known from its first event
    with _runs_lock:
        RUNS[run.id] = run
        while len(RUNS) > MAX_RUNS:
            old = RUNS.popitem(last=False)[1]
            if old.status == "running":
                stop(old.id)
    try:
        run.proc = subprocess.Popen(_command(run), cwd=folder, env=run.env or _env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
    except OSError as e:
        run.status, run.ended = "failed", time.time()
        run.add({"kind": "error", "text": f"could not start Claude Code: {e}"})
        return run
    threading.Thread(target=_pump, args=(run, _prompt_text(run)), daemon=True, name=f"claude-{run.id}").start()
    if not run.title:  # the panel sends it with every later prompt (`--name`)
        threading.Thread(target=_title_later, args=(run,), daemon=True, name=f"claude-title-{run.id}").start()
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
    state: dict[str, Any] = {"cwd": run.cwd, "t0": time.time()}
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            run.add({"kind": "log", "text": _clip(line, 4000)})
            continue
        events = normalise_codex(raw, state) if run.provider == "codex" else normalise(raw)
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
        name = PROVIDERS.get(run.provider, "Claude Code")
        run.add({"kind": "error", "text": ("".join(stderr).strip() or f"{name} exited with code {code}")[-4000:]})
    if run.status == "running":
        run.status = "done" if code == 0 else "failed"
    if run.home is not None:  # back to this host's sign-in, where the history, transcripts and resume look
        try:
            _carry_transcript(run.session_id, claude_home())
        except OSError:
            pass
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
        if not pid or pid in _helper_pids or not _SESSION_RE.fullmatch(sid) or not _alive(pid, str(d.get("procStart") or "")):
            continue
        s = {"pid": pid, "session_id": sid, "cwd": str(d.get("cwd") or ""), "name": str(d.get("name") or sid[:8]),
             "status": str(d.get("status") or "idle"), "entrypoint": str(d.get("entrypoint") or ""),
             "kind": str(d.get("kind") or ""), "version": str(d.get("version") or ""),
             "started": (d.get("startedAt") or 0) / 1000, "updated": (d.get("updatedAt") or 0) / 1000}
        if sid not in found or s["updated"] > found[sid]["updated"]:  # resumed in a newer process
            found[sid] = s
    for s in found.values():
        s["title"] = _title_of(s["session_id"])
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


def _agent_from_meta(folder: Path, tid: str, m: dict[str, Any]) -> dict[str, Any]:
    """A subagent known only from its meta file (subagents/agent-<id>.meta.json next to its transcript)."""
    meta: Path = m["_file"]
    agent_id = meta.name.removeprefix("agent-").removesuffix(".meta.json")
    started = ended = 0.0
    try:
        ended = meta.stat().st_mtime if m.get("finished") else 0.0
        with (folder / f"agent-{agent_id}.jsonl").open("rb") as f:
            started = _ts((json.loads(f.readline() or b"{}") or {}).get("timestamp"))
    except (OSError, ValueError, AttributeError):
        pass
    return {"id": tid, "type": str(m.get("agentType") or "general-purpose"), "description": str(m.get("description") or ""),
            "status": "completed" if m.get("finished") else "running",
            "background": m.get("requestShape") == "background", "model": "", "started": started or ended,
            "ended": ended, "tokens": 0, "tools": 0, "agent_id": agent_id}


def _task_files(session_id: str) -> list[dict[str, str]]:
    """Claude Code's own task list of a session (TaskCreate / TaskUpdate): ~/.claude/tasks/<session>/<n>.json."""
    out = []
    for f in (claude_home() / "tasks" / session_id).glob("*.json"):
        try:
            t = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(t, dict) and t.get("status") != "deleted":
            out.append({"id": str(t.get("id") or f.stem), "subject": str(t.get("subject") or "")[:300],
                        "status": str(t.get("status") or "pending")})
    return sorted(out, key=lambda t: (len(t["id"]), t["id"]))[:200]


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
                metas[str(m["toolUseId"])] = m | {"_file": f}
        for a in agents:
            m = metas.pop(a["id"], None)
            if m and m.get("finished") and a["status"] == "running":
                a["status"] = "completed"
        # Started before the part of a long transcript that was read: only the meta file tells of them.
        agents = sorted((_agent_from_meta(tr.path.parent / session_id / "subagents", tid, m) for tid, m in metas.items()),
                        key=lambda a: a["started"]) + agents
        snap["task_list"] = _task_files(session_id) or snap["task_list"]
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
        **snap, "session_id": session_id, "title": _title_of(session_id) if tr else "", "live": live is not None,
        "status": status, "process": live,
        "transcript": str(tr.path) if tr else "", "cwd": cwd,
        "version": snap["version"] or (live or {}).get("version", ""),
        "entrypoint": (live or {}).get("entrypoint") or snap["entrypoint"],
        "current": current, "agents": agents, "tasks": tasks, "configured_agents": configured,
    }


def host_activity(cwd: str = "") -> dict[str, Any]:
    """Every live Claude Code session on this host and what runs in it: the overview under /agents and /tasks."""
    rows, agent_types = [], []
    for s in sessions():
        try:
            d = session_status(s["session_id"], after=1 << 62)  # no events: only the state
        except (KeyError, ValueError):
            continue
        agent_types = agent_types or d["agent_types"]
        run = [a for a in d["agents"] if a["status"] == "running"]
        bg = [t for t in d["tasks"] if t["status"] == "running"]
        rows.append({"session_id": s["session_id"], "title": d["title"] or s.get("title") or s.get("name") or "",
                     "cwd": d["cwd"], "status": s["status"], "entrypoint": d["entrypoint"],
                     "agents": len(d["agents"]), "agents_running": run, "tasks_running": bg,
                     "todos_open": sum(t["status"] != "completed" for t in d["todos"] + d["task_list"])})
    configured = _agent_files(claude_home()) + (_agent_files(Path(cwd) / ".claude") if cwd and Path(cwd).is_absolute() else [])
    return {"sessions": rows, "busy": sum(r["status"] == "busy" for r in rows),
            "agents_running": sum(len(r["agents_running"]) for r in rows),
            "tasks_running": sum(len(r["tasks_running"]) for r in rows),
            "agent_types": agent_types, "configured_agents": configured}


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"  # what Claude Code's own /usage reads
LIMITS_TTL = 60.0
_limits_cache: dict[str, Any] = {"key": None, "at": 0.0, "out": None}
_LIMIT_LABELS = {"session": "5-hour session", "weekly_all": "Weekly limit"}


def _oauth_usage(token: str) -> dict[str, Any]:
    req = urllib.request.Request(USAGE_URL, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                                                     "User-Agent": "agent-company"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def _parse_limits(d: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for x in d.get("limits") or []:
        if not isinstance(x, dict) or not isinstance(x.get("percent"), int | float):
            continue
        kind = str(x.get("kind") or "")
        scope = x.get("scope") if isinstance(x.get("scope"), dict) else {}
        model = (scope.get("model") or {}).get("display_name") if isinstance(scope.get("model"), dict) else None
        label = _LIMIT_LABELS.get(kind) or (f"Weekly limit · {model}" if model else kind.replace("_", " ").capitalize())
        out.append({"kind": kind, "label": label, "percent": float(x["percent"]),
                    "severity": str(x.get("severity") or "normal"), "resets_at": str(x.get("resets_at") or "")})
    if not out:  # the older answer: one object per window
        for key, kind, label in (("five_hour", "session", "5-hour session"), ("seven_day", "weekly_all", "Weekly limit"),
                                 ("seven_day_opus", "weekly_scoped", "Weekly limit · Opus"),
                                 ("seven_day_sonnet", "weekly_scoped", "Weekly limit · Sonnet")):
            w = d.get(key)
            if isinstance(w, dict) and isinstance(w.get("utilization"), int | float):
                out.append({"kind": kind, "label": label, "percent": float(w["utilization"]), "severity": "normal",
                            "resets_at": str(w.get("resets_at") or "")})
    return out


def claude_limits() -> dict[str, Any]:
    """How much of the Claude plan's limits is used (5-hour session, week), as Claude Code's /usage shows it.

    Read with the host's Claude Code sign-in (~/.claude/.credentials.json); the token never leaves the server.
    An API-key login has no plan limits."""
    path = claude_home() / ".credentials.json"
    try:
        st = path.stat()
        oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return {"available": False, "reason": "Claude Code is not signed in with a Claude account", "limits": []}
    key = (str(path), st.st_mtime_ns)
    c = _limits_cache
    if c["key"] == key and time.time() - c["at"] < LIMITS_TTL:
        return c["out"]
    token, plan = str(oauth.get("accessToken") or ""), str(oauth.get("subscriptionType") or "")
    out: dict[str, Any] = {"available": False, "plan": plan, "limits": [], "reason": ""}
    if not token:
        out["reason"] = "Claude Code is not signed in with a Claude account"
    elif oauth.get("expiresAt") and float(oauth["expiresAt"]) / 1000 < time.time():
        out["reason"] = "Claude Code's sign-in has expired: it renews it on its next run"
    else:
        try:
            out["limits"] = _parse_limits(_oauth_usage(token))
            out["available"] = bool(out["limits"])
            out["reason"] = "" if out["limits"] else "this plan reports no usage limits"
        except urllib.error.HTTPError as e:
            if e.code == 429 and c["key"] == key and c["out"] and c["out"]["available"]:
                return c["out"]  # rate limited: keep the last answer
            out["reason"] = "Claude Code's sign-in was refused: sign in again" if e.code in {401, 403} else f"limits unavailable (HTTP {e.code})"
        except (OSError, ValueError) as e:
            out["reason"] = f"limits unavailable ({type(e).__name__})"
    out["at"] = time.time()
    c.update(key=key, at=time.time(), out=out)
    return out


def stop_session(session_id: str) -> dict[str, Any]:
    """End a live Claude Code session on this host (SIGTERM to its process)."""
    if not _SESSION_RE.fullmatch(session_id or ""):
        raise ValueError("bad session id")
    run = next((r for r in RUNS.values() if r.session_id == session_id and r.status == "running"), None)
    if run is not None:  # one of this panel's prompts: stop it the usual way
        stop(run.id)
        return {"session_id": session_id, "pid": run.proc.pid if run.proc else 0, "panel": True}
    live = next((s for s in sessions(refresh=True) if s["session_id"] == session_id), None)
    if live is None:
        raise KeyError(f"session {session_id}")
    if live["pid"] == os.getpid():
        raise ValueError("that is this server's own process")
    try:
        os.kill(live["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        raise ValueError(f"not allowed to stop process {live['pid']}") from e
    global _sessions_cache
    _sessions_cache = (0.0, [])  # the process exits shortly: the next listing looks again
    return {"session_id": session_id, "pid": live["pid"], "panel": False}


# --- history: every session with a transcript --------------------------------------------------

HISTORY_HEAD = 65_536  # bytes read from the start of a transcript: folder, branch, first prompt
HISTORY_TAIL = 131_072  # and from its end: renamed / AI title, last prompt
_history_cache: dict[str, tuple[tuple[float, int], dict[str, Any]]] = {}


def _prompt_of(raw: dict[str, Any]) -> str:
    if raw.get("isMeta") or raw.get("isSidechain"):
        return ""
    content = (raw.get("message") or {}).get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
        return ""
    texts = [str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(x for x in texts if x.strip() and not x.lstrip().startswith("<")).strip()


def _summary(path: Path, size: int) -> dict[str, Any]:
    """What the history window shows for one transcript, from its head and tail only."""
    with path.open("rb") as f:
        head = f.read(HISTORY_HEAD)
        tail = b""
        if size > HISTORY_HEAD:
            f.seek(max(HISTORY_HEAD, size - HISTORY_TAIL))
            tail = f.read()
            if size - HISTORY_TAIL > HISTORY_HEAD:
                tail = tail[tail.find(b"\n") + 1:]  # drop the cut line
    out: dict[str, Any] = {"cwd": "", "git_branch": "", "entrypoint": "", "version": "", "started": 0.0,
                           "first_prompt": "", "last_prompt": "", "custom_title": "", "ai_title": ""}
    for line in (head + b"\n" + tail).splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        t = raw.get("type")
        if t == "custom-title" and raw.get("customTitle"):
            out["custom_title"] = str(raw["customTitle"])[:200]
        elif t == "ai-title" and raw.get("aiTitle"):
            out["ai_title"] = str(raw["aiTitle"])[:200]
        elif t == "last-prompt" and raw.get("lastPrompt"):
            out["last_prompt"] = str(raw["lastPrompt"])[:500]
        elif t in {"user", "assistant"} and not raw.get("isSidechain"):
            for key, field in (("cwd", "cwd"), ("gitBranch", "git_branch"), ("entrypoint", "entrypoint"),
                               ("version", "version")):
                if raw.get(key) and not out[field]:
                    out[field] = str(raw[key])
            if not out["started"]:
                out["started"] = _ts(raw.get("timestamp"))
            if t == "user":
                prompt = _prompt_of(raw)
                if prompt:
                    out["first_prompt"] = out["first_prompt"] or prompt[:500]
                    out["last_prompt"] = prompt[:500]
    return out


def history(query: str = "", limit: int = 200) -> dict[str, Any]:
    """Every Claude Code session on this host that left a transcript, newest first, with live ones marked."""
    live = {s["session_id"]: s for s in sessions()}
    found: dict[str, dict[str, Any]] = {}
    paths = list((claude_home() / "projects").glob("*/*.jsonl"))
    for gone in set(_history_cache) - {str(p) for p in paths}:
        _history_cache.pop(gone, None)
    for path in paths:
        sid = path.stem
        if not _SESSION_RE.fullmatch(sid):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        cached = _history_cache.get(key)
        if cached is None or cached[0] != (st.st_mtime, st.st_size):
            try:
                cached = ((st.st_mtime, st.st_size), _summary(path, st.st_size))
            except OSError:
                continue
            _history_cache[key] = cached
        s = cached[1]
        if not (s["first_prompt"] or s["custom_title"] or s["ai_title"]) and sid not in live:
            continue  # opened and closed without a prompt
        proc = live.get(sid)
        row = {**s, "session_id": sid, "updated": st.st_mtime, "size": st.st_size,
               "title": s["custom_title"] or s["ai_title"] or _run_title(sid) or s["first_prompt"].split("\n")[0][:120] or sid[:8],
               "status": proc["status"] if proc else "ended", "pid": proc["pid"] if proc else 0,
               "cwd": s["cwd"] or (proc or {}).get("cwd", ""),
               "entrypoint": (proc or {}).get("entrypoint") or s["entrypoint"]}
        if sid not in found or row["updated"] > found[sid]["updated"]:  # in two project folders: the newer
            found[sid] = row
    rows = list(found.values())
    q = query.strip().lower()
    if q:
        rows = [r for r in rows if any(q in str(r[k]).lower() for k in
                                       ("title", "cwd", "first_prompt", "last_prompt", "session_id", "git_branch"))]
    rows.sort(key=lambda r: (r["status"] == "ended", -r["updated"]))
    return {"total": len(rows), "items": rows[:max(1, limit)]}


_titles: dict[str, tuple[float, str]] = {}
TITLE_TTL = 30.0


def _title_of(session_id: str) -> str:
    """A live session's name from its transcript: renamed, AI-written, else its first prompt."""
    at, title = _titles.get(session_id, (0.0, ""))
    if time.time() - at < TITLE_TTL:
        return title
    path = _transcript_path(session_id)
    title = ""
    if path is not None:
        try:
            s = _summary(path, path.stat().st_size)
            title = s["custom_title"] or s["ai_title"] or _run_title(session_id) or _fallback_title(s["first_prompt"])
        except OSError:
            pass
    _titles[session_id] = (time.time(), title)
    if len(_titles) > 500:
        _titles.pop(next(iter(_titles)))
    return title


def delete_session(session_id: str) -> dict[str, Any]:
    """Delete an ended session's transcript, its subagent transcripts and its file checkpoints."""
    if not _SESSION_RE.fullmatch(session_id or ""):
        raise ValueError("bad session id")
    if any(s["session_id"] == session_id for s in sessions(refresh=True)):
        raise ValueError("the session is still running: stop it first")
    home = claude_home()
    paths = list((home / "projects").glob(f"*/{session_id}.jsonl"))
    if not paths:
        raise KeyError(f"session {session_id}")
    for p in paths:
        p.unlink(missing_ok=True)
        _history_cache.pop(str(p), None)
        if (p.parent / session_id).is_dir():
            shutil.rmtree(p.parent / session_id, ignore_errors=True)
    if (home / "file-history" / session_id).is_dir():
        shutil.rmtree(home / "file-history" / session_id, ignore_errors=True)
    with _watch_lock:
        _watch.pop(session_id, None)
    return {"session_id": session_id, "deleted": [str(p) for p in paths]}


# --- token use of every Claude Code session on this host (the panel's /status) -------------------
# Read from the transcripts (subagents' too): each assistant message records its usage, repeated on every
# line of the message, so it is counted once per (message id, request id). Files are read incrementally.
# Windows are the Cost and Usage monitor's (usage.py): today and this week from 00:00 UTC, Monday.
_usage_files: dict[str, dict[str, Any]] = {}
_usage_lock = threading.Lock()
_prices_cache: tuple[float, dict[str, tuple[float, float]]] = (0.0, {})


def _model_key(model: str) -> str:
    return re.sub(r"-\d{8}$", "", model.rsplit("/", 1)[-1].strip().lower())


def _prices() -> dict[str, tuple[float, float]]:
    """$ per 1M input/output tokens per model id, from the Model Router's catalog (editable in Models)."""
    global _prices_cache
    if time.time() - _prices_cache[0] < 60:
        return _prices_cache[1]
    from sqlalchemy import select

    from agent_company.db import ModelEntry, session

    out: dict[str, tuple[float, float]] = {}
    try:
        with session() as s:
            for m in s.scalars(select(ModelEntry).where(ModelEntry.kind == "llm")).all():
                if m.model_id and (m.input_cost or m.output_cost):
                    out.setdefault(_model_key(m.model_id), (m.input_cost, m.output_cost))
    except Exception:  # noqa: BLE001 - no prices only means no cost, never a failed /status
        pass
    _prices_cache = (time.time(), out)
    return out


def _usage_scan(path: Path, entry: dict[str, Any]) -> None:
    """Read what was appended to a transcript since the last scan (whole lines only)."""
    with path.open("rb") as f:
        f.seek(entry["offset"])
        data = f.read()
    end = data.rfind(b"\n") + 1
    entry["offset"] += end
    for line in data[:end].splitlines():
        if b'"usage"' not in line or b'"assistant"' not in line:
            continue  # tool results and prompts are most of the bytes: skip them unparsed
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        msg = raw.get("message") or {}
        u = msg.get("usage") or {}
        model = str(msg.get("model") or "")
        if raw.get("type") != "assistant" or not u or model.startswith("<"):
            continue
        cw = u.get("cache_creation") or {}
        cw1h = int(cw.get("ephemeral_1h_input_tokens") or 0)
        entry["msgs"][(msg.get("id"), raw.get("requestId"))] = (
            _ts(raw.get("timestamp")), _model_key(model), int(u.get("input_tokens") or 0),
            int(u.get("output_tokens") or 0), int(u.get("cache_read_input_tokens") or 0),
            max(0, int(u.get("cache_creation_input_tokens") or 0) - cw1h), cw1h)


def claude_usage() -> dict[str, Any]:
    """Tokens and their API-price cost today and this week, all Claude Code sessions on this host."""
    from agent_company import usage

    t = usage.now()
    d0, w0 = usage.day_start(t), usage.week_start(t)
    next_day, next_week = d0 + timedelta(days=1), w0 + timedelta(days=7)
    since = w0.timestamp()
    with _usage_lock:
        seen: set[str] = set()
        for path in (claude_home() / "projects").glob("**/*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < since:
                continue
            key = str(path)
            seen.add(key)
            entry = _usage_files.get(key)
            if entry is None or st.st_size < entry["offset"]:  # new, or rewritten shorter: read it again
                entry = _usage_files[key] = {"offset": 0, "msgs": {}}
            if st.st_size > entry["offset"]:
                try:
                    _usage_scan(path, entry)
                except OSError:
                    continue
        for gone in set(_usage_files) - seen:
            _usage_files.pop(gone, None)
        msgs: dict[Any, tuple[Any, ...]] = {}
        for entry in _usage_files.values():
            msgs.update(entry["msgs"])
    prices = _prices()

    def window(start: float, resets: datetime) -> dict[str, Any]:
        w = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
             "tokens": 0, "cost": 0.0, "unpriced_tokens": 0, "messages": 0, "models": {}}
        for at, model, i, o, cr, cw5, cw1h in msgs.values():
            if at < start:
                continue
            n = i + o + cr + cw5 + cw1h
            w["messages"] += 1
            w["input_tokens"] += i
            w["output_tokens"] += o
            w["cache_read_tokens"] += cr
            w["cache_write_tokens"] += cw5 + cw1h
            w["tokens"] += n
            m = w["models"].setdefault(model, {"model": model, "tokens": 0, "cost": 0.0, "priced": model in prices})
            m["tokens"] += n
            if model in prices:  # cache reads cost 0.1x input, 5-minute writes 1.25x, 1-hour writes 2x
                pin, pout = prices[model]
                cost = (pin * (i + 0.1 * cr + 1.25 * cw5 + 2 * cw1h) + pout * o) / 1_000_000
                w["cost"] += cost
                m["cost"] += cost
            else:
                w["unpriced_tokens"] += n
        w["cost"] = round(w["cost"], 4)
        w["models"] = sorted(({**m, "cost": round(m["cost"], 4)} for m in w["models"].values()),
                             key=lambda m: -m["tokens"])
        return w | {"since": datetime.fromtimestamp(start, UTC).isoformat(), "resets_at": resets.isoformat(),
                    "resets_in_seconds": max(0, int((resets - t).total_seconds()))}

    return {"daily": window(d0.timestamp(), next_day), "weekly": window(since, next_week),
            "now": t.isoformat(), "files": len(_usage_files)}


def warm_usage() -> None:
    """Read the transcripts in the background, so the first /status answers at once."""
    if not _usage_files and not _usage_lock.locked():
        threading.Thread(target=lambda: _safe(claude_usage), daemon=True, name="claude-usage").start()


def _safe(fn: Any) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - a warm-up only
        pass
