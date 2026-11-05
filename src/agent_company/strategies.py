"""Workflow templates and strategy selection (docs/requirements/SPEC-04-orchestration.md S04-R-11, FR-24 and FR-26;
the versioning of §20.4).

kinds       a goal is a `bugfix`, `feature`, `refactor`, `research` or `docs` goal. Each kind has a workflow template
            (an orchestration strategy): the roles its tasks may use, whether tasks may change files, the default
            checks (`inherit`: the goal's or the setting's quality commands; `none`: a goal that changes no files
            runs none), an iteration limit, the execution mode (`parallel`, or `sequential`: one task at a time) and
            the review policy (`independent_qa`: a plan without a QA review gets one).
versions    templates are rows of `strategies`, versioned per kind; `seed` writes version 1 of the built-in ones.
            An administrator saves a new version or restores an old one (a new version again); old ones are kept.
            Every goal records the version it ran under (`Goal.strategy`), so results can be reproduced and compared.
selection   the order may name the kind (`create_goal(kind=...)`): its defaults apply when the goal is created.
            Otherwise the planner names one in its plan (`"kind"`), recorded with source `planner`: then the template
            can only tighten the goal (a lower iteration limit, protected tests, a docs scope when none was given).
            A plan that names none gets a kind inferred from the plan (source `inferred`, recorded only).
enforcement the plan of a goal whose kind was chosen must fit its template (`violations`); a plan that does not goes
            back to the planner once, then the template's own plan is used (`fallback_plan`).
"""

from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_company.db import Goal, Strategy, log_event, session

KINDS = ("feature", "bugfix", "refactor", "research", "docs")
ANY = "*"
CHECKS = ("inherit", "none")
EXECUTION = ("parallel", "sequential")
REVIEW = ("independent_qa", "none")

_READ_ROLES = ["research", "external_data", "analyst", "comprehensive_analyst", "architect", "qa", "security",
               "network_security"]
BUILTIN: dict[str, dict[str, Any]] = {
    "feature": {
        "name": "Feature", "description": "New behaviour: specify, implement with tests, review.",
        "roles": [ANY], "writes": True, "recruit": True, "checks": "inherit", "max_iterations": 3,
        "execution": "parallel", "review": "independent_qa", "protect_tests": False, "allowed_paths": [],
        "instructions": "Specify the behaviour first, implement it with tests, then review it against the spec."},
    "bugfix": {
        "name": "Bug fix", "description": "Reproduce the fault, fix its cause, add a regression test, review.",
        "roles": ["architect", "developer", "frontend", "devops", "qa", "security", "research"], "writes": True,
        "recruit": False, "checks": "inherit", "max_iterations": 3, "execution": "parallel",
        "review": "independent_qa", "protect_tests": False, "allowed_paths": [],
        "instructions": ("Find and state the cause before changing code; fix the cause, not the symptom; add a test "
                         "that fails without the fix. Keep the change as small as the fix allows.")},
    "refactor": {
        "name": "Refactoring", "description": "Change the structure without changing behaviour; existing tests stay.",
        "roles": ["architect", "developer", "frontend", "devops", "qa", "security"], "writes": True,
        "recruit": False, "checks": "inherit", "max_iterations": 3, "execution": "sequential",
        "review": "independent_qa", "protect_tests": True, "allowed_paths": [],
        "instructions": ("Behaviour must not change: existing tests are protected and must keep passing. Change one "
                         "area at a time.")},
    "research": {
        "name": "Research", "description": "Collect sources, analyse them and report findings; no file changes.",
        "roles": list(_READ_ROLES), "writes": False, "recruit": True, "checks": "none", "max_iterations": 2,
        "execution": "parallel", "review": "independent_qa", "protect_tests": False, "allowed_paths": [],
        "instructions": ("Nobody changes files: collect sources, analyse them, answer the question with labelled "
                         "evidence (FACT, INFERENCE, HYPOTHESIS) and a RECOMMENDATION line; QA reviews the report.")},
    "docs": {
        "name": "Documentation", "description": "Write or update documentation only.",
        "roles": ["architect", "developer", "frontend", "product", "research", "qa", "security"], "writes": True,
        "recruit": False, "checks": "inherit", "max_iterations": 2, "execution": "parallel",
        "review": "independent_qa", "protect_tests": True,
        "allowed_paths": ["docs/*", "*.md", "*.mdx", "*.rst", "*.txt"],
        "instructions": "Change documentation files only; check every statement against the code it describes."},
}
FIELDS = set(BUILTIN["feature"])


class StrategyError(ValueError):
    pass


