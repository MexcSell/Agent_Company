"""Users: register with a name only, wait for approval, then sign in automatically
from the registered computer.

The browser creates a random 256-bit device secret on first visit and keeps it in
localStorage; the server stores only its SHA-256. Registering binds the account to
that secret. Once an administrator approves the account, the browser signs in by
itself on every visit (`POST /api/auth/device`) with no password. Another computer
or browser profile has another secret and is not signed in. The first account
becomes the administrator and is active immediately.

Computer info (browser, OS, screen, timezone) is stored for the administrator to
check at approval; it is never used to authenticate.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select

from agent_company.db import Event, User, UserSession, now, session
from agent_company.governance import notify

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
SESSION_DAYS = 30
# What a non-administrator may do. Administrators may do everything.
DEFAULT_PERMISSIONS: dict[str, Any] = {
    "projects": "all",         # "all" regular projects, or a list of project ids
    "run": True,               # give orders, start goals, ask the research room
    "create_projects": False,  # create projects (in a new workspace; folders and commands stay admin-only)
    "manage_agents": False,    # create, clone, pause, retire and edit agents; evolution actions
    "manage_models": False,    # add, edit, test and delete models; change routes
}


def permissions_of(u: User) -> dict[str, Any]:
    return {**DEFAULT_PERMISSIONS, **(u.permissions or {})}


def can(user: dict[str, Any], perm: str) -> bool:
    return bool(user.get("is_admin")) or bool(user.get("permissions", {}).get(perm))


def set_permissions(user_id: int, perms: dict[str, Any]) -> dict[str, Any]:
    bad = set(perms) - set(DEFAULT_PERMISSIONS)
    if bad:
        raise AuthError(400, "unknown_permission", f"unknown permission(s): {', '.join(sorted(bad))}")
    projects = perms.get("projects", "all")
    if projects != "all" and not (isinstance(projects, list) and all(isinstance(x, int) for x in projects)):
        raise AuthError(400, "bad_projects", "projects is \"all\" or a list of project ids")
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError(404, "not_found")
        u.permissions = {**permissions_of(u), **{k: (v if k == "projects" else bool(v)) for k, v in perms.items()}}
        s.flush()
        return user_dict(u)


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def user_dict(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name, "status": u.status, "is_admin": u.is_admin,
            "device_bound": u.device_hash is not None, "device_info": u.device_info,
            "created_at": u.created_at.isoformat(),
            "approved_at": u.approved_at.isoformat() if u.approved_at else None,
            "git": {"name": u.git_name or "", "email": u.git_email or "", "branch": u.git_branch or ""},
            "permissions": permissions_of(u)}


def _new_session(s, user_id: int) -> str:  # noqa: ANN001
    token = secrets.token_urlsafe(32)
    s.add(UserSession(token_hash=_hash(token), user_id=user_id,
                      expires_at=now() + timedelta(days=SESSION_DAYS)))
    return token


def register(name: str, device_secret: str, device_info: dict[str, Any] | None = None
             ) -> tuple[dict[str, Any], str | None]:
    """Create (or re-bind) an account. Returns (user, session token or None if pending)."""
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise AuthError(400, "invalid_name", "Names are 3-32 letters, digits, dots, dashes or underscores.")
    if not SECRET_RE.match(device_secret or ""):
        raise AuthError(400, "invalid_device")
    info = device_info or {}
    if len(json.dumps(info)) > 4096:
        raise AuthError(413, "device_info_too_large")
    dh = _hash(device_secret)
    with session() as s:
        by_device = s.scalars(select(User).where(User.device_hash == dh)).first()
        if by_device is not None:
            if by_device.name.lower() != name.lower():
                raise AuthError(409, "device_taken", f"This computer is registered as {by_device.name}.")
            token = _new_session(s, by_device.id) if by_device.status == "active" else None
            return user_dict(by_device), token
        existing = s.scalars(select(User).where(func.lower(User.name) == name.lower())).first()
        if existing is not None:
            if existing.device_hash is not None:
                raise AuthError(409, "name_taken", "That name is registered on another computer.")
            existing.device_hash, existing.device_info, existing.status = dh, info, "pending"
            s.flush()
            s.add(Event(kind="user", message=f"{existing.name} registered a new computer; waiting for approval"))
            notify(f"{existing.name} registered a new computer", str(info.get("label", "")), level="warning",
                   source="users", admin_only=True, s=s)
            return user_dict(existing), None
        first = s.scalar(select(func.count()).select_from(User).where(User.is_admin,
                                                                      User.status == "active")) == 0
        u = User(name=name, device_hash=dh, device_info=info, is_admin=first,
                 status="active" if first else "pending", approved_at=now() if first else None)
        s.add(u)
        s.flush()
        token = _new_session(s, u.id) if first else None
        if not first:
            notify(f"{name} is waiting for approval", str(info.get("label", "")), level="warning",
                   source="users", admin_only=True, s=s)
        s.add(Event(kind="user", message=f"{name} registered"
                    + (" as administrator" if first else "; waiting for approval")))
        return user_dict(u), token


def device_login(device_secret: str) -> tuple[dict[str, Any], str]:
    if not SECRET_RE.match(device_secret or ""):
        raise AuthError(400, "invalid_device")
    with session() as s:
        u = s.scalars(select(User).where(User.device_hash == _hash(device_secret))).first()
        if u is None:
            raise AuthError(401, "unknown_device", "This computer is not registered.")
        if u.status != "active":
            raise AuthError(403, u.status, {"pending": "Waiting for administrator approval.",
                                            "rejected": "Registration was rejected.",
                                            "disabled": "This account is disabled."}.get(u.status, u.status))
        return user_dict(u), _new_session(s, u.id)


def user_for_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with session() as s:
        sess = s.get(UserSession, _hash(token))
        if sess is None:
            return None
        if sess.expires_at.replace(tzinfo=None) < now().replace(tzinfo=None):
            s.delete(sess)
            return None
        u = s.get(User, sess.user_id)
        if u is None or u.status != "active":
            return None
        return user_dict(u)


def logout(token: str | None) -> None:
    if token:
        with session() as s:
            s.execute(delete(UserSession).where(UserSession.token_hash == _hash(token)))


def list_users() -> list[dict[str, Any]]:
    with session() as s:
        return [user_dict(u) for u in s.scalars(select(User).order_by(User.id))]


ACTIONS = {"approve", "reject", "disable", "enable", "reset_device", "make_admin"}


def admin_action(actor_id: int, user_id: int, action: str) -> dict[str, Any]:
    if action not in ACTIONS:
        raise AuthError(400, "unknown_action")
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError(404, "not_found")
        if u.id == actor_id and action in {"reject", "disable", "reset_device"}:
            raise AuthError(400, "self", "You cannot do that to your own account.")
        if u.is_admin and action in {"reject", "disable"}:
            raise AuthError(400, "admin", "Administrators cannot be disabled or rejected.")
        if action == "approve":
            if u.status not in {"pending", "rejected"}:
                raise AuthError(400, "not_pending")
            u.status, u.approved_at = "active", now()
        elif action == "reject":
            u.status = "rejected"
        elif action == "disable":
            u.status = "disabled"
        elif action == "enable":
            if u.status != "disabled":
                raise AuthError(400, "not_disabled")
            u.status = "active"
        elif action == "reset_device":
            u.device_hash, u.device_info, u.status = None, {}, "pending"
        elif action == "make_admin":
            if u.status != "active":
                raise AuthError(400, "not_active")
            u.is_admin = True
        if u.status != "active":
            s.execute(delete(UserSession).where(UserSession.user_id == u.id))
        s.flush()
        s.add(Event(kind="user", message=f"{action} {u.name}"))
        return user_dict(u)


def delete_user(actor_id: int, user_id: int) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError(404, "not_found")
        if u.id == actor_id or u.is_admin:
            raise AuthError(400, "admin", "Administrators cannot be deleted here.")
        from sqlalchemy import update

        from agent_company.db import Goal

        s.execute(update(Goal).where(Goal.created_by == u.id).values(created_by=None))
        s.execute(delete(UserSession).where(UserSession.user_id == u.id))
        s.delete(u)
