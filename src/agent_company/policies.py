"""Delegated approval rights and versioned governance policies (docs/requirements/SPEC-01-governance.md S01-R-03,
S01-R-05).

delegation  S01-R-03: an administrator delegates a right (`DELEGABLE`: approving or rejecting the goals of one project
            that wait for a merge) to another user, for one project and until an expiry (at most `MAX_DAYS`). Each
            use checks the grant at call time (`allows`), so a revocation or an expiry holds for the next action
            without a background job. Grantees are users by construction: agents have no user row, so they can
            never receive approval rights over their own work; administrators need none, and the platform
            project's merges stay with administrators. A delegate sees the delegated project while the grant holds.
            The API audits the grant, the revocation and every delegated use.
versions    S01-R-05: every change of a company governance setting (`GOVERNANCE_KEYS`, through `prefs.set_value` or
            `prefs.clear`) records a version of all of them (the stored overrides and the values in force) with its
            author; the first change records the state before it as version 1, so it can be restored. Restoring a
            version is a change of its own. A project may add stricter rules (`PROJECT_KEYS`, `Project.policy`, also
            versioned): lower limits, approval required, stricter recruitment approval; one that would weaken the
            company rule is refused. `effective` combines both at the use sites (budgets, the gate, recruitment), so a
            company limit lowered later still wins.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_company.config import settings
from agent_company.db import Delegation, PolicyVersion, Project, Setting, User, log_event, now, session

DELEGABLE = {"approve_goals": "approve or reject the project's goals that wait for a merge"}
MAX_DAYS = 365

GOVERNANCE_KEYS = ("require_approval", "goal_budget_usd", "goal_token_budget", "goal_agent_limit", "goal_time_budget",
                   "budget_exhausted_action", "recruit_requires_approval", "qualify_recruits", "proposal_policy",
                   "max_proposal_goals", "max_goal_depth", "candidate_smoke", "auto_push", "a2a_enabled",
                   "blocked_commands", "sandbox", "sandbox_memory_mb", "sandbox_tasks", "sandbox_cpus", "sandbox_paths",
                   "sandbox_env")
# A project's own rules and what "stricter" means for each: a lower limit (0 = none), approval switched on, a later
# step of the recruitment-approval order.
LIMIT_KEYS = ("goal_budget_usd", "goal_token_budget", "goal_agent_limit")
RECRUIT_ORDER = ("never", "permanent_only", "always")
PROJECT_KEYS = (*LIMIT_KEYS, "require_approval", "recruit_requires_approval", "sandbox")
SANDBOX_ORDER = ("off", "auto", "required")


def _aware(d: datetime) -> datetime:
    from datetime import UTC

    return d if d.tzinfo else d.replace(tzinfo=UTC)


# --- delegation (S01-R-03) ---------------------------------------------------------------------

def _active(q, at: datetime | None = None):  # noqa: ANN001, ANN202
    t = at or now()
    return q.where(Delegation.revoked_at.is_(None), Delegation.expires_at > t)


def delegation_dict(d: Delegation, names: dict[int, str] | None = None) -> dict[str, Any]:
    t = now()
    state = "revoked" if d.revoked_at else "expired" if _aware(d.expires_at) <= t else "active"
    return {"id": d.id, "permission": d.permission, "user_id": d.grantee_user_id,
            "user": (names or {}).get(d.grantee_user_id), "project_id": d.project_id, "granted_by": d.granted_by,
            "note": d.note, "state": state, "expires_at": _aware(d.expires_at).isoformat(),
            "created_at": d.created_at.isoformat(), "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None,
            "revoked_by": d.revoked_by}


def delegate(granted_by: str, user_id: int, permission: str, project_id: int, expires_at: datetime | str,
             note: str = "") -> dict[str, Any]:
    if permission not in DELEGABLE:
        raise ValueError(f"permission is one of {', '.join(DELEGABLE)}")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError("expires_at is an ISO date and time") from e
    expires_at = _aware(expires_at)
    if expires_at <= now():
        raise ValueError("the expiry is in the past")
    if expires_at > now() + timedelta(days=MAX_DAYS):
        raise ValueError(f"a delegation lasts at most {MAX_DAYS} days")
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise ValueError("delegations go to a user (agents never receive approval rights)")
        if u.status != "active":
            raise ValueError(f"{u.name} is {u.status}")
        if u.is_admin:
            raise ValueError(f"{u.name} is an administrator and holds every right already")
        p = s.get(Project, project_id)
        if p is None:
            raise ValueError("unknown project")
        if p.protected:
            raise ValueError("the platform project's merges stay with administrators")
        d = Delegation(permission=permission, grantee_user_id=user_id, project_id=project_id, expires_at=expires_at,
                       granted_by=granted_by, note=(note or "").strip()[:500])
        s.add(d)
        s.flush()
        log_event("delegation", f"{granted_by} delegated {permission} on project #{project_id} to {u.name} until "
                  f"{expires_at.isoformat()}", delegation_id=d.id, s=s)
        return delegation_dict(d, {u.id: u.name})


def revoke(delegation_id: int, actor: str) -> dict[str, Any]:
    with session() as s:
        d = s.get(Delegation, delegation_id)
        if d is None:
            raise KeyError(delegation_id)
        if d.revoked_at is None:
            d.revoked_at, d.revoked_by = now(), actor
            log_event("delegation", f"{actor} revoked delegation #{d.id}", delegation_id=d.id, s=s)
        u = s.get(User, d.grantee_user_id)
        return delegation_dict(d, {u.id: u.name} if u else {})


def list_delegations(user_id: int | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Delegation).order_by(Delegation.id.desc())
        if user_id is not None:
            q = q.where(Delegation.grantee_user_id == user_id)
        if active_only:
            q = _active(q)
        rows = s.scalars(q).all()
        names = dict(s.execute(select(User.id, User.name)).all())
        return [delegation_dict(d, names) for d in rows]


def allows(user: dict[str, Any], permission: str, project_id: int | None) -> bool:
    """May `user` use `permission` on the project now: administrators always, others through a delegation that is
    neither revoked nor expired at this moment."""
    if user.get("is_admin"):
        return True
    if project_id is None or permission not in DELEGABLE:
        return False
    with session() as s:
        q = _active(select(func.count()).select_from(Delegation).where(
            Delegation.grantee_user_id == user["id"], Delegation.permission == permission,
            Delegation.project_id == project_id))
        return bool(s.scalar(q))


def delegated(user_id: int):  # noqa: ANN201
    """The ids of the projects a user holds an active delegation for, as a subquery."""
    return _active(select(Delegation.project_id).where(Delegation.grantee_user_id == user_id))


# --- versioned policies (S01-R-05) -------------------------------------------------------------

def _company_state(s: Session) -> dict[str, Any]:
    stored = {r.key: (r.value or {}).get("v") for r in s.scalars(select(Setting).where(Setting.key.in_(GOVERNANCE_KEYS)))}
    cur = settings()
    return {"overrides": stored, "values": {k: getattr(cur, k) for k in GOVERNANCE_KEYS}}


def _project_state(s: Session, project_id: int) -> dict[str, Any]:
    p = s.get(Project, project_id)
    if p is None:
        raise KeyError(project_id)
    rules = dict(p.policy or {})
    return {"overrides": rules, "values": rules}


def _scope_state(s: Session, scope: str) -> dict[str, Any]:
    if scope == "company":
        return _company_state(s)
    if scope.startswith("project:"):
        return _project_state(s, int(scope.split(":", 1)[1]))
    raise ValueError("scope is 'company' or 'project:<id>'")


def _record(s: Session, scope: str, author: str, note: str) -> PolicyVersion:
    last = s.scalar(select(func.max(PolicyVersion.version)).where(PolicyVersion.scope == scope)) or 0
    v = PolicyVersion(scope=scope, version=last + 1, value=_scope_state(s, scope), author=author, note=note[:500])
    s.add(v)
    s.flush()
    return v


def baseline(scope: str = "company") -> None:
    """Before the first versioned change of a scope: its current state as version 1, so the change can be undone."""
    with session() as s:
        if s.scalar(select(func.count()).select_from(PolicyVersion).where(PolicyVersion.scope == scope)):
            return
        _record(s, scope, "system", "state before the first recorded change")


def record(scope: str, author: str, note: str = "") -> dict[str, Any]:
    with session() as s:
        return version_dict(_record(s, scope, author, note))


def version_dict(v: PolicyVersion) -> dict[str, Any]:
    return {"id": v.id, "scope": v.scope, "version": v.version, "overrides": (v.value or {}).get("overrides", {}),
            "values": (v.value or {}).get("values", {}), "author": v.author, "note": v.note,
            "created_at": v.created_at.isoformat()}


def versions(scope: str = "company", limit: int = 100) -> list[dict[str, Any]]:
    with session() as s:
        _scope_state(s, scope)  # validates the scope
        q = (select(PolicyVersion).where(PolicyVersion.scope == scope).order_by(PolicyVersion.version.desc())
             .limit(max(1, min(limit, 500))))
        return [version_dict(v) for v in s.scalars(q)]


def restore(scope: str, version: int, actor: str) -> dict[str, Any]:
    """Bring a scope back to a recorded version; the restore is recorded as a new version."""
    from agent_company import prefs

    with session() as s:
        v = s.scalars(select(PolicyVersion).where(PolicyVersion.scope == scope, PolicyVersion.version == version)).first()
        if v is None:
            raise KeyError(f"{scope} has no version {version}")
        target = dict((v.value or {}).get("overrides", {}))
    if scope == "company":
        for key in set(GOVERNANCE_KEYS) & set(target):
            prefs._coerce(key, target[key])  # raises for a version written under older limits
        with session() as s:
            for key in GOVERNANCE_KEYS:
                row = s.get(Setting, key)
                if key in target:
                    if row is None:
                        s.add(Setting(key=key, value={"v": target[key]}))
                    else:
                        row.value = {"v": target[key]}
                elif row is not None:
                    s.delete(row)
        prefs.load()
    else:
        pid = int(scope.split(":", 1)[1])
        clean = _check_project_rules(target)  # the company rules may be stricter now than when it was written
        with session() as s:
            s.get(Project, pid).policy = clean
    out = record(scope, actor, f"restored version {version}")
    log_event("policy", f"{actor} restored {scope} version {version}", scope=scope, version=version)
    return out


# --- a project's stricter rules ----------------------------------------------------------------

def _check_project_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Validate a project's rules against the company's; drop the ones that only inherit (0, False, empty)."""
    bad = set(rules) - set(PROJECT_KEYS)
    if bad:
        raise ValueError(f"a project may set {', '.join(PROJECT_KEYS)} (not {', '.join(sorted(bad))})")
    from agent_company import prefs

    cur, out = settings(), {}
    for key, value in rules.items():
        if value in (None, "", 0, False):
            continue
        v = prefs._coerce(key, value)
        company = getattr(cur, key)
        if key in LIMIT_KEYS:
            if v and company and v > company:
                raise ValueError(f"{key} {v:g} is above the company limit of {company:g}: a project may only lower it")
        elif key == "recruit_requires_approval":
            if RECRUIT_ORDER.index(v) < RECRUIT_ORDER.index(company):
                raise ValueError(f"recruitment approval '{v}' is weaker than the company's '{company}'")
            if v == company:
                continue
        elif key == "sandbox":
            if SANDBOX_ORDER.index(v) < SANDBOX_ORDER.index(company):
                raise ValueError(f"sandbox '{v}' is weaker than the company's '{company}'")
            if v == company:
                continue
        elif key == "require_approval" and not v:
            continue
        if v:
            out[key] = v
    return out


