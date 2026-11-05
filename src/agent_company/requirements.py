"""Requirements and traceability (docs/requirements/ASEAO08-engineering.md S08-R-01, AT-002, AT-003, AT-031).

A requirement belongs to a project: a key (`REQ-001`, or a document's own ID such as `S07-R-01`), its text, kind
(`functional|non_functional|story|use_case`), acceptance criteria, an optional subject, a version and a source. A
change of text, kind or acceptance is a new version; the earlier one goes to `history`, and an approved requirement
goes back to `draft`, because the new version was never approved, built or tested.

Status: `draft -> approved -> implemented -> verified`, `retired` from any (and back to draft). People approve
(S08-FR-10); `verified` needs a passing check on the current version. The goal loop moves requirements along:

- goals and task contracts carry `requirement_keys` (a goal also picks up the project's keys named in its title or
  description); after each gate `on_gate` links every task to the requirements it names (passed or not) and every
  quality command to all of the iteration's requirements;
- the merge (`on_merged`) records the commit, then an `approved` requirement becomes `implemented`, and
  `implemented` (or approved) becomes `verified` once a check that passed on its current version is linked. The gate
  must pass before a merge, yet the two can still differ: a requirement named only on the goal with no quality
  command, or one whose text changed after the work. A `draft` requirement is linked but not moved: the
  traceability row then shows merged work on an unapproved requirement.

`traceability(project_id)` answers which requirements are implemented, tested and merged (current version only).

Templates (S08-FR-03): a `story` reads "As a <actor>, I want <want>[, so that <benefit>]"; a `use_case` names its
actors, preconditions, a numbered main flow, alternatives and postconditions in labelled lines (`USE_CASE_TEMPLATE`).
Both are parsed from the text (or given as `details`, which win) into `Requirement.details`, refused when the actor
or the want / the actors or the main flow are missing, versioned with the text and shown to the planner. `actors`
lists who the project's stories and use cases serve.

Conflicts (S08-FR-09): the planner is asked to flag contradicting requirements as a decision; as a backstop, a new
or changed requirement whose subject matches another one's (the same explicit subject, or nearly the same words)
while exactly one of them is negated ("must not", "never", ...) records one decision per pair that waits for a
person. It has no goal, so it does not hold a merge; it notifies the administrators.

`import_docs` loads the requirement IDs of a project's `docs/requirements/*.md` (tables and `### ID` headings),
the first content of the platform project itself (status words: done -> implemented, partial and open -> approved,
gated -> draft, skip -> retired).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from agent_company.db import Goal, Project, Requirement, RequirementLink, Task, log_event, now, session

KINDS = ["functional", "non_functional", "story", "use_case"]
STATUSES = ["draft", "approved", "implemented", "verified", "retired"]
LIVE = ("draft", "approved", "implemented", "verified")
_NEG = re.compile(r"\b(must not|mustn't|shall not|should not|may not|cannot|can't|never|no longer|not allowed|"
                  r"forbid\w*|prohibit\w*|disallow\w*|without)\b|^\s*no\b", re.I)
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "be", "is", "are", "it", "its", "with", "by",
         "must", "shall", "should", "may", "can", "will", "not", "never", "no", "longer", "allowed", "cannot",
         "without", "always", "every", "each", "all", "any", "that", "this", "only", "from", "as", "at"}
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+$")


def _row(r: Requirement) -> dict[str, Any]:
    return {"id": r.id, "project_id": r.project_id, "key": r.key, "text": r.text, "kind": r.kind,
            "acceptance": r.acceptance or [], "details": r.details or {}, "subject": r.subject or "",
            "version": r.version,
            "history": r.history or [], "status": r.status, "source": r.source or "", "conflicts": r.conflicts or [],
            "created_by": r.created_by or "", "note": r.note or "", "at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def _texts(items: Any) -> list[str]:
    items = [items] if isinstance(items, str) else list(items or [])
    return [str(x).strip()[:1000] for x in items[:20] if str(x).strip()]


# --- story and use-case templates (S08-FR-03) ---

STORY_TEMPLATE = "As a <actor>, I want <what>, so that <benefit>."
USE_CASE_TEMPLATE = ("<summary line>\nActors: <who>, <who>\nPreconditions: <what holds before>\nMain flow:\n"
                     "1. <step>\n2. <step>\nAlternatives:\n- <deviation and what happens>\n"
                     "Postconditions: <what holds after>")
_STORY = re.compile(r"^\s*as\s+(?:an?|the)?\s*(?P<actor>[^,]+?)\s*,?\s+i\s+(?:want|need|would like|can)\s+(?:to\s+)?"
                    r"(?P<want>.+?)(?:\s*,?\s+so\s+that\s+(?P<benefit>.+?))?\s*\.?\s*$", re.I | re.S)
_UC_LABELS = {"actor": "actors", "actors": "actors", "precondition": "preconditions", "preconditions": "preconditions",
              "main flow": "main_flow", "main success scenario": "main_flow", "basic flow": "main_flow",
              "flow": "main_flow", "steps": "main_flow", "alternative": "alternatives", "alternatives": "alternatives",
              "alternative flows": "alternatives", "extensions": "alternatives", "exceptions": "alternatives",
              "postcondition": "postconditions", "postconditions": "postconditions"}
_UC_LISTS = ("actors", "preconditions", "main_flow", "alternatives", "postconditions")
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[a-z][.)])\s*")


def _split_actors(text: str) -> list[str]:
    return [x.strip(" .") for x in re.split(r",|;|\band\b", text) if x.strip(" .")]


def parse_story(text: str) -> dict[str, Any]:
    m = _STORY.match(text.strip().split("\n")[0])
    if not m:
        return {}
    return {"actors": [m["actor"].strip()], "want": m["want"].strip(), "benefit": (m["benefit"] or "").strip()}


def parse_use_case(text: str) -> dict[str, Any]:
    """Labelled lines (`Actors:`, `Preconditions:`, `Main flow:` …); a label's items follow on its line or on the
    lines below it (numbered or bulleted)."""
    out: dict[str, list[str]] = {}
    cur = ""
    for line in text.splitlines():
        label, colon, rest = line.partition(":")
        key = _UC_LABELS.get(label.strip().lower()) if colon else None
        if key:
            cur = key
            out.setdefault(cur, [])
            rest = rest.strip()
            if rest:
                out[cur] += _split_actors(rest) if cur == "actors" else [rest]
        elif cur and line.strip():
            item = _ITEM.sub("", line).strip()
            if item:
                out[cur] += _split_actors(item) if cur == "actors" else [item]
    return out


def template(kind: str, text: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """The kind's template filled from the text, then from explicit `details`; {} for functional and
    non-functional requirements. A story without actor or want, a use case without actors or main flow is refused
    with the template to follow."""
    if kind not in ("story", "use_case"):
        return {}
    given = {k: v for k, v in dict(details or {}).items() if v not in (None, "", [])}
    if kind == "story":
        d = parse_story(text) | given
        d["actors"] = _texts(d.get("actors") or ([d["actor"]] if d.get("actor") else []))[:5]
        d.pop("actor", None)
        d = {"actors": d["actors"], "want": str(d.get("want") or "").strip()[:1000],
             "benefit": str(d.get("benefit") or "").strip()[:1000]}
        if not d["actors"] or not d["want"]:
            raise ValueError(f"a story names who wants what: \"{STORY_TEMPLATE}\"")
        return d
    d = parse_use_case(text) | given
    d = {k: _texts(d.get(k))[:20] for k in _UC_LISTS}
    if not d["actors"] or not d["main_flow"]:
        raise ValueError("a use case names its actors and its main flow:\n" + USE_CASE_TEMPLATE)
    return d


def describe(kind: str, details: dict[str, Any] | None) -> str:
    """The template in one line for prompts."""
    d = details or {}
    if kind == "story" and d.get("actors"):
        return (f"actor: {', '.join(d['actors'])} | wants: {d.get('want', '')}"
                + (f" | so that: {d['benefit']}" if d.get("benefit") else ""))
    if kind == "use_case" and d.get("actors"):
        parts = [f"Actors: {', '.join(d['actors'])}"]
        if d.get("preconditions"):
            parts.append(f"Preconditions: {'; '.join(d['preconditions'])}")
        parts.append("Main flow: " + " ".join(f"{i}. {x}" for i, x in enumerate(d.get("main_flow") or [], 1)))
        if d.get("alternatives"):
            parts.append(f"Alternatives: {'; '.join(d['alternatives'])}")
        if d.get("postconditions"):
            parts.append(f"Postconditions: {'; '.join(d['postconditions'])}")
        return " | ".join(parts)
    return ""


def actors(project_id: int) -> list[dict[str, Any]]:
    """Who the project's live stories and use cases serve, with the requirement keys of each (case-insensitive)."""
    with session() as s:
        rows = list(s.scalars(select(Requirement).where(Requirement.project_id == project_id,
                                                        Requirement.kind.in_(["story", "use_case"]),
                                                        Requirement.status.in_(LIVE)).order_by(Requirement.key)))
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        for a in (r.details or {}).get("actors") or []:
            row = out.setdefault(a.lower(), {"actor": a, "keys": []})
            row["keys"].append(r.key)
    return sorted(out.values(), key=lambda x: (-len(x["keys"]), x["actor"].lower()))


