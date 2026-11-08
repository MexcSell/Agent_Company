"""Root-cause analysis of failing goals (docs/requirements/ASEAO07-analysis.md S07-R-02, ASEAO07 §10, AT-012).

Triggers, checked when a goal ends (`after_goal`, from loop._notify_goal):
- the goal failed after using all its iterations;
- the same check failed in the last STREAK finished goals of a project (once per streak: a check already analysed
  is analysed again only after STREAK more failing goals).

The platform collects the evidence itself (`evidence`): each goal's tasks with role, agent, status, dependencies,
estimated against actual minutes and failed acceptance conditions; the checks that failed and in how many
iterations (the defects); failed reviews; open blockers; failed runs and error events from the trace. Every item
carries a reference (`check:…`, `task:…`, `run:…`, `event:…`, `blocker:…`). The General Manager (the analysis room
once it is enabled) reads it and answers with lines:

    CONFIRMED: <layer>: <cause> | <refs>        a cause the cited evidence shows
    CONTRIBUTING: <layer>: <factor> | <refs>    plausible, not shown
    ACTION: <corrective action> :: <why>
    FOLLOW_UP: <how to tell whether the action worked>

The layer (G-R-17) is one of `selfevolve.LAYERS`, so repeated causes show where the system must change (a code
defect is not fixed by a larger model); a missing or unknown layer stays unclassified, never guessed.
A CONFIRMED line citing no reference of the pack becomes CONTRIBUTING: a correlation is not a cause. Each ACTION
becomes a pending proposal (decided like any other, at most ACTIONS). The analysis is stored as a knowledge item of
kind `operational` (screened against memory poisoning like every lesson, S11-R-05). Follow-up: when a goal spawned
from one of the actions ends, the item records whether the checks the analysis watched passed there.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from agent_company.db import (Agent, Defect, Event, Goal, Lesson, Proposal, Risk, Run, Task, log_event, now,
                              session)
from agent_company.performance import OPEN
from agent_company.selfevolve import LAYERS

STREAK = 3
ACTIONS = 3
FINISHED = ("approved", "awaiting_approval", "failed", "rejected")
_LINE = re.compile(r"(?im)^[\s*-]*(CONFIRMED|CONTRIBUTING|ACTION|FOLLOW_UP|FOLLOW-UP):\s*(.+?)\s*$")

INSTRUCTIONS = """Find the root causes of the failure from the evidence above. Answer with lines only:
CONFIRMED: <layer>: <cause> | <references from the evidence that show it, e.g. check:scope, task:12, run:40>
CONTRIBUTING: <layer>: <factor that plausibly contributed but the evidence does not show> | <references, if any>
ACTION: <one corrective action a goal can carry out> :: <why it addresses a cause>
FOLLOW_UP: <how to tell whether the actions worked, e.g. which check must pass>
A cause is CONFIRMED only when the cited evidence shows it; something that merely happened at the same time is
CONTRIBUTING at most. <layer> is where the cause lies: model (the model misunderstood a clear requirement), prompt
(instructions incomplete or ambiguous), code (the implementation lacks validation or recovery), orchestration (task
split, dependencies or allocation), tools (a tool, API or adapter misbehaved), data (retrieved or input material was
wrong or stale), environment (permissions, runtime or infrastructure). Give at most three actions; never propose to
weaken a check, a permission or a budget."""
_LAYER = re.compile(rf"^\s*({'|'.join(LAYERS)})\s*:\s*(.+)$", re.I | re.S)


def _last_failed(g: Goal) -> list[str]:
    h = (g.history or [])[-1:] if g.history else []
    return list(h[0].get("failed_checks") or []) if h else []


def _streak(s: Any, project_id: int) -> tuple[list[int], list[str]]:
    """The last STREAK finished goals of the project and the checks that failed in every one of them."""
    goals = list(s.scalars(select(Goal).where(Goal.project_id == project_id, Goal.origin.not_in(("eval",)),
                                              Goal.status.in_(FINISHED)).order_by(Goal.id.desc()).limit(STREAK)))
    if len(goals) < STREAK:
        return [], []
    common = set(_last_failed(goals[0]))
    for g in goals[1:]:
        common &= set(_last_failed(g))
    common = {c for c in common if not c.startswith("task ")}  # task keys are per plan, not the same check
    return [g.id for g in goals], sorted(common)


def _analysed(s: Any, project_id: int, checks: list[str], since: Any) -> bool:
    """A streak analysis of the project watching one of these checks after `since` (the streak's oldest goal).
    Analyses of single goals do not count: they never looked across goals."""
    for item in s.scalars(select(Lesson).where(Lesson.project_id == project_id, Lesson.kind == "operational",
                                               Lesson.created_at >= since)):
        rep = item.report or {}
        if rep.get("scope") == "streak" and set(rep.get("watch") or []) & set(checks):
            return True
    return False


def evidence(goal_ids: list[int]) -> dict[str, Any]:
    """The evidence pack: items with references, and its text for the analyst."""
    items: list[tuple[str, str]] = []
    watch: dict[str, int] = {}
    from agent_company import mission

    labels: dict[int, str] = {}
    for gid in goal_ids:  # before the session below: the first read of a goal's counts stores them
        try:
            c = mission.label_counts(gid)
            labels[gid] = f" (task labels: verified {c['verified']}, partial {c['partial']}, failed {c['failed']})"
        except Exception:  # noqa: BLE001 - an active goal or missing counts never break the pack
            pass
    with session() as s:
        for gid in goal_ids:
            g = s.get(Goal, gid)
            if g is None:
                continue
            items.append((f"goal:{gid}", f"goal #{gid} '{g.title[:120]}': {g.status} after {g.iteration} of "
                                         f"{g.max_iterations} iteration(s)" + labels.get(gid, "")))
            for d in s.scalars(select(Defect).where(Defect.goal_id == gid).order_by(Defect.id)):
                n = d.last_iteration - d.first_iteration + 1
                items.append((f"check:{d.check}", f"goal #{gid}: '{d.check}' ({d.severity}) failed in iteration(s) "
                                                  f"{d.first_iteration}-{d.last_iteration}, now {d.status}: "
                                                  f"{d.detail.strip()[:300]}"))
                if d.status in OPEN:
                    watch[d.check] = watch.get(d.check, 0) + n
            minutes = dict(s.execute(select(Run.task_id, func.sum(Run.duration)).where(Run.goal_id == gid)
                                     .group_by(Run.task_id)).all())
            names = dict(s.execute(select(Agent.id, Agent.name)).all())
            for t in s.scalars(select(Task).where(Task.goal_id == gid, Task.iteration == g.iteration)
                               .order_by(Task.id)):
                est = (t.contract or {}).get("estimated_minutes") or 0
                failed = [c.get("detail", "") for c in (t.gate or {}).get("acceptance", []) if not c.get("passed")]
                items.append((f"task:{t.id}", f"goal #{gid} task '{t.key}' ({t.role}, {names.get(t.agent_id, '-')})"
                              f": {t.status}/{t.stage}" + (f", after {', '.join(t.depends_on)}" if t.depends_on else "")
                              + (f", estimated {est} min" if est else "")
                              + f", ran {float(minutes.get(t.id) or 0) / 60:.1f} min"
                              + (f"; failed conditions: {'; '.join(x[:150] for x in failed[:3])}" if failed else "")))
            for r in s.scalars(select(Run).where(Run.goal_id == gid, Run.ok.is_(False)).order_by(Run.id).limit(10)):
                items.append((f"run:{r.id}", f"goal #{gid} {r.purpose} run on {r.model_name} failed: "
                                             f"{r.error.strip()[:200]}"))
            for e in s.scalars(select(Event).where(Event.goal_id == gid, Event.kind.in_(("error", "escalation")))
                               .order_by(Event.id.desc()).limit(8)):
                items.append((f"event:{e.id}", f"goal #{gid} {e.kind}: {e.message[:200]}"))
            if g.project_id is not None:
                for b in s.scalars(select(Risk).where(Risk.project_id == g.project_id, Risk.goal_id == gid,
                                                      Risk.kind == "blocker")):
                    items.append((f"blocker:{b.id}", f"goal #{gid} blocker ({b.severity}): {b.title[:200]}"))
    text = "\n".join(f"- [{ref}] {line}" for ref, line in items)
    return {"items": items, "refs": {ref for ref, _ in items if not ref.startswith("goal:")}, "text": text,
            "watch": sorted(watch, key=lambda c: -watch[c])[:5]}


def _cited(text: str, refs: set[str]) -> list[str]:
    """The pack's references named in `text` (whole references only: task:1 is not task:12)."""
    return sorted(r for r in refs if re.search(rf"(?<![\w:]){re.escape(r)}(?![\w.-])", text))


def parse(output: str, refs: set[str]) -> dict[str, Any]:
    """The analyst's lines; a CONFIRMED cause without a reference from the pack is only CONTRIBUTING."""
    out: dict[str, Any] = {"confirmed": [], "contributing": [], "actions": [], "follow_up": ""}
    for kind, body in _LINE.findall(output or ""):
        kind = kind.upper().replace("-", "_")
        if kind in ("CONFIRMED", "CONTRIBUTING"):
            cause, _, cited = body.partition("|")
            found = _cited(cited or cause, refs)
            m = _LAYER.match(cause)
            layer, cause = (m.group(1).lower(), m.group(2)) if m else ("", cause)
            entry = {"cause": cause.strip()[:400], "evidence": found[:6], "layer": layer}
            if kind == "CONFIRMED" and not found:
                entry["demoted"] = "no evidence of the pack cited"
                out["contributing"].append(entry)
            else:
                out["confirmed" if kind == "CONFIRMED" else "contributing"].append(entry)
        elif kind == "ACTION":
            title, _, why = body.partition("::")
            if title.strip() and len(out["actions"]) < ACTIONS:
                out["actions"].append({"title": title.strip()[:256], "why": why.strip()[:1000]})
        elif not out["follow_up"]:
            out["follow_up"] = body[:500]
    return out


def analyse(goal_id: int, trigger: str, goal_ids: list[int] | None = None,
            watch: list[str] | None = None) -> dict[str, Any] | None:
    """Run the analysis for `goal_id` (and the other goals of a streak); returns the stored item's report."""
    from agent_company import executors, loop, registry
    from agent_company.learning import goal_level, screen

    ids = sorted(set(goal_ids or []) | {goal_id})
    pack = evidence(ids)
    g = loop._goal(goal_id)
    gm = registry.staff("general_manager", ["analysis"], goal_id, prefer=loop._project_manager(g))
    try:
        prompt = (f"Root-cause analysis ({trigger}).\n\nEvidence collected by the platform:\n{pack['text']}\n\n"
                  + INSTRUCTIONS)
        req = executors.Request(purpose="rca", role="general_manager", system=loop._system_for(gm), prompt=prompt,
                                workdir=Path(g.worktree) if g.worktree else None,
                                data={"items": pack["items"], "watch": watch or pack["watch"], "goal_id": goal_id})
        res, choice = loop._execute(gm, req, "rca", goal_id=goal_id)
    finally:
        registry.release(gm.id, goal_id)
    if not res.ok:
        log_event("rca", f"root-cause analysis of goal #{goal_id} not made: {res.error[:200]}", goal_id=goal_id)
        return None
    found = parse(res.text, pack["refs"])
    report = {"rca": True, "trigger": trigger, "scope": "streak" if len(ids) > 1 else "goal", "goals": ids,
              "watch": list(watch or pack["watch"]),
              "confirmed": found["confirmed"], "contributing": found["contributing"],
              "follow_up": found["follow_up"], "actions": [], "model": choice.name}
    text = (f"Root-cause analysis of goal{'s' if len(ids) > 1 else ''} {', '.join(f'#{i}' for i in ids)} "
            f"({trigger}). Confirmed: " + ("; ".join(c["cause"] for c in found["confirmed"]) or "none")
            + ". Contributing: " + ("; ".join(c["cause"] for c in found["contributing"]) or "none") + "."
            + (f" Follow-up: {found['follow_up']}" if found["follow_up"] else ""))[:1000]
    with session() as s:
        row = s.get(Goal, goal_id)
        status, reason = screen(f"{text}\n" + "\n".join(a["title"] + " " + a["why"] for a in found["actions"]))
        level = goal_level(s, row)
        if level == "secret":  # secret content is never stored in knowledge, nor proposed from (S14-R-05)
            text, status, reason = "(withheld: the work is classified secret)", "rejected", "secret goal or project"
        # outcome "analysis": knowledge, not the analyst's own success or failure (evolution counts those)
        item = Lesson(agent_id=gm.id, goal_id=goal_id, project_id=row.project_id, text=text, outcome="analysis",
                      kind="operational", evidence_class="INFERENCE", sources=[f"goal:{i}" for i in ids],
                      status=status, status_reason=reason, classification=level, report={})
        s.add(item)
        s.flush()
        if status == "approved":  # a screened-out analysis proposes nothing (it waits for a person in the queue)
            for a in found["actions"]:
                p = Proposal(goal_id=goal_id, project_id=row.project_id, agent_id=gm.id, title=a["title"],
                             description=(f"{a['why']}\n\nFrom root-cause analysis K{item.id} of goal #{goal_id}."
                                          + (f" Follow-up: {found['follow_up']}" if found["follow_up"] else "")))
                s.add(p)
                s.flush()
                report["actions"].append({"proposal_id": p.id, "title": a["title"], "result": "pending"})
        elif level != "secret":
            log_event("security", f"root-cause analysis K{item.id} {status}: {reason}", goal_id=goal_id, s=s,
                      rule="memory_poisoning")
        item.report = dict(report)  # set once, after the proposals: in-place changes of a JSON column are not seen
        from agent_company import kgraph

        kgraph.on_lesson(s, item, False, [])
        log_event("rca", f"root-cause analysis K{item.id} of goal #{goal_id}: {len(found['confirmed'])} confirmed "
                  f"cause(s), {len(report['actions'])} proposal(s)", goal_id=goal_id, agent_id=gm.id, s=s,
                  knowledge_id=item.id, trigger=trigger)
        report["id"] = item.id
        project_id = row.project_id
    from agent_company.config import settings

    if report["actions"] and settings().auto_approve_proposals:  # owner policy, after the session committed
        from agent_company import ownerpolicy

        done = ownerpolicy.approve_pending(ids=[a["proposal_id"] for a in report["actions"]])
        result = {x["id"]: k for k, xs in done.items() for x in xs}
        for a in report["actions"]:
            outcome = result.get(a["proposal_id"], "held")
            a["result"] = "pending" if outcome == "held" else outcome
    try:  # after the session: drafting a rule may run a model (EL-R-04)
        report["rules"] = propose_rules(project_id)
    except Exception as e:  # noqa: BLE001 - a rule proposal never breaks the analysis or the goal's ending
        log_event("error", f"rule proposals of goal #{goal_id}: {type(e).__name__}: {e}", goal_id=goal_id)
    return report


TEMPLATE = "Before handing work back, make sure the check '{check}' passes; it failed for a {layer} cause in goals {ids}."
_RULE_PREFIX = re.compile(r"^[\s*#-]*(RULE:\s*)?", re.I)


def _causes(s: Any, project_id: int) -> dict[tuple[str, str], dict[str, set[int]]]:
    """Causes (layer, failing check) of the project's approved analyses and the distinct failed goals of each."""
    from agent_company.learning import goal_level

    out: dict[tuple[str, str], dict[str, set[int]]] = {}
    ok: dict[int, bool] = {}

    def counts(gid: int) -> bool:
        if gid not in ok:
            g = s.get(Goal, gid)
            ok[gid] = bool(g is not None and g.project_id == project_id and g.status == "failed"
                           and g.origin not in ("eval", "improvement") and goal_level(s, g) != "secret")
        return ok[gid]

    for item in s.scalars(select(Lesson).where(Lesson.project_id == project_id, Lesson.kind == "operational",
                                               Lesson.status == "approved", Lesson.classification != "secret")
                          .order_by(Lesson.id)):
        rep = item.report or {}
        if not rep.get("rca"):
            continue
        watch = [c for c in rep.get("watch") or [] if not c.startswith("task ")]  # task keys are per plan
        goals = {int(g) for g in rep.get("goals") or [] if counts(int(g))}
        if not goals:
            continue
        for c in rep.get("confirmed") or []:
            if c.get("layer") not in LAYERS:
                continue
            cited = [r[6:] for r in c.get("evidence") or [] if r.startswith("check:") and r[6:] in watch]
            for chk in cited or watch:
                v = out.setdefault((c["layer"], chk), {"goals": set(), "lessons": set()})
                v["goals"] |= goals
                v["lessons"].add(item.id)
    return out


def _ids(goal_ids: list[int]) -> str:
    return ", ".join(f"#{g}" for g in goal_ids)


def _draft_rule(layer: str, check: str, goal_ids: list[int]) -> tuple[str, int | None]:
    """The instructor's one-line rule for this cause, else the template line. Returns (line, instructor id)."""
    from agent_company import executors, router, usage
    from agent_company.config import settings
    from agent_company.db import Agent as A
    from agent_company.learning import goal_level, screen

    chk = " ".join(check.split())[:120]
    template = TEMPLATE.format(check=chk, layer=layer, ids=_ids(goal_ids))
    try:
        with session() as s:
            judge = s.scalars(select(A).where(A.role == "instructor", A.enabled.is_(True)).order_by(A.id)).first()
            if judge is None:
                return template, None
            jid, jname, pref = judge.id, judge.name, judge.model_pref
            level = goal_level(s, s.get(Goal, max(goal_ids)))
        if _out_of_budget(max(goal_ids)):
            return template, jid
        choice = router.resolve("instructor", pref, classification=level)
        req = executors.Request(
            purpose="rule", role="instructor", agent_id=jid, goal_id=max(goal_ids),
            system=f"You are {jname}, the instructor.",
            prompt=(f"Write ONE line, a rule for `.claude/rules/` that would have prevented this cause: layer {layer}, "
                    f"failing check '{chk}', goals {_ids(goal_ids)}. Answer with the line only."),
            data={"fallback": template, "layer": layer, "check": check})
        res = executors.for_choice(choice, settings().command_timeout).run(req)
        usage.record(model_name=choice.name, kind=choice.kind, input_tokens=res.input_tokens,
                     output_tokens=res.output_tokens, cost=res.cost, duration=res.duration, ok=res.ok,
                     error=res.error, agent_id=jid, purpose="rule", goal_id=max(goal_ids), res=res)
        lines = [x for x in (res.text or "").strip().splitlines() if x.strip()]
        if not res.ok or len(lines) != 1:
            return template, jid
        line = _RULE_PREFIX.sub("", lines[0]).strip()
        if not 20 <= len(line) <= 200 or screen(line)[0] != "approved":
            return template, jid
        return line, jid
    except Exception:  # noqa: BLE001 - router.NotCleared, a failed run: the template stands in
        return template, None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", text.lower()).strip("-")


def propose_rules(project_id: int | None) -> list[int]:
    """EL-R-04: a cause (layer, failing check) confirmed in `rule_proposal_min_goals` failed goals of the project
    becomes one pending rule proposal citing them; never a second one for the same cause. Returns the new ids."""
    from agent_company.config import settings

    if project_id is None:
        return []
    least = settings().rule_proposal_min_goals

    def existing(s: Any) -> set[tuple[str, str]]:
        out = set()
        for p in s.scalars(select(Proposal).where(Proposal.project_id == project_id, Proposal.kind == "rule")):
            cause = (p.case or {}).get("cause") or {}
            out.add((cause.get("layer", ""), cause.get("check", "")))
        return out

    with session() as s:
        causes = _causes(s, project_id)
        have = existing(s)
    due = sorted(k for k, v in causes.items() if len(v["goals"]) >= least and k not in have)
    drafts = {k: _draft_rule(k[0], k[1], sorted(causes[k]["goals"])) for k in due}
    made: list[int] = []
    with session() as s:
        have = existing(s)
        for (layer, check), (line, jid) in drafts.items():
            if (layer, check) in have:
                continue
            goals, lessons = sorted(causes[(layer, check)]["goals"]), sorted(causes[(layer, check)]["lessons"])
            name = f"rca-{layer}-{_slug(check)}"[:64].rstrip("-.")
            p = Proposal(goal_id=max(goals), project_id=project_id, agent_id=jid, kind="rule", tag="rule",
                         status="pending", title=f"Rule: {line}"[:256],
                         description=(f"{line}\n\nCause: {layer} layer, failing check '{check}', confirmed by "
                                      f"root-cause analyses {', '.join(f'K{k}' for k in lessons)} in goals "
                                      f"{_ids(goals)}."),
                         case={"cause": {"layer": layer, "check": check}, "goals": goals, "rule": line,
                               "name": name, "lessons": lessons})
            s.add(p)
            s.flush()
            have.add((layer, check))
            made.append(p.id)
            log_event("rca", f"rule proposal {p.id} for {layer}/{check} from goals {_ids(goals)}",
                      goal_id=max(goals), s=s)
    return made


def follow_up(goal_id: int) -> list[int]:
    """A goal spawned from an analysis's action ended: record whether the watched checks passed in it. Returns the
    ids of the knowledge items updated."""
    updated = []
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None or g.origin != "proposal":
            return []
        pids = set(s.scalars(select(Proposal.id).where(Proposal.spawned_goal_id == goal_id)))
        if not pids:
            return []
        failed = set(_last_failed(g))
        for item in s.scalars(select(Lesson).where(Lesson.kind == "operational", Lesson.project_id == g.project_id)):
            rep = dict(item.report or {})
            acts = [dict(a) for a in rep.get("actions") or []]
            hit = [a for a in acts if a.get("proposal_id") in pids]
            if not rep.get("rca") or not hit:
                continue
            watched = [c for c in rep.get("watch") or [] if c in failed]
            passed = g.status in ("approved", "awaiting_approval")
            for a in hit:
                a.update(goal_id=goal_id, at=now().isoformat(),
                         result="worked" if passed and not watched else "did not work",
                         still_failing=watched)
            item.report = rep | {"actions": acts}
            updated.append(item.id)
            log_event("rca", f"follow-up of root-cause analysis K{item.id}: goal #{goal_id} "
                      + ("passed the watched checks" if passed and not watched else
                         "did not fix " + (", ".join(watched) or "the failure")), goal_id=goal_id, s=s,
                      knowledge_id=item.id)
    return updated


def _out_of_budget(goal_id: int) -> bool:
    """The analysis is a model run billed to the failed goal (and so its project): none once the project's budget is
    used up. A goal's own budget is not checked: it failed, and the analysis is the last spend on it."""
    from agent_company.db import Project
    from agent_company.projects import project_spend

    with session() as s:
        g = s.get(Goal, goal_id)
        p = s.get(Project, g.project_id) if g is not None and g.project_id is not None else None
        budget = float(p.budget_usd or 0) if p is not None else 0.0
    return budget > 0 and project_spend(g.project_id) >= budget


def after_goal(goal_id: int, status: str) -> dict[str, Any] | None:
    """Called when a goal ends: follow up earlier analyses, then analyse when a trigger holds. Never raises."""
    try:
        follow_up(goal_id)
        with session() as s:
            g = s.get(Goal, goal_id)
            if g is None or g.origin in ("eval", "improvement"):
                return None
            exhausted = status == "failed" and g.iteration >= g.max_iterations
            streak, common = _streak(s, g.project_id) if g.project_id is not None else ([], [])
            if common and goal_id in streak:
                oldest = s.get(Goal, min(streak))
                if _analysed(s, g.project_id, common, oldest.created_at):
                    streak, common = [], []
            else:
                streak, common = [], []
        if (common or exhausted) and _out_of_budget(goal_id):
            log_event("rca", f"no root-cause analysis of goal #{goal_id}: the project's budget is used up",
                      goal_id=goal_id)
            return None
        if common:
            return analyse(goal_id, f"'{common[0]}' failed in {STREAK} goals in a row", streak, common)
        if exhausted:
            return analyse(goal_id, f"failed after {g.max_iterations} iteration(s)")
    except Exception as e:  # noqa: BLE001 - an analysis never breaks the goal's ending
        log_event("error", f"root-cause analysis of goal #{goal_id}: {type(e).__name__}: {e}", goal_id=goal_id)
    return None


def list_analyses(project_id: int | None = None, goal_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with session() as s:
        q = select(Lesson).where(Lesson.kind == "operational").order_by(Lesson.id.desc()).limit(limit)
        if project_id is not None:
            q = q.where(Lesson.project_id == project_id)
        rows = [x for x in s.scalars(q) if (x.report or {}).get("rca")]
        if goal_id is not None:
            rows = [x for x in rows if goal_id in (x.report or {}).get("goals", [])]
        return [{"id": x.id, "text": x.text, "status": x.status, "at": x.created_at.isoformat(), **(x.report or {})}
                for x in rows]


def layer_counts(s: Any) -> dict[str, Any]:
    """Confirmed causes of every root-cause analysis per layer (G-R-17); `unclassified` when none was named."""
    counts = {layer: 0 for layer in [*LAYERS, "unclassified"]}
    analyses = 0
    for (report,) in s.execute(select(Lesson.report).where(Lesson.kind == "operational")):
        if not (report or {}).get("rca"):
            continue
        analyses += 1
        for c in report.get("confirmed") or []:
            counts[c.get("layer") if c.get("layer") in LAYERS else "unclassified"] += 1
    return {"layers": counts, "causes": sum(counts.values()), "n": analyses}
