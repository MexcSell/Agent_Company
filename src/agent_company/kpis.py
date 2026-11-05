"""Department KPIs (SPEC-01 FR-08, S01-R-06): measurable performance per room, never one score for an agent.

A KPI names its responsible room, a definition, a formula from `METRICS` (which also fixes its data source), a
target with a direction (higher or lower is better) and a measurement interval. `measure` computes the value over
the last interval from the records the formula reads and keeps it in `kpi_values` with the target in force, whether
it was met and the ids of the records it counted, so every value can be inspected and compared over time.
`measure_due` (the `KpiThread`, every few minutes) measures each enabled KPI of an enabled room with at least one
enabled, active agent once per interval (a room whose roles are switched off is not judged); an administrator can
measure any KPI at once. A period with nothing to count has no value (not zero) and is not judged.

Defaults follow the specification's examples for the rooms that exist (development: delivery and defect rate;
research rooms: verified findings; security: incident resolution time; administration: utilization; study room:
demonstrated skill improvement); a removed default stays removed (it is disabled, not deleted).
"""

from __future__ import annotations

import statistics
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company.db import (Agent, Defect, Goal, Incident, Kpi, KpiValue, LearningPlan, Lesson, Room, Run, Task,
                              log_event, now, session)

INTERVALS = {"daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}
DIRECTIONS = ("higher", "lower")
EVIDENCE_CAP = 50  # record ids kept per kind and value


def _naive(t: datetime) -> datetime:  # SQLite hands datetimes back without a zone (stored as UTC)
    return t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _members(s: Session, room: str) -> list[int]:
    return list(s.scalars(select(Agent.id).where(Agent.room_key == room)))


def _ids(xs: Any) -> list[int]:
    return sorted(int(x) for x in xs)[:EVIDENCE_CAP]


Measure = tuple[float | None, int, dict[str, list[int]]]


def _task_success(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    rows = s.execute(select(Task.id, Task.status).where(Task.agent_id.in_(_members(s, room)),
                                                         Task.status.in_(["done", "failed"]),
                                                         Task.finished_at >= a, Task.finished_at < b)).all()
    done = [i for i, st in rows if st == "done"]
    return (len(done) / len(rows) if rows else None), len(rows), {"tasks": _ids(i for i, _ in rows)}


def _worked_goals(s: Session, room: str) -> set[int]:
    return set(s.scalars(select(Task.goal_id).where(Task.agent_id.in_(_members(s, room)))))


def _goal_delivery(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    worked = _worked_goals(s, room)
    rows = s.execute(select(Goal.id, Goal.status).where(Goal.id.in_(worked), Goal.origin != "eval",
                                                         Goal.status.in_(["approved", "rejected", "failed"]),
                                                         Goal.updated_at >= a, Goal.updated_at < b)).all()
    ok = [i for i, st in rows if st == "approved"]
    return (len(ok) / len(rows) if rows else None), len(rows), {"goals": _ids(i for i, _ in rows)}


def _defect_rows(s: Session, room: str, a: datetime, b: datetime) -> tuple[list[tuple[int, int]], set[int]]:
    worked = _worked_goals(s, room)
    defects = s.execute(select(Defect.id, Defect.goal_id).where(Defect.goal_id.in_(worked), Defect.created_at >= a,
                                                                 Defect.created_at < b)).all()
    active = set(s.scalars(select(Task.goal_id).where(Task.agent_id.in_(_members(s, room)), Task.finished_at >= a,
                                                      Task.finished_at < b)))
    return [(i, g) for i, g in defects], active | {g for _, g in defects}


def _defect_rate(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    defects, goals = _defect_rows(s, room, a, b)
    return ((len(defects) / len(goals)) if goals else None), len(goals), {"defects": _ids(i for i, _ in defects),
                                                                          "goals": _ids(goals)}


def _defects_detected(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    defects, goals = _defect_rows(s, room, a, b)
    return (float(len(defects)) if goals else None), len(goals), {"defects": _ids(i for i, _ in defects)}


def _verified_findings(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    ids = list(s.scalars(select(Lesson.id).where(Lesson.agent_id.in_(_members(s, room)), Lesson.kind == "research",
                                                 Lesson.status == "approved", Lesson.created_at >= a,
                                                 Lesson.created_at < b)))
    return float(len(ids)), len(ids), {"knowledge": _ids(ids)}


def _incident_hours(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    rows = s.execute(select(Incident.id, Incident.created_at, Incident.updated_at)
                     .where(Incident.status.in_(["remediated", "closed"]), Incident.updated_at >= a,
                            Incident.updated_at < b)).all()
    hours = [(_naive(u) - _naive(c)).total_seconds() / 3600 for _, c, u in rows]
    return (round(statistics.median(hours), 3) if hours else None), len(rows), {"incidents": _ids(i for i, *_ in rows)}


def _utilization(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    active = set(s.scalars(select(Agent.id).where(Agent.room_key == room, Agent.enabled, Agent.lifecycle == "active")))
    worked = set(s.scalars(select(Run.agent_id).where(Run.agent_id.in_(active), Run.started_at >= a,
                                                      Run.started_at < b)))
    return (len(worked) / len(active) if active else None), len(active), {"agents": _ids(worked)}


def _skill_improvement(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    rows = s.execute(select(LearningPlan.id, LearningPlan.status).where(
        LearningPlan.agent_id.in_(_members(s, room)), LearningPlan.status.in_(["passed", "failed"]),
        LearningPlan.updated_at >= a, LearningPlan.updated_at < b)).all()
    passed = [i for i, st in rows if st == "passed"]
    return float(len(passed)), len(rows), {"learning_plans": _ids(passed)}


def _cost_per_task(s: Session, room: str, a: datetime, b: datetime) -> Measure:
    members = _members(s, room)
    runs = s.execute(select(Run.id, Run.cost).where(Run.agent_id.in_(members), Run.started_at >= a,
                                                    Run.started_at < b)).all()
    tasks = list(s.scalars(select(Task.id).where(Task.agent_id.in_(members), Task.status == "done",
                                                 Task.finished_at >= a, Task.finished_at < b)))
    cost = sum(float(c or 0) for _, c in runs)
    return (round(cost / len(tasks), 6) if tasks else None), len(tasks), {"tasks": _ids(tasks),
                                                                          "runs": _ids(i for i, _ in runs)}


# key -> (label, formula, data source, unit, default direction, function)
METRICS: dict[str, tuple[str, str, str, str, str, Callable[[Session, str, datetime, datetime], Measure]]] = {
    "task_success_rate": ("Task success rate", "finished tasks of the room's agents that succeeded / all of them "
                          "finished in the period", "tasks", "ratio", "higher", _task_success),
    "goal_delivery_rate": ("Delivery rate", "goals the room worked on that were accepted / those decided "
                           "(accepted, rejected, failed) in the period; eval goals left out", "goals, tasks",
                           "ratio", "higher", _goal_delivery),
    "defect_rate": ("Defect rate", "defects detected in the period on goals the room worked on / goals it worked "
                    "on in the period", "defects, tasks", "per goal", "lower", _defect_rate),
    "defects_detected": ("Defect detection", "defects detected in the period on goals the room worked on",
                         "defects, tasks", "count", "higher", _defects_detected),
    "verified_findings": ("Verified research findings", "research knowledge items by the room's agents approved in "
                          "the period (claims that passed the independent review)", "knowledge items", "count",
                          "higher", _verified_findings),
    "incident_resolution_hours": ("Incident resolution time", "median hours from opening to remediation of the "
                                  "security incidents remediated or closed in the period", "incidents", "hours",
                                  "lower", _incident_hours),
    "utilization": ("Workforce utilization", "enabled, active agents of the room with at least one run in the "
                    "period / enabled, active agents of the room", "agents, runs", "ratio", "higher", _utilization),
    "skill_improvement": ("Demonstrated skill improvement", "learning plans of the room's agents passed by their "
                          "hidden final evaluation in the period", "learning plans", "count", "higher",
                          _skill_improvement),
    "cost_per_task": ("Cost per task", "model cost of the room's agents' runs in the period / tasks they finished",
                      "runs, tasks", "USD", "lower", _cost_per_task),
}

# (room, metric, target, interval): the specification's examples, for rooms that exist
DEFAULTS = [("development", "goal_delivery_rate", 0.8, "weekly"), ("development", "defect_rate", 0.5, "weekly"),
            ("information_collection", "verified_findings", 5, "weekly"), ("analysis", "verified_findings", 3, "weekly"),
            ("security", "incident_resolution_hours", 24, "weekly"), ("administration", "utilization", 0.5, "weekly"),
            ("study_room", "skill_improvement", 1, "monthly")]


def seed() -> None:
    """The default KPIs, once per room and metric: a disabled one is not added again."""
    with session() as s:
        rooms = set(s.scalars(select(Room.key)))
        have = set(s.execute(select(Kpi.room_key, Kpi.metric)).all())
        for room, metric, target, interval in DEFAULTS:
            if room in rooms and (room, metric) not in have:
                label, formula, *_ = METRICS[metric]
                s.add(Kpi(room_key=room, name=label, definition=formula, metric=metric, target=float(target),
                          direction=METRICS[metric][4], interval=interval, created_by="system"))


def _value_row(v: KpiValue) -> dict[str, Any]:
    return {"id": v.id, "period_start": v.period_start.isoformat(), "period_end": v.period_end.isoformat(),
            "value": v.value, "samples": v.samples, "target": v.target, "met": v.met, "evidence": v.evidence or {},
            "measured_by": v.measured_by, "measured_at": v.measured_at.isoformat()}


def _row(k: Kpi, values: list[KpiValue] | None = None, rooms: dict[str, str] | None = None) -> dict[str, Any]:
    label, formula, source, unit, *_ = METRICS.get(k.metric, (k.metric, "", "", "", "", None))
    vals = [_value_row(v) for v in values or []]
    latest = vals[0] if vals else None
    prev = next((v for v in vals[1:] if v["value"] is not None), None)
    return {"id": k.id, "room": k.room_key, "room_name": (rooms or {}).get(k.room_key, k.room_key), "name": k.name,
            "definition": k.definition, "metric": k.metric, "formula": formula, "data_source": source, "unit": unit,
            "target": k.target, "direction": k.direction, "interval": k.interval, "enabled": k.enabled,
            "created_by": k.created_by, "last_measured_at": k.last_measured_at.isoformat() if k.last_measured_at
            else None, "latest": latest,
            "change": (round(latest["value"] - prev["value"], 6) if latest and latest["value"] is not None and prev
                       else None),
            "history": vals}


def list_kpis(room: str = "", include_disabled: bool = False, history: int = 12) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Kpi)
        if room:
            q = q.where(Kpi.room_key == room)
        if not include_disabled:
            q = q.where(Kpi.enabled)
        rooms = dict(s.execute(select(Room.key, Room.name)).all())
        out = []
        for k in s.scalars(q.order_by(Kpi.room_key, Kpi.id)):
            vals = list(s.scalars(select(KpiValue).where(KpiValue.kpi_id == k.id)
                                  .order_by(KpiValue.period_end.desc(), KpiValue.id.desc()).limit(history)))
            out.append(_row(k, vals, rooms))
        return out


def get(kpi_id: int, history: int = 100) -> dict[str, Any]:
    with session() as s:
        k = s.get(Kpi, kpi_id)
        if k is None:
            raise KeyError(f"KPI {kpi_id}")
        vals = list(s.scalars(select(KpiValue).where(KpiValue.kpi_id == k.id)
                              .order_by(KpiValue.period_end.desc(), KpiValue.id.desc()).limit(history)))
        return _row(k, vals, dict(s.execute(select(Room.key, Room.name)).all()))


def _check(metric: str, direction: str, interval: str, target: float | None) -> None:
    if metric not in METRICS:
        raise ValueError(f"metric is one of {', '.join(METRICS)}")
    if direction not in DIRECTIONS:
        raise ValueError("direction is higher or lower")
    if interval not in INTERVALS:
        raise ValueError(f"interval is one of {', '.join(INTERVALS)}")
    if target is not None and target < 0:
        raise ValueError("a target cannot be negative")


def create(room: str, metric: str, target: float | None = None, *, name: str = "", definition: str = "",
           direction: str = "", interval: str = "weekly", actor: str = "system") -> dict[str, Any]:
    direction = direction or (METRICS[metric][4] if metric in METRICS else "higher")
    _check(metric, direction, interval, target)
    with session() as s:
        if s.scalars(select(Room).where(Room.key == room)).first() is None:
            raise KeyError(f"room {room}")
        k = Kpi(room_key=room, name=(name or METRICS[metric][0]).strip()[:128],
                definition=(definition or METRICS[metric][1]).strip()[:2000], metric=metric,
                target=None if target is None else float(target), direction=direction, interval=interval,
                created_by=actor[:128])
        s.add(k)
        s.flush()
        log_event("kpi", f"KPI '{k.name}' of {room} defined by {actor}", s=s, kpi_id=k.id)
        kid = k.id
    return get(kid)


def update(kpi_id: int, actor: str, **fields: Any) -> dict[str, Any]:
    """Name, definition, target, direction, interval, enabled. Values already measured keep the target they were
    judged against."""
    allowed = {"name", "definition", "target", "direction", "interval", "enabled"}
    with session() as s:
        k = s.get(Kpi, kpi_id)
        if k is None:
            raise KeyError(f"KPI {kpi_id}")
        change = {f: v for f, v in fields.items() if f in allowed and v is not None}
        if "clear_target" in fields and fields["clear_target"]:
            change["target"] = None
        _check(k.metric, change.get("direction", k.direction), change.get("interval", k.interval),
               change.get("target", k.target))
        before = {f: getattr(k, f) for f in change}
        for f, v in change.items():
            setattr(k, f, str(v).strip()[:2000] if f in ("name", "definition") else v)
        log_event("kpi", f"KPI '{k.name}' changed by {actor}", s=s, kpi_id=k.id, before=before,
                  after={f: getattr(k, f) for f in change})
    return get(kpi_id)


def measure(kpi_id: int, actor: str = "system", until: datetime | None = None) -> dict[str, Any]:
    """The KPI over the interval that ends at `until` (now): its value, samples, target in force, met, evidence."""
    end = _naive(until or now())
    with session() as s:
        k = s.get(Kpi, kpi_id)
        if k is None:
            raise KeyError(f"KPI {kpi_id}")
        start = end - timedelta(seconds=INTERVALS[k.interval])
        value, samples, evidence = METRICS[k.metric][5](s, k.room_key, start, end)
        value = None if value is None else round(float(value), 6)
        met = None if value is None or k.target is None else (
            value >= k.target - 1e-9 if k.direction == "higher" else value <= k.target + 1e-9)
        v = KpiValue(kpi_id=k.id, period_start=start, period_end=end, value=value, samples=samples, target=k.target,
                     met=met, evidence={x: ids for x, ids in evidence.items() if ids}, measured_by=actor[:128])
        s.add(v)
        k.last_measured_at = end
        s.flush()
        if met is False:
            log_event("kpi", f"KPI '{k.name}' of {k.room_key} missed its target: {value:g} vs {k.target:g} "
                             f"({k.direction} is better)", s=s, kpi_id=k.id, value_id=v.id)
        out = _value_row(v)
    return out


def measure_due(at: datetime | None = None) -> int:
    """Measure each enabled KPI of an enabled, staffed room whose interval has passed since its last measurement."""
    t = _naive(at or now())
    with session() as s:
        staffed = set(s.scalars(select(Agent.room_key).where(Agent.enabled, Agent.lifecycle == "active")))
        rooms = set(s.scalars(select(Room.key).where(Room.enabled))) & staffed
        due = [k.id for k in s.scalars(select(Kpi).where(Kpi.enabled))
               if k.room_key in rooms and (k.last_measured_at is None or
                                           _naive(k.last_measured_at) + timedelta(seconds=INTERVALS[k.interval]) <= t)]
    for kid in due:
        measure(kid, "system", at)
    return len(due)


def compare(kpi_id: int) -> dict[str, Any]:
    """Values over time: each measured period with its change from the one before, met counts."""
    k = get(kpi_id)
    vals = [v for v in reversed(k["history"]) if v["value"] is not None]
    series = [{"period_end": v["period_end"], "value": v["value"], "samples": v["samples"], "met": v["met"],
               "change": round(v["value"] - vals[i - 1]["value"], 6) if i else None} for i, v in enumerate(vals)]
    judged = [v for v in vals if v["met"] is not None]
    return {"kpi": {x: k[x] for x in ("id", "room", "name", "metric", "target", "direction", "interval")},
            "series": series, "met": sum(bool(v["met"]) for v in judged), "judged": len(judged)}


class KpiThread(threading.Thread):
    """Measures due KPIs every few minutes."""

    def __init__(self, every: int = 600) -> None:
        super().__init__(daemon=True, name="kpi-measurements")
        self.every = every
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(self.every):
            try:
                measure_due()
            except Exception as e:  # noqa: BLE001 - a broken formula must not stop the measurements
                log_event("error", f"KPI measurement failed: {e}")