def _next_key(s: Any, project_id: int) -> str:
    keys = s.scalars(select(Requirement.key).where(Requirement.project_id == project_id,
                                                   Requirement.key.like("REQ-%")))
    top = max((int(k[4:]) for k in keys if k[4:].isdigit()), default=0)
    return f"REQ-{top + 1:03d}"


def create(project_id: int, text: str, kind: str = "functional", acceptance: list[Any] | None = None, *,
           key: str = "", subject: str = "", source: str = "", actor: str = "", status: str = "draft",
           check_conflicts: bool = True, details: dict[str, Any] | None = None) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("a requirement needs a text")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    filled = template(kind, text, details)
    if status not in STATUSES or status == "verified":
        raise ValueError("a new requirement is draft, approved, implemented or retired")
    key = str(key or "").strip().upper()
    if key and not KEY_RE.match(key):
        raise ValueError(f"bad requirement key '{key}' (like REQ-001 or S08-R-01)")
    with session() as s:
        if s.get(Project, project_id) is None:
            raise KeyError(f"project {project_id}")
        key = key or _next_key(s, project_id)
        if s.scalars(select(Requirement).where(Requirement.project_id == project_id, Requirement.key == key)).first():
            raise ValueError(f"requirement {key} already exists in this project")
        r = Requirement(project_id=project_id, key=key[:32], text=text[:8000], kind=kind,
                        acceptance=_texts(acceptance), details=filled, subject=str(subject or "").strip()[:128], status=status,
                        source=str(source or "")[:256], created_by=str(actor or "")[:128])
        s.add(r)
        s.flush()
        log_event("requirement", f"requirement {r.key} added to project {project_id}: {text[:200]}", s=s,
                  requirement_id=r.id, project_id=project_id)
        rid = r.id
    if check_conflicts:
        _flag_conflicts(rid)
    return get(rid)


