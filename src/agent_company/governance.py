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
            for g in s.scalars(select(Goal).where(Goal.status.in_(["queued", "running"]))):
                g.status = "cancelled"
                cancelled.append(g.id)
    audit(actor, "emergency_stop" if on else "emergency_lift", "all autonomous execution",
          after={"reason": reason, "cancelled_goals": cancelled}, severity="high")
    notify("Emergency stop" if on else "Emergency stop lifted",
           (f"{actor}: {reason}. {len(cancelled)} goal(s) cancelled." if on else f"Lifted by {actor}."),
           level="error" if on else "success", source="emergency")
    return {**value, "cancelled_goals": cancelled}
