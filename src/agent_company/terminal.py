"""Workspace terminal (administrators): real interactive shells on pseudo-terminals.

This is the administrator's own console, like the terminal in an editor: it runs as the
server's user with no command filter, so `ssh`, `sudo`, editors and full-screen programs
work. It is not an agent executor - agents still run sandboxed in `data/worktrees/` and pass
`governance.check_command`. Opening and closing a session is audited by the API.

Sessions outlive the browser connection (switching views or reloading re-attaches and
replays the recent output); a session with nobody attached for `IDLE_LIMIT` seconds is
closed. Output reaches the browser through a WebSocket as raw bytes for xterm.js.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import struct
import subprocess
import termios
import time
import uuid
from pathlib import Path
from typing import Any

from agent_company.config import settings

SCROLLBACK = 256 * 1024
IDLE_LIMIT = 6 * 3600
MAX_SESSIONS = 12

# Bash start-up for workspace shells: the user's own rc files, then colours - a coloured
# prompt with the git branch and the last exit status, coloured ls/grep/diff, and the typed
# command shown in its own colour (PS0 resets it before the command's output).
RC = r"""
[ -f /etc/bash.bashrc ] && . /etc/bash.bashrc
[ -f ~/.bashrc ] && . ~/.bashrc
alias ls='ls --color=auto' grep='grep --color=auto' egrep='egrep --color=auto' diff='diff --color=auto' ip='ip -color=auto'
export CLICOLOR=1 GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'
__ac_branch() { local b; b=$(git symbolic-ref --short HEAD 2>/dev/null) && printf ' \001\e[1;35m\002(%s)\001\e[0m\002' "$b"; }
__ac_status() { local s=$?; [ $s -ne 0 ] && printf '\001\e[1;31m\002✗ %s \001\e[0m\002' "$s"; return $s; }
PS1='$(__ac_status)\[\e[1;32m\]\u@\h\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]$(__ac_branch)\[\e[1;36m\] \$\[\e[0m\] \[\e[1;33m\]'
PS0='\[\e[0m\]'
"""


def _rc_file() -> Path:
    path = settings().data_dir / "terminal" / "bashrc"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != RC:
        path.write_text(RC)
    return path


def _set_size(fd: int, cols: int, rows: int) -> None:
    cols, rows = max(2, min(int(cols), 1000)), max(2, min(int(rows), 500))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _controlling_tty() -> None:  # runs in the child after setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Session:
    def __init__(self, cwd: Path, cols: int, rows: int, owner: str, shell: str | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.cwd, self.owner = str(cwd), owner
        self.created = self.last_seen = time.time()
        self.buffer = bytearray()
        self.listeners: set[asyncio.Queue[bytes | None]] = set()
        self.exit_code: int | None = None
        self._pending = bytearray()
        master, slave = os.openpty()
        _set_size(master, cols, rows)
        shell = shell or os.environ.get("SHELL") or "/bin/bash"
        argv = [shell, "--rcfile", str(_rc_file()), "-i"] if Path(shell).name == "bash" else [shell, "-i"]
        env = {k: v for k, v in os.environ.items() if k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}}
        env.update(TERM="xterm-256color", COLORTERM="truecolor", AGENT_COMPANY_TERMINAL="1")
        self.title = Path(shell).name
        try:
            self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                                         start_new_session=True, preexec_fn=_controlling_tty, close_fds=True)
        finally:
            os.close(slave)
        self.fd = master
        os.set_blocking(master, False)
        self.loop = asyncio.get_running_loop()
        self.loop.add_reader(master, self._on_read)

    # --- output -------------------------------------------------------------------
    def _on_read(self) -> None:
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._finish()
            return
        self.buffer += data
        if len(self.buffer) > SCROLLBACK:
            del self.buffer[: len(self.buffer) - SCROLLBACK]
        for q in list(self.listeners):
            q.put_nowait(data)

    def _finish(self) -> None:
        if self.fd < 0:
            return
        self.loop.remove_reader(self.fd)
        self.loop.remove_writer(self.fd)
        try:
            self.exit_code = self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.exit_code = -1
        os.close(self.fd)
        self.fd = -1
        for q in list(self.listeners):
            q.put_nowait(None)

    @property
    def alive(self) -> bool:
        return self.fd >= 0

    # --- input --------------------------------------------------------------------
    def write(self, data: bytes) -> None:
        if not self.alive or not data:
            return
        self._pending += data
        self._flush()

    def _flush(self) -> None:
        if not self.alive:
            return
        try:
            n = os.write(self.fd, self._pending)
            del self._pending[:n]
        except BlockingIOError:
            pass
        except OSError:
            self._pending.clear()
        if self._pending:
            self.loop.add_writer(self.fd, self._flush)
        else:
            self.loop.remove_writer(self.fd)

    def resize(self, cols: int, rows: int) -> None:
        if self.alive:
            _set_size(self.fd, cols, rows)

    # --- attach / close -------------------------------------------------------------
    def attach(self) -> asyncio.Queue[bytes | None]:
        q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.listeners.add(q)
        self.last_seen = time.time()
        return q

    def detach(self, q: asyncio.Queue[bytes | None]) -> None:
        self.listeners.discard(q)
        self.last_seen = time.time()

    def close(self) -> None:
        if self.alive:
            for sig in (signal.SIGHUP, signal.SIGKILL):
                try:
                    os.killpg(self.proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    self.proc.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            self._finish()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "cwd": self.cwd, "owner": self.owner, "title": self.title, "alive": self.alive,
                "exit_code": self.exit_code, "attached": len(self.listeners), "created": self.created}


SESSIONS: dict[str, Session] = {}


def _reap() -> None:
    now = time.time()
    for sid, s in list(SESSIONS.items()):
        idle = 0 if s.listeners else now - s.last_seen
        if s.alive and idle > IDLE_LIMIT:
            s.close()
        if not s.alive and idle > 60:  # keep an exited shell briefly so its tab can show the exit
            SESSIONS.pop(sid, None)


def create(cwd: str, owner: str, cols: int = 120, rows: int = 32, shell: str | None = None) -> Session:
    """Start a shell. Must be called from the server's event loop (it registers a reader)."""
    _reap()
    if sum(1 for s in SESSIONS.values() if s.alive) >= MAX_SESSIONS:
        raise ValueError(f"at most {MAX_SESSIONS} terminals may be open; close one first")
    folder = Path(cwd or Path.home()).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    s = Session(folder, cols, rows, owner, shell)
    SESSIONS[s.id] = s
    return s


def get(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if s is None:
        raise KeyError(f"terminal {sid}")
    return s


def list_sessions() -> list[dict[str, Any]]:
    _reap()
    return [s.to_dict() for s in SESSIONS.values()]


def close(sid: str) -> None:
    s = SESSIONS.pop(sid, None)
    if s is not None:
        s.close()


def close_all() -> None:
    for sid in list(SESSIONS):
        close(sid)
