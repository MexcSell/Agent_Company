"""Governance: tamper-evident audit log, notifications and the dangerous-command policy.

Audit (ASEAO SPEC01 §10.5, SPEC14): records are append-only, carry actor, action,
target, before/after state, timestamp and a correlation id, and are hash-chained:
each record's hash covers its content and the previous record's hash, so editing
or deleting any record breaks `verify_audit()` from that point on.

Command policy (SPEC14 §8.4, §10.3): commands that would deploy, delete important
data, change access control, escalate privileges or reach production are refused
before they run, and the refusal is audited as a high-severity event.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_company.db import AuditEvent, Notification, session
from agent_company.workflow import ACTIVE, set_status

_audit_lock = threading.Lock()


def _digest(row: dict[str, Any], prev: str) -> str:
    payload = json.dumps({**row, "prev": prev}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def audit(actor: str, action: str, target: str = "", before: dict[str, Any] | None = None,
          after: dict[str, Any] | None = None, severity: str = "info", correlation_id: str = "") -> int:
    """Append an audit record. Uses its own session, so call it outside open write sessions."""
    with _audit_lock, session() as s:
        last = s.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).first()
        prev = last.hash if last else ""
        ev = AuditEvent(actor=actor[:128], action=action[:64], target=str(target)[:256], before=before or {},
                        after=after or {}, severity=severity, correlation_id=correlation_id or uuid.uuid4().hex)
        s.add(ev)
        s.flush()
        ev.prev_hash = prev
        ev.hash = _digest(_audit_row(ev), prev)
        return ev.id


def _audit_row(ev: AuditEvent) -> dict[str, Any]:
    return {"id": ev.id, "actor": ev.actor, "action": ev.action, "target": ev.target, "before": ev.before,
            "after": ev.after, "severity": ev.severity, "correlation_id": ev.correlation_id,
            "created_at": ev.created_at.replace(tzinfo=None).isoformat(timespec="seconds")}


def verify_audit() -> dict[str, Any]:
    """Recompute the chain. Returns {"ok": bool, "records": n, "broken_at": id | None}."""
    with session() as s:
        prev, n = "", 0
        for ev in s.scalars(select(AuditEvent).order_by(AuditEvent.id)):
            n += 1
            if ev.prev_hash != prev or ev.hash != _digest(_audit_row(ev), prev):
                return {"ok": False, "records": n, "broken_at": ev.id}
            prev = ev.hash
        return {"ok": True, "records": n, "broken_at": None}


def list_audit(limit: int = 200, action: str = "") -> list[dict[str, Any]]:
    with session() as s:
        q = select(AuditEvent)
        if action:
            q = q.where(AuditEvent.action == action)
        rows = s.scalars(q.order_by(AuditEvent.id.desc()).limit(limit)).all()
        return [_audit_row(e) | {"hash": e.hash[:12]} for e in rows]


# --- notifications --------------------------------------------------------------

def notify(title: str, body: str = "", level: str = "info", source: str = "system", goal_id: int | None = None,
           project_id: int | None = None, user_id: int | None = None, admin_only: bool = False,
           s: Session | None = None) -> None:
    n = Notification(title=title[:256], body=body[:4000], level=level, source=source, goal_id=goal_id,
                     project_id=project_id, user_id=user_id, admin_only=admin_only)
    if s is not None:
        s.add(n)
        return
    with session() as own:
        own.add(n)


def _visible(q, user: dict[str, Any]):  # noqa: ANN001, ANN202
    q = q.where((Notification.user_id.is_(None)) | (Notification.user_id == user["id"]))
    if not user["is_admin"]:
        q = q.where(Notification.admin_only.is_(False))
    return q


def list_notifications(user: dict[str, Any], limit: int = 100) -> dict[str, Any]:
    with session() as s:
        rows = s.scalars(_visible(select(Notification), user).order_by(Notification.id.desc()).limit(limit)).all()
        items = [{"id": n.id, "level": n.level, "title": n.title, "body": n.body, "source": n.source,
                  "goal_id": n.goal_id, "project_id": n.project_id, "at": n.created_at.isoformat(),
                  "read": user["id"] in (n.read_by or [])} for n in rows]
        return {"items": items, "unread": sum(1 for i in items if not i["read"])}


def unread_count(user: dict[str, Any]) -> int:
    return list_notifications(user, 200)["unread"]


def mark_read(user: dict[str, Any], ids: list[int] | None = None) -> None:
    with session() as s:
        q = _visible(select(Notification), user)
        if ids:
            q = q.where(Notification.id.in_(ids))
        for n in s.scalars(q):
            if user["id"] not in (n.read_by or []):
                n.read_by = [*(n.read_by or []), user["id"]]


def delete_notification(nid: int) -> None:
    with session() as s:
        n = s.get(Notification, nid)
        if n is not None:
            s.delete(n)


def notification_count() -> int:
    with session() as s:
        return s.scalar(select(func.count()).select_from(Notification)) or 0


# --- dangerous-command policy -----------------------------------------------------

DANGEROUS: list[tuple[str, re.Pattern[str]]] = [
    ("privilege escalation", re.compile(r"(^|[;&|]\s*|\s)(sudo|su|doas|pkexec)\s", re.I)),
    ("recursive delete of a root, home or parent path",
     re.compile(r"\brm\s+(-[a-z]*r[a-z]*f?|-[a-z]*f[a-z]*r|--recursive)\b[^;&|]*\s(/|~|\$HOME|\.\.)(\s|/?\*?$|/?\s)", re.I)),
    ("disk or filesystem destruction", re.compile(r"\b(mkfs(\.\w+)?|dd\s+[^;]*of=/dev/|wipefs|shred\s)", re.I)),
    ("deployment or production change",
     re.compile(r"\b(kubectl\s+(apply|delete|rollout|scale)|helm\s+(install|upgrade|uninstall)|terraform\s+(apply|destroy)"
                r"|docker\s+push|fly\s+deploy|vercel\s+(deploy|--prod)|heroku\s|ansible-playbook|serverless\s+deploy"
                r"|npm\s+publish|twine\s+upload|gh\s+release\s+create|git\s+push\b)", re.I)),
    ("database destruction", re.compile(r"\b(drop\s+(database|schema|table)|truncate\s+table|dropdb\s)", re.I)),
    ("access-control or firewall change",
     re.compile(r"\b(chmod\s+(-R\s+)?[0-7]*777\s+/|chown\s+-R\s+\S+\s+/|iptables|ufw\s|firewall-cmd|useradd|usermod"
                r"|passwd\s|visudo|setfacl)", re.I)),
    ("piping a download into a shell", re.compile(r"\b(curl|wget)\b[^|;]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", re.I)),
    ("service or system control", re.compile(r"\b(systemctl\s+(stop|disable|mask|restart)|shutdown|reboot|halt|poweroff"
                                               r"|kill\s+-9\s+1\b|crontab\s+-r)", re.I)),
    ("audit or monitoring tampering", re.compile(r"\b(auditctl|history\s+-c|unset\s+HISTFILE)", re.I)),
]


def check_command(cmd: str) -> str | None:
    """Return why a command is refused, or None if it may run."""
    for reason, pattern in DANGEROUS:
        if pattern.search(cmd):
            return reason
    return None


# --- emergency stop (docs/requirements/SPEC-14-security.md, S14-R-01 level E5) -----------------

EMERGENCY_KEY = "_emergency"


def emergency() -> dict[str, Any] | None:
    """The active emergency stop ({actor, reason, at}) or None."""
    from agent_company.db import Setting

    with session() as s:
        row = s.get(Setting, EMERGENCY_KEY)
        return dict(row.value) if row and (row.value or {}).get("on") else None


def set_emergency(on: bool, actor: str, reason: str = "") -> dict[str, Any]:
    """E5: all autonomous execution stops. Running and queued goals are cancelled, new agent runs are
    refused until an administrator lifts it. The Workspace (the administrator's own tools) is not affected."""
    from agent_company.db import Goal, Setting, now

    cancelled: list[int] = []
    with session() as s:
        row = s.get(Setting, EMERGENCY_KEY)
        value = {"on": on, "actor": actor, "reason": reason[:500], "at": now().isoformat()}
        if row is None:
            s.add(Setting(key=EMERGENCY_KEY, value=value))
        else:
            row.value = value
        if on:
            for g in s.scalars(select(Goal).where(Goal.status.in_(ACTIVE))):
                set_status(s, g, "cancelled", f"E5 emergency stop: {reason}"[:500], actor)
                cancelled.append(g.id)
    killed = _kill(everything=True) if on else []
    audit(actor, "emergency_stop" if on else "emergency_lift", "all autonomous execution",
          after={"reason": reason, "cancelled_goals": cancelled, "terminated": killed}, severity="high")
    notify("Emergency stop" if on else "Emergency stop lifted",
           (f"{actor}: {reason}. {len(cancelled)} goal(s) cancelled." if on else f"Lifted by {actor}."),
           level="error" if on else "success", source="emergency", admin_only=True)
    return {**value, "cancelled_goals": cancelled, "terminated": killed}


def _kill(**which: Any) -> list[int]:
    from agent_company import executors

    try:
        return executors.terminate(**which)
    except Exception:  # noqa: BLE001 - the stop itself must never fail because a process is odd
        return []


# Scoped stops E0-E4 (S14-R-01). E2 (one goal) is loop.cancel, final and not stored; E5 is the key above.
STOPS_KEY = "_stops"
LEVELS = {"E0": "executor", "E1": "agent", "E2": "goal", "E3": "project", "E4": "room", "E5": "company"}
EXECUTOR_KINDS = {"claude_code", "codex", "llm", "research"}


def stops() -> list[dict[str, Any]]:
    """Active scoped stops: [{level, scope, actor, reason, at, paused}]."""
    from agent_company.db import Setting

    with session() as s:
        row = s.get(Setting, STOPS_KEY)
        return list((row.value or {}).get("stops", [])) if row else []


def _save_stops(s: Session, items: list[dict[str, Any]]) -> None:
    from agent_company.db import Setting

    row = s.get(Setting, STOPS_KEY)
    if row is None:
        s.add(Setting(key=STOPS_KEY, value={"stops": items}))
    else:
        row.value = {"stops": items}


def stopped(agent_id: int | None = None, project_id: int | None = None, room: str | None = None,
            executor: str | None = None) -> str | None:
    """Why this work may not run now (an emergency stop that covers it), or None."""
    e5 = emergency()
    if e5:
        return f"E5 emergency stop by {e5.get('actor')}: {e5.get('reason')}"
    for st in stops():
        hit = ((st["level"] == "E0" and executor is not None and st["scope"] == executor)
               or (st["level"] == "E1" and agent_id is not None and str(st["scope"]) == str(agent_id))
               or (st["level"] == "E3" and project_id is not None and str(st["scope"]) == str(project_id))
               or (st["level"] == "E4" and room is not None and st["scope"] == room))
        if hit:
            return f"{st['level']} stop of {LEVELS[st['level']]} {st['scope']} by {st['actor']}: {st['reason']}"
    return None


def stop(level: str, scope: str | int, actor: str, reason: str) -> dict[str, Any]:
    """Trigger a stop. Needs no model or agent: it writes the database and signals processes directly."""
    from agent_company.db import Agent, Goal, now

    if level == "E5":
        return set_emergency(True, actor, reason)
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}; one of {', '.join(LEVELS)}")
    scope = str(scope).strip()
    if level == "E0" and scope not in EXECUTOR_KINDS:
        raise ValueError(f"E0 scope is an executor: {', '.join(sorted(EXECUTOR_KINDS))}")
    killed: list[int] = []
    cancelled: list[int] = []
    paused: list[int] = []
    current = stops()
    with session() as s:
        if level == "E2":
            g = s.get(Goal, int(scope))
            if g is None:
                raise KeyError(f"goal {scope}")
            if g.status in ACTIVE:
                set_status(s, g, "cancelled", f"E2 stop: {reason}"[:500], actor)
                cancelled.append(g.id)
        else:
            if level in {"E1", "E4"}:
                q = select(Agent).where(Agent.id == int(scope)) if level == "E1" else select(Agent).where(
                    Agent.room_key == scope)
                agents = list(s.scalars(q))
                if not agents:
                    raise KeyError(f"{LEVELS[level]} {scope}")
                for a in agents:
                    if a.lifecycle == "active":
                        a.lifecycle = "paused"
                        paused.append(a.id)
            if level == "E3":
                for g in s.scalars(select(Goal).where(Goal.project_id == int(scope), Goal.status.in_(ACTIVE))):
                    set_status(s, g, "cancelled", f"E3 stop: {reason}"[:500], actor)
                    cancelled.append(g.id)
            items = [x for x in current if not (x["level"] == level and str(x["scope"]) == scope)]
            items.append({"level": level, "scope": scope, "actor": actor, "reason": reason[:500],
                          "at": now().isoformat(), "paused": paused})
            _save_stops(s, items)
    if level == "E1":
        killed = _kill(agent_id=int(scope))
    for gid in cancelled:
        killed += _kill(goal_id=gid)
    for aid in paused if level == "E4" else []:
        killed += _kill(agent_id=aid)
    after = {"reason": reason, "cancelled_goals": cancelled, "paused_agents": paused, "terminated": killed}
    audit(actor, "emergency_stop", f"{level} {LEVELS[level]} {scope}", after=after, severity="high")
    notify(f"Stop {level}: {LEVELS[level]} {scope}", f"{actor}: {reason}", level="error", source="emergency",
           admin_only=True)
    return {"level": level, "scope": scope, **after}