def get(requirement_id: int, links: bool = False) -> dict[str, Any]:
    with session() as s:
        r = s.get(Requirement, requirement_id)
        if r is None:
            raise KeyError(f"requirement {requirement_id}")
        out = _row(r)
        if links:
            out["links"] = [_link_row(x) for x in s.scalars(select(RequirementLink).where(
                RequirementLink.requirement_id == r.id).order_by(RequirementLink.id))]
        return out


def list_requirements(project_id: int, status: str = "") -> list[dict[str, Any]]:
    with session() as s:
        q = select(Requirement).where(Requirement.project_id == project_id)
        if status:
            q = q.where(Requirement.status.in_([x for x in status.split(",") if x]))
        return [_row(r) for r in s.scalars(q.order_by(Requirement.key))]


def update(requirement_id: int, actor: str, text: str | None = None, kind: str | None = None,
           acceptance: list[Any] | None = None, subject: str | None = None, source: str | None = None,
           note: str = "", check_conflicts: bool = True, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """A change of text, kind, acceptance or template details is a new version (the old one is kept); subject and
    source are not."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    with session() as s:
        r = s.get(Requirement, requirement_id)
        if r is None:
            raise KeyError(f"requirement {requirement_id}")
        if r.status == "retired":
            raise ValueError("a retired requirement is not changed; set it back to draft first")
        new = {"text": str(text).strip()[:8000] if text is not None else r.text, "kind": kind or r.kind,
               "acceptance": _texts(acceptance) if acceptance is not None else list(r.acceptance or [])}
        if not new["text"]:
            raise ValueError("a requirement needs a text")
        old = (r.text, r.kind, list(r.acceptance or []), dict(r.details or {}))
        if (new["text"], new["kind"]) != old[:2]:  # only a changed story is checked again
            new["details"] = template(new["kind"], new["text"], details)
        elif details is not None:  # the same text: the stored details stand where the change names nothing
            new["details"] = template(new["kind"], new["text"], {**old[3], **details})
        else:
            new["details"] = old[3]
        versioned = (new["text"], new["kind"], new["acceptance"], new["details"]) != old
        if versioned:
            r.history = [*(r.history or []), {"version": r.version, "text": r.text, "kind": r.kind,
                                              "acceptance": list(r.acceptance or []), "details": old[3],
                                              "status": r.status, "by": actor[:128], "until": now().isoformat()}]
            r.version += 1
            r.text, r.kind, r.acceptance, r.details = new["text"], new["kind"], new["acceptance"], new["details"]
            if r.status in ("approved", "implemented", "verified"):
                r.status = "draft"  # the new version was never approved, built or tested
        if subject is not None:
            r.subject = str(subject).strip()[:128]
        if source is not None:
            r.source = str(source)[:256]
        if note:
            r.note = str(note)[:2000]
        log_event("requirement", f"requirement {r.key} {'is now version ' + str(r.version) if versioned else 'edited'}"
                  f" by {actor}", s=s, requirement_id=r.id, project_id=r.project_id)
        rid, changed = r.id, versioned or subject is not None
    if changed and check_conflicts:
        _flag_conflicts(rid)
    return get(rid)


def set_status(requirement_id: int, status: str, actor: str, note: str = "") -> dict[str, Any]:
    """People approve requirements; an agent (`agent:*`, `system`) cannot. `verified` needs a passing check on the
    current version."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    with session() as s:
        r = s.get(Requirement, requirement_id)
        if r is None:
            raise KeyError(f"requirement {requirement_id}")
        if r.status == status:
            return _row(r)
        if r.status == "retired" and status != "draft":
            raise ValueError("a retired requirement can only go back to draft")
        if status == "approved" and (actor.startswith("agent:") or actor == "system"):
            raise PermissionError("only a person approves a requirement")
        if status == "verified" and not _tested(s, r):
            raise ValueError(f"no check passed on version {r.version} of {r.key} yet")
        before, r.status = r.status, status
        if note:
            r.note = str(note)[:2000]
        log_event("requirement", f"requirement {r.key}: {before} -> {status} by {actor}", s=s, requirement_id=r.id,
                  project_id=r.project_id)
        return _row(r)


# --- conflicts ---------------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w not in _STOP and len(w) > 1}


def contradicts(a: Requirement, b: Requirement) -> bool:
    """Same subject, opposite statements: exactly one is negated and both name the same subject (explicitly, or
    with at least 70% of their words in common)."""
    if bool(_NEG.search(a.text)) == bool(_NEG.search(b.text)):
        return False
    if a.subject and b.subject:
        return a.subject.casefold() == b.subject.casefold()
    wa, wb = _words(a.text), _words(b.text)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.7


def _flag_conflicts(requirement_id: int) -> list[dict[str, Any]]:
    """One decision per contradicting pair, waiting for a person (recorded once per pair)."""
    from agent_company import decisions

    with session() as s:
        r = s.get(Requirement, requirement_id)
        if r is None or r.status == "retired":
            return []
        known = {c.get("key") for c in r.conflicts or []}
        pairs = [(o.id, o.key, o.text, o.version) for o in s.scalars(select(Requirement).where(
            Requirement.project_id == r.project_id, Requirement.id != r.id, Requirement.status.in_(LIVE)))
            if o.key not in known and contradicts(r, o)]
        mine = (r.id, r.key, r.text, r.version, r.project_id)
    out = []
    for oid, okey, otext, over in pairs:
        d = decisions.record(
            None, f"Conflicting requirements {mine[1]} and {okey}"[:256],
            "A person decides which requirement stands, or rewords one of them so both can hold.",
            [f"{mine[1]}: {mine[2][:500]}", f"{okey}: {otext[:500]}"],
            "Both name the same subject and exactly one of them forbids it.",
            [{"ref": f"requirement {mine[1]} v{mine[3]}", "class": "FACT"},
             {"ref": f"requirement {okey} v{over}", "class": "FACT"}],
            confidence=0.5, category="technical", requires_human_approval=True, project_id=mine[4],
            question=f"{mine[1]} and {okey} seem to contradict each other: which one holds?",
            criteria=["the project's goals", "where each requirement came from"],
            limitations=["found by matching words and negations; it may be a false alarm"],
            confidence_note="a word-overlap heuristic, not a reading of the meaning")
        with session() as s:
            for rid, other in ((mine[0], okey), (oid, mine[1])):
                x = s.get(Requirement, rid)
                x.conflicts = [*(x.conflicts or []), {"key": other, "decision_id": d["id"]}]
                s.add(RequirementLink(requirement_id=rid, version=x.version, kind="decision",
                                      ref=f"decision:{d['id']}", detail=f"conflicts with {other}"))
        out.append(d)
    return out


# --- the goal loop -----------------------------------------------------------------------------

def keys_in(project_id: int | None, text: str) -> list[str]:
    """The project's requirement keys named in a text (a goal's title or description)."""
    if project_id is None or not text:
        return []
    with session() as s:
        keys = list(s.scalars(select(Requirement.key).where(Requirement.project_id == project_id,
                                                            Requirement.status != "retired")))
    return sorted(k for k in keys if re.search(rf"(?<![A-Z0-9-]){re.escape(k)}(?![0-9])", text, re.I))


def planner_note(goal: Goal, limit: int = 8) -> str:
    """For the planner: the goal's own requirements in full and a few related approved ones (never the whole list:
    the platform project holds hundreds)."""
    if goal.project_id is None:
        return ""
    with session() as s:
        rows = list(s.scalars(select(Requirement).where(Requirement.project_id == goal.project_id,
                                                        Requirement.status.in_(LIVE))))
    if not rows:
        return ""
    own = set(goal.requirement_keys or [])
    words = _words(f"{goal.title} {goal.description}")
    related = sorted((r for r in rows if r.key not in own and r.status != "draft"),
                     key=lambda r: -len(words & _words(r.text)))
    related = [r for r in related if words & _words(r.text)][:limit]
    lines = [f"- {r.key} [{r.status}, v{r.version}"
             + (f", {r.kind.replace('_', ' ')}" if r.kind in ("story", "use_case") else "") + f"]: {r.text[:600]}"
             + (f" Template: {describe(r.kind, r.details)[:800]}" if describe(r.kind, r.details) else "")
             + (f" Acceptance: {'; '.join(r.acceptance)}" if r.acceptance else "")
             for r in rows if r.key in own] + [f"- {r.key} (related): {r.text[:300]}" for r in related]
    if not lines:
        return ""
    return ("\nRequirements (name the ones a task implements in its \"requirement_keys\"; if two of them "
            "contradict each other, add a decision that needs a person instead of choosing silently):\n"
            + "\n".join(lines) + "\n")


def _link(s: Any, r: Requirement, kind: str, ref: str, goal_id: int, iteration: int, passed: bool,
          detail: str = "") -> None:
    exists = s.scalars(select(RequirementLink.id).where(
        RequirementLink.requirement_id == r.id, RequirementLink.kind == kind, RequirementLink.ref == ref[:512],
        RequirementLink.goal_id == goal_id, RequirementLink.iteration == iteration,
        RequirementLink.version == r.version)).first()
    if exists is None:
        s.add(RequirementLink(requirement_id=r.id, version=r.version, kind=kind, ref=ref[:512], goal_id=goal_id,
                              iteration=iteration, passed=passed, detail=detail[:2000]))


def _by_key(s: Any, project_id: int, keys: set[str]) -> dict[str, Requirement]:
    if not keys:
        return {}
    return {r.key: r for r in s.scalars(select(Requirement).where(
        Requirement.project_id == project_id, Requirement.key.in_(sorted(keys)), Requirement.status != "retired"))}


def on_gate(goal_id: int, iteration: int, report: dict[str, Any]) -> None:
    """After the gate: which tasks worked on which requirement and which checks tested it."""
    checks = {c["check"]: c for c in report.get("checks", [])}
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None or g.project_id is None:
            return
        tasks = list(s.scalars(select(Task).where(Task.goal_id == goal_id, Task.iteration == iteration)))
        per_task = {t.id: {str(k).upper() for k in (t.contract or {}).get("requirement_keys") or []} for t in tasks}
        goal_keys = {str(k).upper() for k in g.requirement_keys or []}
        # S09-R-06: a quality command or a task's acceptance command may name the requirements it tests
        named = [(name[8:], {str(k).upper() for k in c["requirement_keys"]}, c) for name, c in checks.items()
                 if name.startswith("command ") and c.get("requirement_keys")]
        for t in tasks:
            for a in (t.gate or {}).get("acceptance", []):
                cond = a.get("condition") or {}
                if cond.get("type") == "command" and cond.get("requirement_keys"):
                    named.append((f"task {t.key}: {cond.get('run') or ''}",
                                  {str(k).upper() for k in cond["requirement_keys"]}, a))
        worked = goal_keys.union(*per_task.values())
        wanted = worked.union(*(k for _, k, _ in named))
        reqs = _by_key(s, g.project_id, wanted)
        unknown = sorted(wanted - set(reqs))
        if unknown:
            log_event("requirement", f"iteration {iteration} names unknown requirement(s) {', '.join(unknown)}",
                      goal_id=goal_id, s=s, unknown=unknown)
        if not reqs:
            return
        for k in goal_keys & set(reqs):
            _link(s, reqs[k], "goal", f"goal:{goal_id}", goal_id, 0, True)
        for t in tasks:
            c = checks.get(f"task {t.key}", {})
            for k in per_task[t.id] & set(reqs):
                _link(s, reqs[k], "task", f"task:{t.id}", goal_id, iteration, bool(c.get("passed")),
                      f"{t.title}: {t.status}")
        for name, c in checks.items():
            if name.startswith("command ") and not c.get("requirement_keys"):  # unnamed: the iteration's ones
                for r in (reqs[k] for k in sorted(worked & set(reqs))):
                    _link(s, r, "check", name[8:], goal_id, iteration, bool(c.get("passed")),
                          str(c.get("detail", ""))[-500:])
        for ref, keys, c in named:
            for k in keys & set(reqs):
                _link(s, reqs[k], "check", ref, goal_id, iteration, bool(c.get("passed")),
                      str(c.get("detail", ""))[-500:])


def _tested(s: Any, r: Requirement, goal_id: int | None = None) -> bool:
    q = select(RequirementLink.id).where(RequirementLink.requirement_id == r.id, RequirementLink.kind == "check",
                                         RequirementLink.version == r.version, RequirementLink.passed)
    if goal_id is not None:
        q = q.where(RequirementLink.goal_id == goal_id)
    return s.scalars(q).first() is not None


def on_merged(goal_id: int, sha: str) -> None:
    """The merge records its commit on every requirement the goal or its tasks named, and moves approved ones on."""
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None or g.project_id is None:
            return
        keys = {str(k).upper() for k in g.requirement_keys or []}
        for t in s.scalars(select(Task).where(Task.goal_id == goal_id, Task.status == "done")):
            keys |= {str(k).upper() for k in (t.contract or {}).get("requirement_keys") or []}
        for r in _by_key(s, g.project_id, keys).values():
            _link(s, r, "merge", sha, goal_id, g.iteration, True, f"merged {g.branch} into {g.base_branch}")
            before = r.status
            if r.status in ("approved", "implemented") and _tested(s, r, goal_id):
                r.status = "verified"
            elif r.status == "approved":
                r.status = "implemented"
            if r.status != before:
                log_event("requirement", f"requirement {r.key}: {before} -> {r.status} (merged {sha[:10]})",
                          goal_id=goal_id, s=s, requirement_id=r.id, project_id=r.project_id)


# --- reading -----------------------------------------------------------------------------------

def _link_row(x: RequirementLink) -> dict[str, Any]:
    return {"id": x.id, "version": x.version, "kind": x.kind, "ref": x.ref, "goal_id": x.goal_id,
            "iteration": x.iteration, "passed": x.passed, "detail": x.detail, "at": x.created_at.isoformat()}


def traceability(project_id: int) -> dict[str, Any]:
    """Per requirement (current version): implemented (a passing task), tested (a passing check), merged (a merge
    commit), with the goals and commits; and the counts."""
    with session() as s:
        reqs = list(s.scalars(select(Requirement).where(Requirement.project_id == project_id)
                              .order_by(Requirement.key)))
        links: dict[int, list[RequirementLink]] = {}
        for x in s.scalars(select(RequirementLink).where(RequirementLink.requirement_id.in_([r.id for r in reqs]))):
            links.setdefault(x.requirement_id, []).append(x)
        rows = []
        for r in reqs:
            cur = [x for x in links.get(r.id, []) if x.version == r.version]
            rows.append({"id": r.id, "key": r.key, "text": r.text, "kind": r.kind, "status": r.status,
                         "actors": (r.details or {}).get("actors") or [],
                         "version": r.version, "conflicts": r.conflicts or [],
                         "implemented": any(x.kind == "task" and x.passed for x in cur),
                         "tested": any(x.kind == "check" and x.passed for x in cur),
                         "failing": sorted({x.ref for x in cur if x.kind in ("task", "check") and not x.passed}
                                           - {x.ref for x in cur if x.kind in ("task", "check") and x.passed})[:10],
                         "merged": [x.ref for x in cur if x.kind == "merge"],
                         "goals": sorted({x.goal_id for x in cur if x.goal_id}),
                         "earlier_versions_linked": any(x.version != r.version for x in links.get(r.id, []))})
    live = [x for x in rows if x["status"] != "retired"]
    # S09-R-06: agreed requirements no passing check covers in their current version (drafts are not agreed yet)
    untested = [x["key"] for x in live if x["status"] != "draft" and not x["tested"]]
    return {"project_id": project_id, "requirements": rows, "untested": untested, "actors": actors(project_id),
            "counts": {"total": len(live), "implemented": sum(x["implemented"] for x in live),
                       "tested": sum(x["tested"] for x in live), "merged": sum(bool(x["merged"]) for x in live),
                       "untested": len(untested),
                       "conflicts": sum(bool(x["conflicts"]) for x in live),
                       "by_status": {st: sum(x["status"] == st for x in rows) for st in STATUSES}}}


def count(project_id: int) -> int:
    with session() as s:
        return s.scalar(select(func.count(Requirement.id)).where(Requirement.project_id == project_id)) or 0


# --- import of docs/requirements ---------------------------------------------------------------

_ID = r"(?:S\d{2}|G|SE|WS)-(?:FR|R|D|G)-\d+"
_STATUS_OF = {"done": "implemented", "partial": "approved", "open": "approved", "gated": "draft", "skip": "retired"}


def _status_word(text: str) -> str:
    m = re.match(r"\W*(done|partial|open|gated|skip)", text.strip().lower())
    return _STATUS_OF[m.group(1)] if m else "draft"


def parse_docs(folder: Path) -> list[dict[str, Any]]:
    """Requirement IDs of the Markdown files in `folder`: table rows (`| ID | ... | Requirement | ... | Status |`)
    and `### ID Title` sections (their bullets are the text; a `**Status: ...**` line, else the table's status)."""
    found: dict[str, dict[str, Any]] = {}
    for f in sorted(folder.glob("*.md")):
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        header: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if cells and cells[0].lower() == "id":
                    header = [c.lower() for c in cells]
                elif header and re.fullmatch(_ID, cells[0]):
                    col = next((header.index(h) for h in ("requirement", "constraint", "description", "item")
                                if h in header), None)
                    text = cells[col] if col is not None and col < len(cells) else ""
                    st = header.index("status") if "status" in header else None
                    item = found.setdefault(cells[0], {"key": cells[0], "text": "", "status": "",
                                                       "source": f"docs/requirements/{f.name}"})
                    item["text"] = item["text"] or text
                    item["status"] = item["status"] or (_status_word(cells[st]) if st is not None and st < len(cells)
                                                        else "draft")
            else:
                header = [] if not line.strip() else header
                m = re.match(rf"^###\s+({_ID})\s+(.+?)\s*$", line)
                if m:
                    body, status = [], ""
                    i += 1
                    while i < len(lines) and not lines[i].startswith("#") and not re.match(r"\|\s*id\s*\|", lines[i], re.I):
                        sm = re.search(r"\*\*Status:\s*([^*]+)\*\*", lines[i])
                        if sm:
                            status = _status_word(sm.group(1))
                        elif lines[i].strip() and len(" ".join(body)) < 3000:
                            body.append(lines[i].strip())
                        i += 1
                    item = found.setdefault(m.group(1), {"key": m.group(1), "text": "", "status": "",
                                                         "source": f"docs/requirements/{f.name}"})
                    item["text"] = (m.group(2) + ("\n" + "\n".join(body) if body else ""))[:8000]
                    item["status"] = status or item["status"]
                    continue
            i += 1
    return [x | {"status": x["status"] or "approved"} for x in found.values() if x["text"]]


def import_docs(project_id: int, actor: str) -> dict[str, Any]:
    """Load `<project path>/docs/requirements` (the platform project falls back to this repository's). Same key and
    text: untouched (a status a person moved stays); changed text: a new version with the document's status."""
    from agent_company.config import REPO_ROOT

    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(f"project {project_id}")
        folder = Path(p.path or "") / "docs" / "requirements"
        if not folder.is_dir() and p.protected:
            folder = Path(REPO_ROOT) / "docs" / "requirements"
    if not folder.is_dir():
        raise ValueError(f"no docs/requirements folder in project {project_id}")
    have = {r["key"]: r for r in list_requirements(project_id)}
    added = changed = same = 0
    for item in parse_docs(folder):
        old = have.get(item["key"])
        if old is None:
            create(project_id, item["text"], key=item["key"], source=item["source"], actor=actor,
                   status=item["status"], check_conflicts=False)
            added += 1
        elif old["text"] != item["text"]:
            update(old["id"], actor, text=item["text"], source=item["source"], check_conflicts=False)
            if item["status"] != "draft":
                with session() as s:  # the document's own status, after the new version
                    s.get(Requirement, old["id"]).status = item["status"]
            changed += 1
        else:
            same += 1
    log_event("requirement", f"imported {folder}: {added} new, {changed} changed, {same} unchanged",
              project_id=project_id)
    return {"folder": str(folder), "added": added, "changed": changed, "unchanged": same}
