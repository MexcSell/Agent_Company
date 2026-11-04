"""Task Scheduler: priorities, dependencies and concurrency.

Tasks are persisted (`tasks` table). `run_dag` runs one iteration's tasks: a task
starts once all its dependencies are done, ready tasks start lowest `priority`
first, at most `max_parallel` run at once (process-wide), and tasks that write
files take the worktree's write lock so two writers never edit one tree at once.
A failed task skips everything that depends on it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from sqlalchemy import select

from agent_company.config import settings
from agent_company.db import Task, now, session

_global_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()
_write_locks: dict[str, threading.Lock] = {}


def global_slots() -> threading.BoundedSemaphore:
    global _global_slots
    with _slots_lock:
        if _global_slots is None:
            _global_slots = threading.BoundedSemaphore(max(1, settings().max_parallel))
        return _global_slots


def write_lock(worktree: str) -> threading.Lock:
    with _slots_lock:
        return _write_locks.setdefault(worktree, threading.Lock())


def set_status(task_id: int, status: str, **fields: object) -> None:
    with session() as s:
        t = s.get(Task, task_id)
        if t is None:
            return
        t.status = status
        if status == "running":
            t.started_at = now()
        if status in {"done", "failed", "skipped"}:
            t.finished_at = now()
        for k, v in fields.items():
            setattr(t, k, v)
        info = {"task_id": t.id, "goal_id": t.goal_id, "key": t.key, "title": t.title, "status": status,
                "agent_id": t.agent_id, "role": t.role}
    from agent_company import hooks

    if status == "running":
        hooks.emit("TaskCreated", **info)
    elif status in {"done", "failed", "skipped"}:
        hooks.emit("TaskCompleted", **info)


def run_dag(task_ids: list[int], worker: Callable[[int], bool], worktree: str,
            cancelled: Callable[[], bool] = lambda: False) -> dict[int, str]:
    """Run tasks respecting dependencies. `worker(task_id)` returns True on success."""
    with session() as s:
        tasks = {t.id: (t.key, list(t.depends_on), t.priority, t.writes)
                 for t in s.scalars(select(Task).where(Task.id.in_(task_ids)))}
    by_key = {v[0]: tid for tid, v in tasks.items()}
    state: dict[int, str] = {tid: "queued" for tid in tasks}
    lock = write_lock(worktree)
    slots = global_slots()

    def deps_of(tid: int) -> list[int]:
        return [by_key[k] for k in tasks[tid][1] if k in by_key]

    def run_one(tid: int) -> bool:
        with slots:
            if tasks[tid][3]:
                with lock:
                    return _guarded(worker, tid)
            return _guarded(worker, tid)

    running: dict[Future[bool], int] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        while True:
            for tid, st in state.items():
                if st != "queued":
                    continue
                dep_states = [state[d] for d in deps_of(tid)]
                if any(x in {"failed", "skipped"} for x in dep_states):
                    state[tid] = "skipped"
                    set_status(tid, "skipped", output="skipped: a dependency failed")
            if cancelled():
                for tid, st in state.items():
                    if st == "queued":
                        state[tid] = "skipped"
                        set_status(tid, "skipped", output="skipped: goal cancelled")
            ready = sorted((tid for tid, st in state.items() if st == "queued"
                            and all(state[d] == "done" for d in deps_of(tid))),
                           key=lambda t: (tasks[t][2], t))
            for tid in ready:
                state[tid] = "running"
                running[pool.submit(run_one, tid)] = tid
            if not running:
                break
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for fut in done:
                tid = running.pop(fut)
                state[tid] = "done" if fut.result() else "failed"
    return state


def _guarded(worker: Callable[[int], bool], tid: int) -> bool:
    try:
        return bool(worker(tid))
    except Exception as e:  # noqa: BLE001 - record, never kill the scheduler
        set_status(tid, "failed", output=f"worker crashed: {type(e).__name__}: {e}")
        return False
