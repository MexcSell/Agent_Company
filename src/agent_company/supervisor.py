"""Supervisor: keep the service up and restart it safely when its code changes.

    uv run python -m agent_company supervise

  source change (src/agent_company/**/*.py)
      -> syntax check (compileall) -> candidate boots on PORT+1 without background
         workers -> health check -> wait until no goal is running -> stop the old
         server gracefully -> start the new one -> health check
  a failing candidate never replaces the running service; the error is kept in
  data/supervisor.json and shown on the System page
  the server exits unexpectedly -> restarted with backoff
  `POST /api/system/restart` (or data/restart.request) -> the same safe restart

Static files (the dashboard) are served from disk, so a change there needs no restart.
When an approved goal of the protected platform project merges into this checkout,
this is how the running service picks the change up.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from agent_company.config import REPO_ROOT, settings

SRC = REPO_ROOT / "src" / "agent_company"
POLL = 2.0


def state_file() -> Path:
    return settings().data_dir / "supervisor.json"


def request_file() -> Path:
    return settings().data_dir / "restart.request"


def read_state() -> dict[str, Any] | None:
    try:
        data = json.loads(state_file().read_text())
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        data["running"] = False
        return data
    data["running"] = True
    return data


def request_restart(reason: str) -> bool:
    st = read_state()
    if not st or not st.get("running"):
        return False
    request_file().write_text(reason[:200])
    return True


def snapshot() -> dict[str, float]:
    return {str(p): p.stat().st_mtime for p in SRC.rglob("*.py") if "__pycache__" not in p.parts}


def running_goals() -> int:
    from sqlalchemy import func, select

    from agent_company.db import Goal, session

    with session() as s:
        from agent_company.workflow import WORKING

        return s.scalar(select(func.count()).select_from(Goal).where(Goal.status.in_(WORKING))) or 0


def healthy(port: int, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


class Supervisor:
    def __init__(self, port: int | None = None, host: str | None = None, max_wait: int = 1800):
        self.port = port or settings().port
        self.host = host or settings().host
        self.max_wait = max_wait
        self.child: subprocess.Popen | None = None
        self.state: dict[str, Any] = {"pid": os.getpid(), "port": self.port, "restarts": 0, "last_error": None,
                                      "last_restart": None, "pending": None, "started": time.time()}
        self.stopping = False

    def save(self) -> None:
        settings().data_dir.mkdir(parents=True, exist_ok=True)
        state_file().write_text(json.dumps(self.state | {"child_pid": self.child.pid if self.child else None}))

    def spawn(self, port: int, candidate: bool = False) -> subprocess.Popen:
        env = {**os.environ, "AC_CANDIDATE": "1" if candidate else "0"}
        return subprocess.Popen([sys.executable, "-m", "agent_company", "serve", "--host", "127.0.0.1" if candidate
                                 else self.host, "--port", str(port)], cwd=REPO_ROOT, env=env)

    def stop(self, proc: subprocess.Popen | None, grace: float = 20.0) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)

    def check_candidate(self) -> str | None:
        """None if the new code is good, else why not."""
        r = subprocess.run([sys.executable, "-m", "compileall", "-q", str(SRC)], capture_output=True, text=True)
        if r.returncode:
            return "syntax check failed: " + (r.stdout + r.stderr).strip()[-1500:]
        cand = self.spawn(self.port + 1, candidate=True)
        try:
            if not healthy(self.port + 1, 60):
                return "the candidate did not answer its health check"
            return None
        finally:
            self.stop(cand, 10)

    def safe_restart(self, reason: str) -> None:
        self.state["pending"] = reason
        self.save()
        problem = self.check_candidate()
        if problem:
            self.state.update(last_error=f"{reason}: {problem}", pending=None)
            self.save()
            print(f"[supervisor] not restarting: {problem}", flush=True)
            return
        waited = 0.0
        while running_goals() and waited < self.max_wait and not self.stopping:
            self.state["pending"] = f"{reason} (waiting for {running_goals()} running goal(s))"
            self.save()
            time.sleep(POLL)
            waited += POLL
        self.stop(self.child)
        self.child = self.spawn(self.port)
        ok = healthy(self.port, 60)
        self.state.update(restarts=self.state["restarts"] + 1, last_restart=time.time(), pending=None,
                          last_error=None if ok else f"{reason}: the new server did not become healthy")
        self.save()
        print(f"[supervisor] restarted ({reason}){'' if ok else ' but it is not healthy'}", flush=True)

    def run(self) -> None:
        def on_signal(*_: Any) -> None:
            self.stopping = True

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        self.child = self.spawn(self.port)
        self.save()
        seen = snapshot()
        backoff = 1.0
        print(f"[supervisor] serving on {self.host}:{self.port} (pid {self.child.pid})", flush=True)
        while not self.stopping:
            time.sleep(POLL)
            if self.child.poll() is not None and not self.stopping:
                code = self.child.returncode
                print(f"[supervisor] server exited ({code}); restarting in {backoff:.0f}s", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                self.child = self.spawn(self.port)
                self.state["last_error"] = f"server exited with code {code}; restarted"
                self.save()
                continue
            backoff = 1.0
            now_snap = snapshot()
            reason = None
            if now_snap != seen:
                changed = sorted(set(now_snap.items()) ^ set(seen.items()))
                reason = f"code changed ({Path(changed[0][0]).name}{' and more' if len(changed) > 2 else ''})"
                seen = now_snap
            elif request_file().exists():
                reason = request_file().read_text() or "restart requested"
                request_file().unlink(missing_ok=True)
            if reason:
                self.safe_restart(reason)
                seen = snapshot()
        self.stop(self.child)
        state_file().unlink(missing_ok=True)