def check_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k not in KINDS:
        raise StrategyError(f"unknown goal kind '{kind}'; one of {', '.join(KINDS)}")
    return k


def validate(body: dict[str, Any]) -> dict[str, Any]:
    """A complete, well-formed template body (missing fields are an error, unknown ones are refused)."""
    if not isinstance(body, dict):
        raise StrategyError("a template is an object")
    extra, missing = set(body) - FIELDS, FIELDS - set(body)
    if extra or missing:
        raise StrategyError("template fields " + "; ".join(
            x for x in (f"unknown: {', '.join(sorted(extra))}" if extra else "",
                        f"missing: {', '.join(sorted(missing))}" if missing else "") if x))
    out = copy.deepcopy(body)
    roles = [re.sub(r"[^a-z0-9_*]+", "_", str(r).strip().lower()) for r in out["roles"] or []]
    if not roles or any(not r for r in roles):
        raise StrategyError("a template names at least one role (or '*' for any)")
    out["roles"] = roles
    for key, allowed in (("checks", CHECKS), ("execution", EXECUTION), ("review", REVIEW)):
        if out[key] not in allowed:
            raise StrategyError(f"{key} must be one of {', '.join(allowed)}")
    for key in ("writes", "recruit", "protect_tests"):
        if not isinstance(out[key], bool):
            raise StrategyError(f"{key} is true or false")
    try:
        out["max_iterations"] = int(out["max_iterations"])
    except (TypeError, ValueError) as e:
        raise StrategyError("max_iterations is a number") from e
    if not 1 <= out["max_iterations"] <= 10:
        raise StrategyError("max_iterations is between 1 and 10")
    out["allowed_paths"] = [str(p).strip() for p in out["allowed_paths"] or [] if str(p).strip()]
    for key in ("name", "description", "instructions"):
        out[key] = str(out[key] or "").strip()[:2000]
    if out["writes"] and out["checks"] == "none":  # changed files always meet the quality commands
        raise StrategyError("a template whose tasks change files keeps the checks (checks: inherit)")
    if out["writes"] is False and any(r == ANY for r in out["roles"]):
        raise StrategyError("a template without file changes names its roles (no '*')")
    return out


# --- versions -----------------------------------------------------------------------------------

def seed() -> None:
    with session() as s:
        have = set(s.scalars(select(Strategy.kind)))
        for kind, body in BUILTIN.items():
            if kind not in have:
                s.add(Strategy(kind=kind, version=1, body=copy.deepcopy(body), author="system", note="built-in"))


def _current(s: Session, kind: str) -> Strategy:
    row = s.scalars(select(Strategy).where(Strategy.kind == kind, Strategy.status == "approved")
                    .order_by(Strategy.version.desc())).first()
    if row is None:  # a database from before the templates, or every version retired: the built-in one
        row = Strategy(kind=kind, version=0, body=copy.deepcopy(BUILTIN[kind]), author="system")
    return row


def effective(kind: str, s: Session | None = None) -> dict[str, Any]:
    """The template in force for the kind, with its version."""
    kind = check_kind(kind)
    if s is not None:
        row = _current(s, kind)
        return {"kind": kind, "version": row.version, **copy.deepcopy(row.body)}
    with session() as own:
        return effective(kind, own)


