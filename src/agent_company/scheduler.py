"""Task Scheduler: priorities, dependencies and concurrency.

Tasks are persisted (`tasks` table). `run_dag` runs one iteration's tasks: a task
starts once all its dependencies are done, ready tasks start lowest `priority`
first, at most `max_parallel` run at once (process-wide), and tasks that write
files take the worktree's write lock so two writers never edit one tree at once.
A failed task skips everything that depends on it, except a review: a review
(`quality.REVIEW_ROLES`) waits until all its dependencies finished and runs on
partial work when at least one is done; it is skipped only when none is done.
A contract's `after` keys set order only: the task waits until they finished,
whatever their outcome, and is never skipped because of them.
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


def set_status(task_id: int, status: str, stage: str = "", **fields: object) -> None:
    """Set a task's execution status and the lifecycle stage that goes with it (`stage` overrides the mapping)."""
    from agent_company.workflow import STATUS_STAGE, set_stage

    with session() as s:
        t = s.get(Task, task_id)
        if t is None:
            return
        t.status = status
        output = str(fields.get("output") or "")
        set_stage(t, stage or ("cancelled" if output == "skipped: goal cancelled" else STATUS_STAGE[status]),
                  output.split("\n", 1)[0][:200] if status in ("failed", "skipped") else "")
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


def set_task_stage(task_id: int, stage: str, reason: str = "") -> None:
    from agent_company.workflow import set_stage

    with session() as s:
        t = s.get(Task, task_id)
        if t is not None:
            set_stage(t, stage, reason)


def run_dag(task_ids: list[int], worker: Callable[[int], bool], worktree: str,
            cancelled: Callable[[], bool] = lambda: False, max_agents: int = 0) -> dict[int, str]:
    """Run tasks respecting dependencies. `worker(task_id)` returns True on success. `max_agents` (> 0) caps
    how many of these tasks, each staffed by one agent, run at once (the goal's budget, S04-R-09)."""
    from agent_company.quality import REVIEW_ROLES

    with session() as s:
        rows = list(s.scalars(select(Task).where(Task.id.in_(task_ids))))
        tasks = {t.id: (t.key, list(t.depends_on), t.priority, t.writes,
                        list((t.contract or {}).get("after") or []), t.role in REVIEW_ROLES) for t in rows}
        # a resumed iteration (S04-R-05): tasks that finished before the restart keep their result
        state: dict[int, str] = {t.id: t.status if t.status in {"done", "failed", "skipped"} else "queued"
                                 for t in rows}
    by_key = {v[0]: tid for tid, v in tasks.items()}
    lock = write_lock(worktree)
    slots = global_slots()

    def deps_of(tid: int) -> list[int]:
        return [by_key[k] for k in tasks[tid][1] if k in by_key]

    def afters_of(tid: int) -> list[int]:
        return [by_key[k] for k in tasks[tid][4] if k in by_key]

    fin = {"done", "failed", "skipped"}

    def partial_review(tid: int) -> bool:
        return tasks[tid][5] and bool(deps_of(tid))

    def skip_reason(tid: int) -> str:
        dep_states = [state[d] for d in deps_of(tid)]
        if partial_review(tid):
            if all(x in fin for x in dep_states) and "done" not in dep_states:
                return "skipped: every dependency failed or was skipped"
        elif any(x in {"failed", "skipped"} for x in dep_states):
            return "skipped: a dependency failed"
        return ""

    def is_ready(tid: int) -> bool:
        dep_states = [state[d] for d in deps_of(tid)]
        deps_ok = (all(x in fin for x in dep_states) and "done" in dep_states if partial_review(tid)
                   else all(x == "done" for x in dep_states))
        return deps_ok and all(state[a] in fin for a in afters_of(tid))

    def run_one(tid: int) -> bool:
        with slots:
            if tasks[tid][3]:
                with lock:
                    return _guarded(worker, tid)
            return _guarded(worker, tid)

    running: dict[Future[bool], int] = {}
    cap = max_agents if max_agents > 0 else len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, min(cap, len(tasks)))) as pool:
        while True:
            for tid, st in state.items():
                if st != "queued":
                    continue
                reason = skip_reason(tid)
                if reason:
                    state[tid] = "skipped"
                    set_status(tid, "skipped", output=reason)
            if cancelled():
                for tid, st in state.items():
                    if st == "queued":
                        state[tid] = "skipped"
                        set_status(tid, "skipped", output="skipped: goal cancelled")
            ready = sorted((tid for tid, st in state.items() if st == "queued" and is_ready(tid)),
                           key=lambda t: (tasks[t][2], t))
            for tid in ready[:max(0, cap - len(running))]:
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