def set_project_rules(project_id: int, rules: dict[str, Any], actor: str) -> dict[str, Any]:
    """Replace a project's own rules (administrators; the API audits it). Raises ValueError for a weaker rule."""
    clean = _check_project_rules(rules)
    scope = f"project:{project_id}"
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        before = dict(p.policy or {})
    if clean == before:
        return project_rules(project_id)
    baseline(scope)
    with session() as s:
        s.get(Project, project_id).policy = clean
    record(scope, actor, ", ".join(f"{k} = {v}" for k, v in clean.items()) or "no own rules")
    return project_rules(project_id)


def project_rules(project_id: int) -> dict[str, Any]:
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        own = dict(p.policy or {})
    return {"project_id": project_id, "own": own,
            "effective": {k: effective(k, project_id) for k in PROJECT_KEYS},
            "company": {k: getattr(settings(), k) for k in PROJECT_KEYS}}


def effective(key: str, project_id: int | None, s: Session | None = None) -> Any:
    """The rule in force for a project's work: the company setting, made stricter by the project's own rule."""
    company = getattr(settings(), key)
    if project_id is None or key not in PROJECT_KEYS:
        return company
    if s is not None:
        p = s.get(Project, project_id)
        own = (p.policy or {}).get(key) if p else None
    else:
        with session() as own_s:
            p = own_s.get(Project, project_id)
            own = (p.policy or {}).get(key) if p else None
    if not own:
        return company
    if key in LIMIT_KEYS:
        return min(company, own) if company else own
    if key == "require_approval":
        return True
    if key == "recruit_requires_approval":
        return max(company, own, key=RECRUIT_ORDER.index)
    if key == "sandbox":
        return max(company, own, key=SANDBOX_ORDER.index)
    return company
