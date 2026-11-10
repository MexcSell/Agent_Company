"""Scheduled goals (FW-R-04, after OpenHands automations and the extensions catalog's `cron` triggers).

A project's schedule orders the same goal again and again: `hourly`, `daily HH:MM` or `weekly <day> HH:MM` (UTC).
The `ScheduleThread` creates the goal when it is due (origin `schedule`, on behalf of the administrator who made
the schedule); it then runs like any goal of that project: the gate, and the merge approval. A schedule whose last
goal is still open skips that run and says so, as does one of a project that is not active; a run missed while the
server was down is made once, not once per missed interval.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from agent_company.db import Goal, Project, Schedule, log_event, now, session

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RE = re.compile(r"^(?:(hourly)|daily\s+([01]\d|2[0-3]):([0-5]\d)|weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+([01]\d|2[0-3]):([0-5]\d))$")
OPEN = ("queued", "planning", "running", "recovering", "paused", "awaiting_approval")
KINDS = ("", "bugfix", "feature", "refactor", "research", "docs")


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def parse(interval: str) -> dict[str, Any]:
    """`hourly`, `daily 06:30` or `weekly mon 09:00` (UTC) as {"every", "hour", "minute", "day"}; else ValueError."""
    m = _RE.match(" ".join(str(interval or "").lower().split()))
    if not m:
        raise ValueError("the interval is 'hourly', 'daily HH:MM' or 'weekly <mon..sun> HH:MM' (UTC)")
    if m.group(1):
        return {"every": "hourly"}
    if m.group(2):
        return {"every": "daily", "hour": int(m.group(2)), "minute": int(m.group(3))}
    return {"every": "weekly", "day": DAYS.index(m.group(4)), "hour": int(m.group(5)), "minute": int(m.group(6))}


def normalise(interval: str) -> str:
    spec = parse(interval)
    if spec["every"] == "hourly":
        return "hourly"
    at = f"{spec['hour']:02d}:{spec['minute']:02d}"
    return f"daily {at}" if spec["every"] == "daily" else f"weekly {DAYS[spec['day']]} {at}"


def next_run(interval: str, after: datetime) -> datetime:
    """The first time strictly after `after` the interval names."""
    spec, after = parse(interval), _aware(after)
    if spec["every"] == "hourly":
        return after.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    t = after.replace(hour=spec["hour"], minute=spec["minute"], second=0, microsecond=0)
    if spec["every"] == "daily":
        return t if t > after else t + timedelta(days=1)
    t += timedelta(days=(spec["day"] - t.weekday()) % 7)
    return t if t > after else t + timedelta(days=7)


def _row(s_: Schedule) -> dict[str, Any]:
    return {"id": s_.id, "project_id": s_.project_id, "title": s_.title, "details": s_.details, "checks": s_.checks or [],
            "kind": s_.kind, "interval": s_.interval, "enabled": s_.enabled,
            "next_run_at": _aware(s_.next_run_at).isoformat(), "last_run_at": _aware(s_.last_run_at).isoformat() if s_.last_run_at else None,
            "last_goal_id": s_.last_goal_id, "note": s_.note, "created_by": s_.created_by}


def create(project_id: int, title: str, interval: str, details: str = "", checks: list[str] | None = None,
           kind: str = "", created_by: int | None = None, enabled: bool = True) -> dict[str, Any]:
    from agent_company.governance import check_command

    title = " ".join(str(title or "").split())[:200]
    if not title:
        raise ValueError("a schedule needs a title")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(k or '(planner picks)' for k in KINDS)}")
    checks = [c.strip() for c in checks or [] if c.strip()]
    for cmd in checks:
        if check_command(cmd):
            raise ValueError(f"quality command refused: {cmd}")
    interval = normalise(interval)
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        row = Schedule(project_id=project_id, title=title, details=str(details or "")[:8000], checks=checks, kind=kind,
                       interval=interval, enabled=bool(enabled), next_run_at=next_run(interval, now()),
                       created_by=created_by)
        s.add(row)
        s.flush()
        return _row(row)


def list_schedules(project_id: int | None = None) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Schedule).order_by(Schedule.id)
        if project_id is not None:
            q = q.where(Schedule.project_id == project_id)
        return [_row(x) for x in s.scalars(q)]


def update(schedule_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    bad = set(changes) - {"title", "details", "checks", "kind", "interval", "enabled"}
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    with session() as s:
        row = s.get(Schedule, schedule_id)
        if row is None:
            raise KeyError(schedule_id)
        probe = {"title": row.title, "details": row.details, "checks": row.checks, "kind": row.kind,
                 "interval": row.interval, **changes}
        if "kind" in changes and changes["kind"] not in KINDS:
            raise ValueError("unknown kind")
        if "interval" in changes or ("enabled" in changes and changes["enabled"] and not row.enabled):
            row.interval = normalise(probe["interval"])
            row.next_run_at = next_run(row.interval, now())
        if "title" in changes:
            row.title = " ".join(str(changes["title"]).split())[:200] or row.title
        if "details" in changes:
            row.details = str(changes["details"])[:8000]
        if "checks" in changes:
            from agent_company.governance import check_command

            checks = [c.strip() for c in changes["checks"] or [] if c.strip()]
            if any(check_command(c) for c in checks):
                raise ValueError("a quality command was refused")
            row.checks = checks
        if "kind" in changes:
            row.kind = changes["kind"]
        if "enabled" in changes:
            row.enabled = bool(changes["enabled"])
        return _row(row)


def delete(schedule_id: int) -> None:
    with session() as s:
        row = s.get(Schedule, schedule_id)
        if row is None:
            raise KeyError(schedule_id)
        s.delete(row)


def tick(at: datetime | None = None) -> list[int]:
    """Create the goals of every due schedule; returns their ids. Each due schedule moves to its next run."""
    from agent_company import loop
    from agent_company.projects import post

    at = _aware(at or now())
    made: list[int] = []
    with session() as s:
        due = [x.id for x in s.scalars(select(Schedule).where(Schedule.enabled.is_(True)))
               if _aware(x.next_run_at) <= at]
    for sid in due:
        with session() as s:
            row = s.get(Schedule, sid)
            p = s.get(Project, row.project_id) if row else None
            if row is None or p is None:
                continue
            last = s.get(Goal, row.last_goal_id) if row.last_goal_id else None
            why = ("the project is " + p.status if p.status != "active"
                   else f"goal #{last.id} of the last run is still {last.status}" if last and last.status in OPEN else "")
            spec = {"title": row.title, "details": row.details, "checks": list(row.checks or []) or list(p.quality_commands or []),
                    "kind": row.kind, "project_id": p.id, "path": p.path, "created_by": row.created_by}
            row.last_run_at, row.next_run_at = at, next_run(row.interval, at)
            row.note = f"skipped {at:%Y-%m-%d %H:%M}: {why}" if why else ""
        if why:
            log_event("schedule", f"schedule #{sid} skipped: {why}")
            continue
        try:
            gid = loop.create_goal(spec["title"], spec["details"] or spec["title"], repo_path=spec["path"],
                                   quality_commands=spec["checks"], project_id=spec["project_id"],
                                   created_by=spec["created_by"], origin="schedule", kind=spec["kind"])
        except (ValueError, RuntimeError) as e:
            with session() as s:
                s.get(Schedule, sid).note = f"failed {at:%Y-%m-%d %H:%M}: {e}"[:500]
            log_event("schedule", f"schedule #{sid} could not create its goal: {e}")
            continue
        with session() as s:
            row = s.get(Schedule, sid)
            row.last_goal_id, row.note = gid, f"created goal #{gid} {at:%Y-%m-%d %H:%M}"
        post(spec["project_id"], f"Schedule #{sid} ({spec['title']}) started goal #{gid}.", "system", goal_id=gid)
        log_event("schedule", f"schedule #{sid} created goal #{gid}", goal_id=gid)
        made.append(gid)
    return made


class ScheduleThread(threading.Thread):
    """Looks for due schedules every minute."""

    def __init__(self, every: int = 60) -> None:
        super().__init__(daemon=True, name="schedules")
        self.every = every
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(self.every):
            try:
                tick()
            except Exception as e:  # noqa: BLE001 - one broken schedule must not stop the others
                log_event("error", f"schedules: {type(e).__name__}: {e}")
            try:  # a merge refused only for a dirty or switched checkout is tried again once it is clean
                from agent_company import ownerpolicy

                ownerpolicy.retry_refused()
            except Exception as e:  # noqa: BLE001
                log_event("error", f"merge retry: {type(e).__name__}: {e}")
            try:  # G5 UI-G5-KN-02: Discord sources whose sync interval is due
                from agent_company import discord

                discord.tick()
            except Exception as e:  # noqa: BLE001
                log_event("error", f"discord sync: {type(e).__name__}: {e}")