def lift(level: str, scope: str | int, actor: str) -> dict[str, Any]:
    """Lift a stop (administrators only, enforced by the API). Agents it paused become active again."""
    from agent_company.db import Agent

    if level == "E5":
        return set_emergency(False, actor)
    scope = str(scope).strip()
    restored: list[int] = []
    items = stops()
    with session() as s:
        hit = [x for x in items if x["level"] == level and str(x["scope"]) == scope]
        if not hit:
            raise KeyError(f"no {level} stop of {scope}")
        _save_stops(s, [x for x in items if x not in hit])
        for aid in hit[0].get("paused", []):
            a = s.get(Agent, aid)
            if a is not None and a.lifecycle == "paused":
                a.lifecycle = "active"
                restored.append(aid)
    audit(actor, "emergency_lift", f"{level} {LEVELS[level]} {scope}", after={"restored_agents": restored},
          severity="high")
    notify(f"Stop {level} lifted: {LEVELS[level]} {scope}", f"Lifted by {actor}.", level="success",
           source="emergency", admin_only=True)
    return {"level": level, "scope": scope, "restored_agents": restored}


# --- security incidents (S14-R-04) and behaviour rules (S14-R-03) --------------------------------

INCIDENT_CATEGORIES = ["unauthorized_access", "credential_compromise", "prompt_injection", "data_leakage",
                       "malicious_code", "privilege_escalation", "agent_compromise", "supply_chain",
                       "audit_manipulation", "unauthorized_deployment", "policy_violation"]
