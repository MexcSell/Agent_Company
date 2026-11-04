"""System status: host load, memory, disk, the service process, the queue and the supervisor."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from typing import Any

from sqlalchemy import func, select

from agent_company import worktrees
from agent_company.config import REPO_ROOT, settings
from agent_company.db import Agent, Goal, Task, session

STARTED = time.time()


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            out[k] = int(v.split()[0]) * 1024
    except OSError:
        pass
    return out


def status() -> dict[str, Any]:
    from agent_company.supervisor import read_state

    mem = _meminfo()
    disk = shutil.disk_usage(settings().data_dir if settings().data_dir.exists() else REPO_ROOT)
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    with session() as s:
        goals = dict(s.execute(select(Goal.status, func.count()).group_by(Goal.status)).all())
        running_tasks = s.scalar(select(func.count()).select_from(Task).where(Task.status == "running")) or 0
        busy = s.scalar(select(func.count()).select_from(Agent).where(Agent.status == "busy")) or 0
    try:
        branch = worktrees.git(REPO_ROOT, "rev-parse", "--abbrev-ref", "HEAD")
        dirty = len([x for x in worktrees.git(REPO_ROOT, "status", "--porcelain").splitlines() if x])
        head = worktrees.git(REPO_ROOT, "log", "-1", "--format=%h %s")
    except worktrees.WorktreeError:
        branch, dirty, head = "", 0, ""
    return {
        "host": {"system": f"{platform.system()} {platform.release()}", "cpus": os.cpu_count(),
                 "load": [round(x, 2) for x in load], "memory_total": mem.get("MemTotal"),
                 "memory_available": mem.get("MemAvailable"), "disk_total": disk.total, "disk_free": disk.free},
        "service": {"pid": os.getpid(), "python": sys.version.split()[0], "uptime_seconds": int(time.time() - STARTED),
                    "data_dir": str(settings().data_dir), "database": settings().database_url.split("://")[0],
                    "executor": settings().executor},
        "work": {"goals": goals, "running_tasks": running_tasks, "busy_agents": busy},
        "repository": {"path": str(REPO_ROOT), "branch": branch, "uncommitted_changes": dirty, "head": head},
        "supervisor": read_state(),
    }