def _row(r: Strategy) -> dict[str, Any]:
    return {"kind": r.kind, "version": r.version, "status": r.status, "author": r.author, "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None, **copy.deepcopy(r.body)}


def list_strategies() -> list[dict[str, Any]]:
    """Each kind's template in force, with its earlier versions and how many goals ran under each version."""
    with session() as s:
        rows = s.scalars(select(Strategy).order_by(Strategy.kind, Strategy.version.desc())).all()
        used: dict[tuple[str, int], int] = {}
        for kind, snap in s.execute(select(Goal.kind, Goal.strategy).where(Goal.kind != "")):
            v = (snap or {}).get("version")
            used[(kind, v)] = used.get((kind, v), 0) + 1
        out = []
        for kind in KINDS:
            cur = _current(s, kind)
            versions = [{**_row(r), "goals": used.get((kind, r.version), 0)} for r in rows if r.kind == kind]
            out.append({**effective(kind, s), "author": cur.author, "note": cur.note, "versions": versions})
        return out


def save(kind: str, body: dict[str, Any], author: str, note: str = "", s: Session | None = None) -> dict[str, Any]:
    """A new version of the kind's template; the earlier ones stay (§20.4)."""
    kind = check_kind(kind)
    clean = validate(body)
    if s is None:
        with session() as own:
            return save(kind, clean, author, note, own)
    last = s.scalar(select(func.max(Strategy.version)).where(Strategy.kind == kind)) or 0
    row = Strategy(kind=kind, version=last + 1, body=clean, author=author, note=note.strip()[:500])
    s.add(row)
    s.flush()
    log_event("strategy", f"{author} saved {kind} template version {row.version}" + (f": {note}" if note else ""),
              s=s, goal_kind=kind, version=row.version)
    return _row(row)


def restore(kind: str, version: int, author: str, s: Session | None = None) -> dict[str, Any]:
    """Restore an earlier version: saved again as the newest one."""
    kind = check_kind(kind)
    if s is None:
        with session() as own:
            return restore(kind, version, author, own)
    old = s.scalars(select(Strategy).where(Strategy.kind == kind, Strategy.version == version)).first()
    if old is None:
        raise KeyError(f"{kind} v{version}")
    return save(kind, copy.deepcopy(old.body), author, f"restored version {version}", s)


# --- selection ----------------------------------------------------------------------------------

def snapshot(kind: str, source: str, s: Session | None = None) -> dict[str, Any]:
    return {**effective(kind, s), "source": source}


def creation_defaults(kind: str) -> dict[str, Any]:
    """What a kind named by the order sets when the goal is created (fields the order left empty)."""
    t = effective(kind)
    return {"max_iterations": t["max_iterations"], "protect_tests": t["protect_tests"],
            "allowed_paths": list(t["allowed_paths"]), "strategy": {**t, "source": "order"}}


def choose(goal_id: int, kind: str, source: str, reason: str = "") -> dict[str, Any]:
    """Record the planner's (or an inferred) choice for a goal that had none. A planner's choice can only tighten
    the goal; an inferred kind is a label."""
    kind = check_kind(kind)
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            return {}
        if g.kind and ((g.strategy or {}).get("source") != "inferred" or source == "inferred"):
            return dict(g.strategy or {})  # a chosen kind stays; only an inferred label gives way to the planner
        snap = snapshot(kind, source, s)
        g.kind, g.strategy = kind, snap
        if source == "planner":
            g.max_iterations = max(min(g.max_iterations, snap["max_iterations"]), max(1, g.iteration))
            g.protect_tests = bool(g.protect_tests or snap["protect_tests"])
            if not g.allowed_paths and snap["allowed_paths"]:
                g.allowed_paths = list(snap["allowed_paths"])
        log_event("plan", f"workflow template {kind} v{snap['version']} ({source})" + (f": {reason}" if reason else ""),
                  goal_id=goal_id, s=s, goal_kind=kind, version=snap["version"], source=source)
    return snap


def enforced(goal: Goal) -> bool:
    """Whether the goal's plans must fit its template: when the order or the planner chose the kind."""
    return bool(goal.kind) and (goal.strategy or {}).get("source") in {"order", "planner"}


def infer(title: str, plan: list[Any]) -> str:
    """A kind for a plan whose planner named none: from what the plan does, then the title."""
    if plan and not any(c.writes for c in plan):
        return "research"
    text = title.lower()
    for kind, words in (("bugfix", ("fix", "bug", "crash", "error", "regression")),
                        ("refactor", ("refactor", "restructure", "clean up", "cleanup")),
                        ("docs", ("document", "docs", "readme"))):
        if any(w in text for w in words):
            return kind
    return "feature"


def planner_note(goal: Goal) -> str:
    """The template part of the planner's prompt: the chosen one, or the choice."""
    if enforced(goal):
        t = goal.strategy or {}
        roles = "any role" if ANY in t.get("roles", []) else ", ".join(t.get("roles", []))
        return (f"\nWorkflow template: {goal.kind} v{t.get('version')} ({t.get('description', '')})\n"
                f"- Roles: {roles}" + (" (a new specialist role may be named)" if t.get("recruit") else "") + "\n"
                + ("- Tasks may change files.\n" if t.get("writes") else "- No task may change files (writes: false).\n")
                + (f"- Only these paths may change: {', '.join(t['allowed_paths'])}\n" if t.get("allowed_paths") else "")
                + (f"- {t['instructions']}\n" if t.get("instructions") else ""))
    with session() as s:
        lines = [f"- kind \"{k}\": {effective(k, s)['description']}" for k in KINDS]
    return ("\nWorkflow templates (name the one that fits as \"kind\" in your JSON; its rules then apply):\n"
            + "\n".join(lines) + "\n")


def _allowed_role(t: dict[str, Any], role: str) -> bool:
    from agent_company import org, registry

    roles = t.get("roles") or []
    if ANY in roles:
        return True
    resolved = registry.resolve_role(role)[0]
    if resolved in roles or registry._slug(role) in roles:
        return True
    return bool(t.get("recruit")) and resolved not in org.ROLES  # a new specialist, recruited under the usual rules


def violations(t: dict[str, Any], plan: list[Any]) -> list[str]:
    """What in the plan the template does not allow."""
    from agent_company import org, registry

    out = []
    for c in plan:
        role = registry.resolve_role(c.role)[0]
        if not _allowed_role(t, c.role):
            out.append(f"task {c.key}: role {role} is not in the {t.get('kind')} template")
        elif not t.get("writes") and (c.writes or (role in org.ROLES and org.ROLES[role].writes)):
            out.append(f"task {c.key}: the {t.get('kind')} template changes no files ({role}"
                       + (", writes: true)" if c.writes else " is a writing role)"))
    return out


def with_review(t: dict[str, Any], plan: list[Any]) -> list[Any]:
    """`independent_qa`: a plan without a review task gets one after its last tasks."""
    from agent_company import contracts, registry
    from agent_company.quality import REVIEW_ROLES

    if t.get("review") != "independent_qa" or not plan:
        return plan
    if any(registry.resolve_role(c.role)[0] in REVIEW_ROLES for c in plan):
        return plan
    needed = {d for c in plan for d in c.depends_on}
    keys = {c.key for c in plan}
    key = "review" if "review" not in keys else "template-review"
    return [*plan, contracts.TaskContract(
        key=key, title="Independent review", role="qa", depends_on=[c.key for c in plan if c.key not in needed],
        skills=["review"], description=("Review the work of this goal against its objective: verify the logic first "
                                        "(the normal, an edge and an error case), list FINDING lines, end with an "
                                        "OUTCOME line and VERDICT: PASS or VERDICT: FAIL."),
        acceptance=[contracts.Condition(type="regex", pattern="VERDICT: (PASS|FAIL)")])]


def max_agents(goal: Goal, limit: int | None) -> int | None:
    """`sequential` runs one task at a time; otherwise the goal's budget limit (S04-R-09) stands."""
    if (goal.strategy or {}).get("execution") == "sequential" and enforced(goal):
        return 1
    return limit


def skips_checks(goal: Goal) -> bool:
    """A template with `checks: none` (a goal that changes no files) runs no quality commands."""
    return enforced(goal) and (goal.strategy or {}).get("checks") == "none"


def fallback_plan(goal_title: str, kind: str = "") -> dict[str, Any]:
    """The template's own plan: used offline and when the planner's plans do not fit. Writing kinds use the
    standard specification -> implementation -> review plan."""
    from agent_company import executors

    if kind == "research":
        return {"summary": f"Research plan for {goal_title}", "tasks": [
            {"key": "collect", "title": "Collect the sources", "role": "research", "skills": ["research"],
             "description": (f"Collect and read the sources that answer: {goal_title}. List each source (URL or "
                             "file) with what it says and how reliable it is. Do not change files."),
             "acceptance": [{"type": "min_length", "value": 80}]},
            {"key": "analyse", "title": "Analyse the findings", "role": "analyst", "skills": ["data_analysis"],
             "depends_on": ["collect"],
             "description": ("Compare the collected findings and answer the question. Label each statement FACT, "
                             "INFERENCE or HYPOTHESIS, name what the evidence does not show, and end with a "
                             "RECOMMENDATION line. Do not change files."),
             "acceptance": [{"type": "min_length", "value": 80}]},
            {"key": "review", "title": "Review the report", "role": "qa", "skills": ["review"],
             "depends_on": ["analyse"],
             "description": ("Review the report: does every conclusion follow from a cited source? End with an "
                             "OUTCOME line and VERDICT: PASS or VERDICT: FAIL."),
             "acceptance": [{"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}]}]}
    if kind == "docs":
        return {"summary": f"Documentation plan for {goal_title}", "tasks": [
            {"key": "write", "title": "Write the documentation", "role": "developer", "writes": True,
             "skills": ["writing"], "in_scope": list(BUILTIN["docs"]["allowed_paths"]),
             "description": (f"Write or update the documentation for: {goal_title}. Change documentation files only "
                             "and check each statement against the code. Record what you changed in WORKLOG.md."),
             "acceptance": [{"type": "file_exists", "path": "WORKLOG.md"}]},
            {"key": "review", "title": "Review the documentation", "role": "qa", "skills": ["review"],
             "depends_on": ["write"],
             "description": ("Check the documentation against the code: is every statement true? End with an OUTCOME "
                             "line and VERDICT: PASS or VERDICT: FAIL."),
             "acceptance": [{"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}]}]}
    return executors.standard_plan(goal_title)