INCIDENT_FLOW = ["open", "contained", "investigating", "remediated", "closed"]
SEVERITIES = ["SEV-1", "SEV-2", "SEV-3", "SEV-4"]


def _incident_row(i: Any) -> dict[str, Any]:
    return {"id": i.id, "category": i.category, "severity": i.severity, "status": i.status, "title": i.title,
            "rule": i.rule, "goal_id": i.goal_id, "agent_id": i.agent_id, "project_id": i.project_id,
            "evidence": i.evidence or [], "root_cause": i.root_cause, "actions": i.actions or [],
            "opened_by": i.opened_by, "at": i.created_at.isoformat(), "updated_at": i.updated_at.isoformat()}


def open_incident(category: str, severity: str, title: str, opened_by: str, rule: str = "",
                  goal_id: int | None = None, agent_id: int | None = None, project_id: int | None = None,
                  evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Open an incident. Evidence references are copied (frozen) at this moment. One open incident per
    rule and goal: a rule that fires again adds its evidence to the open one."""
    from agent_company.db import Event, Goal, Incident

    if category not in INCIDENT_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(INCIDENT_CATEGORIES)}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    with session() as s:
        if goal_id is not None and project_id is None:
            g = s.get(Goal, goal_id)
            project_id = g.project_id if g else None
        refs = list(evidence or [])
        if goal_id is not None:  # the goal's latest events, so the record survives later clean-ups
            refs += [{"type": "event", "id": e.id, "kind": e.kind, "message": e.message[:300]}
                     for e in s.scalars(select(Event).where(Event.goal_id == goal_id).order_by(Event.id.desc()).limit(10))]
        existing = s.scalars(select(Incident).where(Incident.rule == rule, Incident.goal_id == goal_id,
                                                    Incident.status != "closed")).first() if rule else None
        if existing is not None:
            existing.evidence = [*(existing.evidence or []), *refs][-50:]
            return _incident_row(existing) | {"new": False}
        inc = Incident(category=category, severity=severity, title=title[:256], rule=rule, goal_id=goal_id,
                       agent_id=agent_id, project_id=project_id, evidence=refs, opened_by=opened_by[:128])
        s.add(inc)
        s.flush()
        notify(f"Security incident {severity}: {title}"[:256], f"Category {category}. Opened by {opened_by}.",
               level="error", source="security", goal_id=goal_id, project_id=project_id, admin_only=True, s=s)
        out = _incident_row(inc) | {"new": True}
    audit(opened_by, "incident_open", f"incident {out['id']}", after={"category": category, "severity": severity,
                                                                     "rule": rule, "goal_id": goal_id},
          severity="high")
    return out


def list_incidents(status: str = "", limit: int = 200) -> list[dict[str, Any]]:
    from agent_company.db import Incident

    with session() as s:
        q = select(Incident)
        if status:
            q = q.where(Incident.status == status)
        return [_incident_row(i) for i in s.scalars(q.order_by(Incident.id.desc()).limit(limit))]


def update_incident(incident_id: int, actor: str, status: str | None = None, note: str = "",
                    root_cause: str | None = None, contain: str = "") -> dict[str, Any]:
    """Move an incident along open → contained → investigating → remediated → closed (forward only),
    or run a containment action on it: pause_agent, cancel_goal, stop_project, stop_all."""
    from agent_company.db import Incident, now

    with session() as s:
        inc = s.get(Incident, incident_id)
        if inc is None:
            raise KeyError(f"incident {incident_id}")
        goal_id, agent_id, project_id, cur = inc.goal_id, inc.agent_id, inc.project_id, inc.status
    if contain:
        target = {"pause_agent": ("E1", agent_id), "cancel_goal": ("E2", goal_id),
                  "stop_project": ("E3", project_id), "stop_all": ("E5", "all")}.get(contain)
        if target is None:
            raise ValueError("containment is pause_agent, cancel_goal, stop_project or stop_all")
        if target[1] is None:
            raise ValueError(f"incident {incident_id} names no {contain.split('_')[1]}")
        stop(target[0], target[1], actor, f"containment of incident {incident_id}" + (f": {note}" if note else ""))
    with session() as s:
        inc = s.get(Incident, incident_id)
        if root_cause is not None:
            inc.root_cause = root_cause.strip()[:4000]
        if status and status != cur:
            if status not in INCIDENT_FLOW or INCIDENT_FLOW.index(status) < INCIDENT_FLOW.index(cur):
                raise ValueError(f"an incident moves forward only: {' → '.join(INCIDENT_FLOW)}")
            if status == "closed" and (not inc.root_cause.strip() or not note.strip()):
                raise ValueError("closing needs a root cause and a remediation note")
            inc.status = status
        if contain and inc.status == "open":
            inc.status = "contained"
        inc.actions = [*(inc.actions or []), {"action": contain or f"status {inc.status}", "actor": actor,
                                              "at": now().isoformat(), "note": note[:1000]}]
        out = _incident_row(inc)
    if out["status"] == "closed" and cur != "closed":
        _incident_lesson(out)
    audit(actor, "incident_update", f"incident {incident_id}", before={"status": cur},
          after={"status": out["status"], "contain": contain}, severity="high")
    return out


def _incident_lesson(inc: dict[str, Any]) -> None:
    """A closed incident becomes a lesson for the agent it concerned (SPEC-11)."""
    from agent_company.db import Lesson

    if inc["agent_id"] is None:
        return
    with session() as s:
        s.add(Lesson(agent_id=inc["agent_id"], goal_id=inc["goal_id"], outcome="failure",
                     text=f"Security incident ({inc['category']}): {inc['root_cause'][:300]}. "
                          f"Remediation: {inc['actions'][-1]['note'][:300]}"))


def security_alert(title: str, body: str = "", goal_id: int | None = None, agent_id: int | None = None,
                   rule: str = "") -> None:
    """A behaviour-rule alert: an administrator notification of source `security` and an events row."""
    from agent_company.db import log_event

    with session() as s:
        notify(title, body, level="warning", source="security", goal_id=goal_id, admin_only=True, s=s)
        log_event("security", title, goal_id=goal_id, agent_id=agent_id, s=s, rule=rule)


REFUSALS_BEFORE_PAUSE = 3


def on_command_refused(goal_id: int | None, agent_id: int | None, cmd: str, reason: str) -> None:
    """Rule: a goal whose commands are refused by the policy 3 times pauses the responsible agent (E1)
    and raises one alert."""
    from agent_company.db import Event, log_event

    if goal_id is None:
        return
    log_event("command_refused", f"refused ({reason}): {cmd[:200]}", goal_id=goal_id, agent_id=agent_id)
    with session() as s:
        n = s.scalar(select(func.count()).select_from(Event).where(Event.goal_id == goal_id,
                                                                    Event.kind == "command_refused")) or 0
    if n != REFUSALS_BEFORE_PAUSE:
        return
    if agent_id is not None and not any(x["level"] == "E1" and str(x["scope"]) == str(agent_id) for x in stops()):
        stop("E1", agent_id, "rule:refused_commands", f"{n} commands refused by the policy in goal #{goal_id}")
    security_alert(f"Goal #{goal_id}: {n} commands refused by the policy",
                   f"Last: {cmd[:300]} ({reason}). The agent was paused.", goal_id, agent_id, "refused_commands")
