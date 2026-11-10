"""G5 Control Hub overviews: Evolution Lab (UI-G5-EV), Governance (UI-G5-GV) and Observability (UI-G5-OB).

Read-only compositions of stored records and the live settings (G5-D-06); actions stay on the pages that perform
them, with their own permission checks and audit. Counts carry their sample sizes; nothing is estimated.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from agent_company.config import settings
from agent_company.dashboard import LIVE, PERIODS, _iso
from agent_company.db import (Agent, AuditEvent, CatalogItem, DefinitionCandidate, Document, Event, EvolutionEvent,
                              Experiment, Goal, ImprovementContract, LearningPlan, Lesson, LiveProcess, McpCall,
                              McpServer, OrgProposal, OrgVersion, Recruitment, Room, Run, Task, now, session)

# --- Evolution Lab ------------------------------------------------------------------------------------------------

LAB_PERIODS = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}
STEPS = ("observe", "diagnose", "propose", "experiment", "validate", "release")  # link 6af21f11 evolution states
MIN_TASKS = 3  # finished tasks an agent needs on each side before its success rate counts as a trend


def _between(col: Any, a: Any, b: Any) -> Any:
    return (col >= a) & (col < b)


def _task_rates(s: Any, a: Any, b: Any) -> dict[int, tuple[int, int]]:
    out: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for aid, st, n in s.execute(select(Task.agent_id, Task.status, func.count()).join(Goal, Goal.id == Task.goal_id)
                                .where(Goal.origin != "eval", Task.agent_id.is_not(None),
                                       _between(Task.finished_at, a, b), Task.status.in_(("done", "failed")))
                                .group_by(Task.agent_id, Task.status)):
        out[aid][0 if st == "done" else 1] += n
    return {k: (v[0], v[0] + v[1]) for k, v in out.items()}


def capability_table(s: Any) -> list[dict[str, Any]]:
    """UI-G5-EV-02: each skill the active projects require -> Available / Weak / Missing -> Reuse / Improve /
    Develop / Acquire, from verified proficiency (`recruitment.available`); Develop when a learning plan for it is
    under way, Acquire (recruit or the marketplace) when not."""
    from agent_company import recruitment
    from agent_company.db import Project
    from agent_company.registry import _slug

    need: dict[str, list[str]] = defaultdict(list)
    for p in s.scalars(select(Project).where(Project.status == "active")):
        for k in p.required_skills or []:
            need[_slug(k)].append(p.name)
    if not need:
        return []
    have = recruitment.available(s, list(need))
    learning = {pl.capability for pl in s.scalars(select(LearningPlan).where(
        LearningPlan.status.in_(("planned", "running"))))}
    out = []
    for k in sorted(need):
        avail = round(have[k]["available"], 2)
        status = "available" if avail >= 1.0 else "weak" if avail > 0 else "missing"
        action = {"available": "reuse", "weak": "improve"}.get(status) or ("develop" if k in learning else "acquire")
        out.append({"skill": k, "available": avail, "status": status, "action": action, "projects": need[k],
                    "agents": [{"id": x["id"], "name": x["name"], "p": round(x["p"], 2)} for x in have[k]["agents"]]})
    return out


def evolution_lab(period: str = "30d") -> dict[str, Any]:
    from agent_company import command, performance

    span = LAB_PERIODS.get(period, LAB_PERIODS["30d"])
    end = now()
    start, prev = end - span, end - 2 * span
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        # 1. agents improving: a measured step (certification, rolled-out playbook, applied definition) or a higher
        #    task success rate than in the period before, with MIN_TASKS finished tasks on each side
        cur, before = _task_rates(s, start, end), _task_rates(s, prev, start)
        steps: dict[int, list[str]] = defaultdict(list)
        plans = list(s.scalars(select(LearningPlan).where(_between(LearningPlan.updated_at, start, end),
                                                          LearningPlan.status.in_(("passed", "failed")))))
        for pl in plans:
            if pl.status == "passed":
                steps[pl.agent_id].append(f"certified in {pl.capability} (L{pl.current_level} → L{pl.target_level})")
        exps = list(s.scalars(select(Experiment).where(_between(Experiment.updated_at, start, end))))
        for e in exps:
            if e.status == "rolled_out" and e.agent_id:
                steps[e.agent_id].append(f"playbook {e.baseline_version} → candidate rolled out (experiment #{e.id})")
        applied = list(s.scalars(select(DefinitionCandidate).where(
            DefinitionCandidate.status == "applied", _between(DefinitionCandidate.decided_at, start, end))))
        for c in applied:
            steps[c.agent_id].append(f"definition rewrite applied (v{c.base_version} → v{c.base_version + 1})")
        improving = []
        for aid in sorted(set(steps) | set(cur), key=lambda x: names.get(x, "")):
            c, b = cur.get(aid), before.get(aid)
            trend = None
            if c and b and c[1] >= MIN_TASKS and b[1] >= MIN_TASKS:
                trend = {"now": round(c[0] / c[1], 3), "before": round(b[0] / b[1], 3), "n": c[1], "n_before": b[1]}
            if steps.get(aid) or (trend and trend["now"] > trend["before"]):
                improving.append({"id": aid, "name": names.get(aid, f"agent #{aid}"), "steps": steps.get(aid, []),
                                  "success_rate": trend})
        # 2. capabilities improved, 3. new skills, 4. knowledge acquired
        capabilities = [{"agent_id": pl.agent_id, "agent": names.get(pl.agent_id, ""), "capability": pl.capability,
                         "from": pl.current_level, "to": pl.target_level, "status": pl.status, "at": _iso(pl.updated_at)}
                        for pl in plans]
        skills = [{"id": c.id, "name": c.name, "source": c.source, "by": c.created_by, "enabled": c.enabled,
                   "at": _iso(c.created_at)} for c in s.scalars(select(CatalogItem).where(
                       CatalogItem.kind == "skill", CatalogItem.builtin.is_(False),
                       _between(CatalogItem.created_at, start, end)).order_by(CatalogItem.id.desc()).limit(30))]
        kn = Counter(o for (o,) in s.execute(select(Lesson.outcome).where(
            Lesson.status == "approved", _between(Lesson.created_at, start, end))))
        drafts = s.scalar(select(func.count()).select_from(Lesson).where(Lesson.status == "draft")) or 0
        # 5. experiments passed / failed
        finished = [e for e in exps if isinstance((e.results or {}).get("comparison"), dict) or e.status in (
            "rolled_out", "rolled_back", "rejected", "failed")]
        passed = [e for e in finished if (e.results or {}).get("comparison", {}).get("better") is True
                  and e.status not in ("rejected", "failed", "rolled_back")]
        exp_rows = [{"id": e.id, "target": e.target, "agent": names.get(e.agent_id or 0, ""), "role": e.role,
                     "status": e.status, "better": (e.results or {}).get("comparison", {}).get("better"),
                     "change": (e.results or {}).get("comparison", {}).get("pass_rate_change"),
                     "regressions": len((e.results or {}).get("comparison", {}).get("regressions") or []),
                     "at": _iso(e.updated_at)} for e in sorted(finished, key=lambda e: e.id, reverse=True)[:20]]
        # 6. improvements awaiting approval
        waiting = (
            [{"kind": "definition", "id": c.id, "title": f"Definition rewrite for {names.get(c.agent_id, '')}",
              "link": f"#/agents/{c.agent_id}", "at": _iso(c.created_at)} for c in s.scalars(
                select(DefinitionCandidate).where(DefinitionCandidate.status == "pending"))]
            + [{"kind": "experiment", "id": e.id, "title": f"Experiment #{e.id}: {e.target} candidate"
                + (f" for {names[e.agent_id]}" if e.agent_id in names else ""), "link": "#/evolution",
                "at": _iso(e.updated_at)} for e in s.scalars(select(Experiment).where(Experiment.status == "review"))]
            + [{"kind": "improvement", "id": c.id, "title": f"Improvement contract #{c.id}: {c.title}",
                "link": "#/evolution", "at": _iso(c.updated_at)} for c in s.scalars(select(ImprovementContract).where(
                    ImprovementContract.status == "verified"))]
            + [{"kind": "org", "id": o.id, "title": f"Organisation: {o.title}", "link": "#/organization",
                "at": _iso(o.created_at)} for o in s.scalars(select(OrgProposal).where(OrgProposal.status == "pending"))]
            + [{"kind": "recruitment", "id": r.id, "title": f"Recruit a {r.requested_role.replace('_', ' ')}",
                "link": "#/evolution", "at": _iso(r.created_at)} for r in s.scalars(select(Recruitment).where(
                    Recruitment.status == "pending"))])
        # 7. versions deployed
        deployed = (
            [{"kind": "playbook", "title": f"{names.get(e.agent_id or 0, e.role or e.target)}: experiment #{e.id} "
              f"rolled out", "at": _iso(e.updated_at)} for e in exps if e.status == "rolled_out"]
            + [{"kind": "definition", "title": f"{names.get(c.agent_id, '')}: definition v{c.base_version + 1}",
                "at": _iso(c.decided_at)} for c in applied]
            + [{"kind": "platform", "title": f"Improvement #{c.id} {c.title} ({c.status})",
                "at": _iso(c.updated_at)} for c in s.scalars(
                    select(ImprovementContract).where(ImprovementContract.status.in_(("monitoring", "stable")),
                                                      _between(ImprovementContract.updated_at, start, end)))]
            + [{"kind": "organisation", "title": f"Organisation v{o.version}: {o.reason[:120]}",
                "at": _iso(o.created_at)} for o in s.scalars(select(OrgVersion).where(
                    _between(OrgVersion.created_at, start, end)).order_by(OrgVersion.version.desc()).limit(10))])
        rolled_back = sum(1 for e in exps if e.status == "rolled_back")
        # 8. is the ecosystem performing better: the same counted metrics, this period vs the one before
        ops = performance.operations(s)
        m_now, m_before = command.metrics(s, start, end, None, ops), command.metrics(s, prev, start, None, ops)
        verdicts = []
        for key, lower in (("mission_success_rate", False), ("task_completion_rate", False),
                           ("cost_per_successful_mission", True)):
            a, b = m_now[key], m_before[key]
            va, vb = (a.get("usd"), b.get("usd")) if lower else (a.get("rate"), b.get("rate"))
            if va is None or vb is None or a["n"] < MIN_TASKS or b["n"] < MIN_TASKS:
                verdict = "insufficient"
            elif va == vb:
                verdict = "same"
            else:
                verdict = "better" if (va < vb if lower else va > vb) else "worse"
            verdicts.append({"metric": key, "now": va, "before": vb, "n": a["n"], "n_before": b["n"],
                             "verdict": verdict})
        judged = [v["verdict"] for v in verdicts if v["verdict"] != "insufficient"]
        overall = ("insufficient" if not judged else "better" if judged.count("better") > judged.count("worse")
                   else "worse" if judged.count("worse") > judged.count("better") else "same")
        # the lifecycle with its count per step in the period
        ev = Counter(st for (st,) in s.execute(select(EvolutionEvent.stage).where(
            _between(EvolutionEvent.created_at, start, end))))
        proposed = (s.scalar(select(func.count()).select_from(DefinitionCandidate).where(
            _between(DefinitionCandidate.created_at, start, end))) or 0) + (s.scalar(
                select(func.count()).select_from(OrgProposal).where(_between(OrgProposal.created_at, start, end))) or 0)
        rca_n = s.scalar(select(func.count()).select_from(Lesson).where(
            Lesson.outcome == "analysis", _between(Lesson.created_at, start, end))) or 0
        lifecycle = {
            "observe": ev["flag"] + ev["observe"],
            "diagnose": ev["diagnose"] + rca_n,
            "propose": ev["generate"] + proposed,
            "experiment": sum(1 for e in exps if e.created_at and _aware(e.created_at) >= start),
            "validate": ev["evaluate"] + sum(1 for e in exps if e.status in ("review", "canary")),
            "release": ev["promote"] + len(deployed),
        }
        table = capability_table(s)
    return {
        "period": period if period in LAB_PERIODS else "30d", "since": _iso(start),
        "improving": improving, "capabilities": capabilities, "skills": skills,
        "knowledge": {"approved": sum(kn.values()), "by_outcome": dict(kn), "drafts_waiting": drafts},
        "experiments": {"passed": len(passed), "failed": len(finished) - len(passed), "rolled_back": rolled_back,
                        "items": exp_rows},
        "awaiting": waiting, "deployed": deployed,
        "better": {"overall": overall, "metrics": verdicts},
        "lifecycle": [{"step": k, "count": lifecycle[k]} for k in STEPS],
        "capability_table": table,
    }


def _aware(t: Any) -> Any:
    return t if t.tzinfo else t.replace(tzinfo=now().tzinfo)


# --- Governance ---------------------------------------------------------------------------------------------------

AUTO, POLICY, HUMAN = "automatic", "policy", "human"


def autonomy_matrix() -> list[dict[str, Any]]:
    """UI-G5-GV-01: the activities of link 6af191fa §8 with who decides them now, from the live settings (G5-D-05).
    `level`: automatic (L1), policy (L2: checks, evaluation or limits decide) or human (L3: a person decides)."""
    st = settings()
    rows: list[tuple[str, str, str, str]] = [
        ("Research and analysis", AUTO, "Research room; public addresses only (netpin); E0 stops web research", ""),
        ("Mission planning", HUMAN if st.require_plan_approval else AUTO,
         "The General Manager plans; " + ("a person approves each plan" if st.require_plan_approval
                                          else "plans run without a person"), "require_plan_approval"),
        ("Dynamic team formation", POLICY, "Staffed from enabled rooms' registered agents by proficiency and load; "
         "gated roles need their room trial", ""),
        ("Recruiting a new agent", HUMAN if st.recruit_requires_approval == "always" else POLICY,
         f"Approval: {st.recruit_requires_approval}" + ("; recruits pass an eval suite first" if st.qualify_recruits
                                                        else ""), "recruit_requires_approval"),
        ("Task delegation", AUTO, "Task contracts with scope, budget and deadline", ""),
        ("Sandbox execution", POLICY if st.sandbox != "off" else HUMAN,
         {"required": "Every check runs in the L3 sandbox (bubblewrap, no network)",
          "auto": "Checks run in the L3 sandbox where bubblewrap is available",
          "off": "Sandbox off: checks run without isolation; an administrator chose this"}.get(st.sandbox, st.sandbox),
         "sandbox"),
        ("Writing files", POLICY, "Agent autonomy G0–G4; writes only inside the mission's worktree", ""),
        ("Internet research", POLICY, "Public addresses only; private networks only with ALLOW_PRIVATE_CRAWL", ""),
        ("Knowledge updates", POLICY, "Experience is screened with provenance; imports stay draft until a person "
         "approves them", ""),
        ("Workflow and playbook changes", POLICY, f"Experiments: held-out suite, no regressions, at least "
         f"+{st.min_improvement} points, a canary of {st.canary_window} tasks", "min_improvement"),
        ("Follow-up proposals", POLICY if st.auto_approve_proposals else HUMAN,
         "Approved by the owner policy, except project, rule and restricted proposals" if st.auto_approve_proposals
         else "A person decides each proposal", "auto_approve_proposals"),
        ("Merging a mission's result", POLICY if (st.auto_merge or not st.require_approval) else HUMAN,
         "Merged by the owner policy when the gate passes" if st.auto_merge else
         "Merged when the quality gate passes" if not st.require_approval else "A person approves each merge",
         "auto_merge"),
        ("Pushing to the remote", POLICY if st.auto_push else HUMAN,
         "Pushed after each merge (AUTO_PUSH)" if st.auto_push else "Only on /sync by a person", "auto_push"),
        ("Dependency changes", POLICY, f"Audited at the security stage ({st.dependency_audit}); a mission must permit "
         "them", "dependency_audit"),
        ("Agent definition rewrites", HUMAN, "A study rewrite is a candidate an administrator applies after the diff",
         ""),
        ("Model and route changes", HUMAN, "Routes are applied by an administrator; experiments test them first", ""),
        ("New tool permissions", HUMAN, "MCP tools: an administrator allows each tool and grants it per agent; a "
         "changed tool definition loses its allow", ""),
        ("Platform self-evolution", HUMAN, "Improvement contracts: fixed benchmark, approval, safe restart, "
         "monitoring and rollback", ""),
        ("Security boundaries and settings", HUMAN, "Administrators only, audited", ""),
        ("Production deployment, external submission, payments", HUMAN, "Never by agents: no production access", ""),
    ]
    return [{"activity": a, "level": lvl, "autonomous": lvl != HUMAN, "control": c, "setting": k}
            for a, lvl, c, k in rows]


def governance_overview(user: dict[str, Any], seen: set[int] | None = None) -> dict[str, Any]:
    from agent_company import approvals, governance, sandbox

    st = settings()
    t = now()
    day0 = t.replace(hour=0, minute=0, second=0, microsecond=0)
    queue = approvals.queue(user, seen)
    with session() as s:
        agents = list(s.scalars(select(Agent).where(Agent.enabled)))
        by_level = Counter(a.autonomy or "G3" for a in agents)
        perms = Counter(p for a in agents for p in (a.permissions or []))
        rooms = list(s.scalars(select(Room).order_by(Room.key)))
        spend_room = dict(s.execute(select(Agent.room_key, func.coalesce(func.sum(Run.cost), 0.0)).join(
            Agent, Agent.id == Run.agent_id).where(Run.started_at >= day0).group_by(Agent.room_key)).all())
        spend_agent = dict(s.execute(select(Run.agent_id, func.coalesce(func.sum(Run.cost), 0.0)).where(
            Run.started_at >= day0, Run.agent_id.is_not(None)).group_by(Run.agent_id)).all())
        live = [g for g in s.scalars(select(Goal).where(Goal.status.in_(LIVE), Goal.origin != "eval"))
                if seen is None or g.project_id is None or g.project_id in seen]
        spent = dict(s.execute(select(Run.goal_id, func.coalesce(func.sum(Run.cost), 0.0)).where(
            Run.goal_id.in_([g.id for g in live] or [-1])).group_by(Run.goal_id)).all())
        goal_rows = {g.id: g for g in live}
        audit_n = s.scalar(select(func.count()).select_from(AuditEvent)) if user.get("is_admin") else None
        audit_last = s.scalar(select(func.max(AuditEvent.created_at))) if user.get("is_admin") else None
        calls_24h = sum(len(tc or []) for (tc,) in s.execute(select(Run.tool_calls).where(
            Run.started_at >= t - timedelta(hours=24))))
        versions = s.scalar(select(func.count()).select_from(DefinitionCandidate).where(
            DefinitionCandidate.status == "applied")) or 0
    for x in queue:
        g = goal_rows.get(x["id"]) if x["kind"] in ("goal", "plan", "questions") else None
        if g is not None:
            x["spent_usd"] = round(float(spent.get(g.id, 0.0)), 6)
            x["budget_usd"] = g.budget_usd
            x["estimated_usd"] = g.estimated_cost_usd
    room_budgets = [{"key": r.key, "name": r.name, "daily_budget_usd": r.daily_budget_usd,
                     "today_usd": round(float(spend_room.get(r.key, 0.0)), 6)} for r in rooms if r.daily_budget_usd]
    quotas = [{"id": a.id, "name": a.name, "quota_usd": a.daily_cost_quota_usd,
               "today_usd": round(float(spend_agent.get(a.id, 0.0)), 6)} for a in agents if a.daily_cost_quota_usd]
    near = [{"id": g.id, "title": g.title, "budget_usd": g.budget_usd, "spent_usd": round(float(spent.get(g.id, 0)), 6)}
            for g in live if g.budget_usd and spent.get(g.id, 0) >= 0.8 * g.budget_usd]
    sb = sandbox.status()
    level = governance.stop_level()
    safety = [
        {"name": "Least-privilege permissions", "control": "Per-agent permissions and autonomy G0–G4",
         "state": f"{perms.get('write', 0)} write · {perms.get('shell', 0)} shell · {perms.get('network', 0)} network "
                  f"of {len(agents)} enabled agents", "ok": True},
        {"name": "Separate sandbox identities", "control": "L3 sandbox: allow-listed environment, data/ and .env "
         "hidden, the worktree's .git read-only", "state": f"sandbox {sb['mode']}"
         + ("" if sb["available"] else f" · unavailable: {sb['reason']}"), "ok": bool(sb["available"]) or sb["mode"] == "off"},
        {"name": "Network egress controls", "control": "No network in the L3 sandbox; pinned public addresses for "
         "fetches; Codex without web tools", "state": "on", "ok": True},
        {"name": "CPU, memory, time and concurrency limits", "control": "systemd scope limits, command timeout, "
         "parallel slots", "state": f"{sb['memory_mb']} MB · {sb['cpus']} CPUs · {st.command_timeout}s · "
         f"{st.max_parallel} parallel" + ("" if sb["limits"] else " · systemd limits unavailable"),
         "ok": bool(sb["limits"])},
        {"name": "Mission and team budgets", "control": "Goal budgets (cost, tokens, agents at once), daily room "
         "budgets and agent quotas", "state": (f"goal ${st.goal_budget_usd:g}" if st.goal_budget_usd else
                                               "no default goal budget") + f" · {len(room_budgets)} room budget(s) · "
         f"on exhaustion: {st.budget_exhausted_action}", "ok": bool(st.goal_budget_usd or room_budgets)},
        {"name": "Tool gateway on every call", "control": "executors.gate records and limits every tool call; MCP "
         "allow-list", "state": f"{calls_24h} tool calls recorded in 24 h", "ok": True},
        {"name": "Human approval with details and expiry", "control": "One approval queue; delegations expire; "
         f"blockers after {st.approval_blocker_hours} h", "state": f"{len(queue)} waiting", "ok": True},
        {"name": "Append-only audit", "control": "Every administrative action is audited",
         "state": f"{audit_n} records · last {_iso(audit_last) or '–'}" if audit_n is not None else "administrators",
         "ok": True},
        {"name": "Versioned releases and rollback", "control": "Definition, configuration, organisation and policy "
         "versions; experiment and improvement rollback", "state": f"{versions} definition rewrites applied · "
         f"auto-rollback {'on' if st.improvement_auto_rollback else 'off'}", "ok": True},
        {"name": "Global and per-mission emergency stop", "control": "E0–E5 stops from every page and the CLI",
         "state": f"stop {level} on" if level else "no stop on", "ok": not level},
    ]
    return {
        "matrix": autonomy_matrix(), "autonomy": {k: by_level.get(k, 0) for k in ("G0", "G1", "G2", "G3", "G4")},
        "permissions": dict(perms), "agents": len(agents),
        "approvals": queue, "budgets": {"rooms": room_budgets, "agents": quotas, "near_limit": near,
                                         "goal_budget_usd": st.goal_budget_usd, "goal_token_budget": st.goal_token_budget,
                                         "goal_agent_limit": st.goal_agent_limit,
                                         "on_exhaustion": st.budget_exhausted_action},
        "safety": safety, "stop_level": level,
    }


# --- Observability ------------------------------------------------------------------------------------------------

def observability(period: str = "24h", admin: bool = True) -> dict[str, Any]:
    from agent_company import sandbox, system

    span = PERIODS.get(period, PERIODS["24h"])
    end = now()
    start = end - span
    with session() as s:
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        procs = [{"pid": p.pid, "kind": p.kind, "program": p.program, "agent": names.get(p.agent_id or 0, ""),
                  "agent_id": p.agent_id, "goal_id": p.goal_id, "started_at": _iso(p.started_at)}
                 for p in s.scalars(select(LiveProcess).order_by(LiveProcess.started_at))]
        worktrees = [{"goal_id": g.id, "title": g.title, "status": g.status, "path": g.worktree}
                     for g in s.scalars(select(Goal).where(Goal.status.in_(LIVE), Goal.worktree != ""))]
        runs = list(s.execute(select(Run.model_name, Run.isolation, Run.ok, Run.cost, Run.duration,
                                     Run.input_tokens, Run.output_tokens, Run.tool_calls)
                              .where(Run.started_at >= start)))
        servers = [{"id": m.id, "name": m.name, "transport": m.transport, "enabled": m.enabled, "status": m.status,
                    "tools": sum(1 for x in m.tools or [] if isinstance(x, dict) and x.get("allowed")),
                    "last_error": (m.last_error or "")[:200], "tested_at": _iso(m.tested_at)}
                   for m in s.scalars(select(McpServer).order_by(McpServer.name))]
        mcp = dict(s.execute(select(McpCall.ok, func.count()).where(McpCall.created_at >= start)
                             .group_by(McpCall.ok)).all())
        docs = s.scalar(select(func.count()).select_from(Document).where(Document.created_at >= start)) or 0
        syncs = [(e.message, (e.data or {}).get("ok"), e.created_at) for e in s.scalars(
            select(Event).where(Event.kind == "source_sync", Event.created_at >= start).order_by(Event.id.desc()))]
        refused = s.scalar(select(func.count()).select_from(Event).where(
            Event.kind == "security", Event.created_at >= start)) or 0
    models: dict[str, dict[str, Any]] = {}
    iso = Counter()
    tools, fails = Counter(), Counter()
    for name, isolation, ok, cost, dur, tin, tout, calls in runs:
        m = models.setdefault(name or "unknown", {"name": name or "unknown", "runs": 0, "failed": 0, "tokens": 0,
                                                  "cost": 0.0, "seconds": 0.0})
        m["runs"] += 1
        m["failed"] += 0 if ok else 1
        m["tokens"] += (tin or 0) + (tout or 0)
        m["cost"] = round(m["cost"] + (cost or 0.0), 6)
        m["seconds"] = round(m["seconds"] + (dur or 0.0), 1)
        iso[isolation or "–"] += 1
        for c in calls or []:
            if isinstance(c, dict):
                tools[c.get("tool", "?")] += 1
                fails[c.get("tool", "?")] += c.get("ok") is False
    total_calls = sum(tools.values())
    out: dict[str, Any] = {
        "period": period if period in PERIODS else "24h", "since": _iso(start),
        "sandbox": sandbox.status() | {"processes": procs, "worktrees": worktrees, "isolation": dict(iso)},
        "tools": {"calls": total_calls, "failed": sum(fails.values()),
                  "failure_rate": round(sum(fails.values()) / total_calls, 3) if total_calls else None,
                  "top": [{"tool": k, "calls": n, "failed": fails[k]} for k, n in tools.most_common(12)],
                  "mcp_servers": servers, "mcp_calls": sum(mcp.values()), "mcp_failed": mcp.get(False, 0),
                  "refused": refused},
        "models": sorted(models.values(), key=lambda x: -x["tokens"]),
        "network": {"documents": docs, "syncs": len(syncs), "sync_failures": sum(1 for _, ok, _ in syncs if ok is False),
                    "recent_syncs": [{"message": m[:200], "ok": ok, "at": _iso(at)} for m, ok, at in syncs[:10]]},
    }
    if admin:
        out["host"] = system.status()["host"]
    else:
        out["sandbox"].pop("home", None)
        out["sandbox"].pop("toolchain", None)
    return out


# --- Mission contract ---------------------------------------------------------------------------------------------

DELIVERABLE_HEAD = "## Expected deliverable"


def mission_contract(goal_id: int) -> dict[str, Any]:
    """UI-G5-MI-02: a mission's contract read from the goal (G5-D-03): objective, deliverable, constraints, success
    criteria, required capabilities, risk and who must approve, with the rules in force for its project."""
    from agent_company import policies, sandbox

    st = settings()
    with session() as s:
        g = s.get(Goal, goal_id)
        if g is None:
            raise KeyError(goal_id)
        tasks = list(s.scalars(select(Task).where(Task.goal_id == g.id, Task.iteration == g.iteration)))
        desc = g.description or ""
        objective, _, rest = desc.partition(DELIVERABLE_HEAD)
        deliverable = rest.split("\n## ", 1)[0].strip()
        mode = sandbox.mode_for(g, s=s)
        plan_approval = bool(policies.effective("require_plan_approval", g.project_id, s=s))
        merge_approval = bool(policies.effective("require_approval", g.project_id, s=s))
        acceptance = sum(len((t.contract or {}).get("acceptance") or []) for t in tasks)
        roles = sorted({t.role for t in tasks} | set((g.strategy or {}).get("roles") or []))
    constraints = [f"at most {g.max_iterations} iteration(s)"]
    if g.allowed_paths:
        constraints.append("changes only in " + ", ".join(g.allowed_paths))
    if g.protect_tests:
        constraints.append("existing tests protected")
    constraints.append("change boundaries lifted: " + ", ".join(g.permits) if g.permits
                       else "no dependency or configuration changes unless permitted")
    constraints.append(f"sandbox {mode}")
    if g.max_active_agents:
        constraints.append(f"at most {g.max_active_agents} agents at once")
    if g.classification and g.classification != "internal":
        constraints.append(f"classification {g.classification}")
    criteria = [f"`{c}` passes" for c in g.quality_commands or []] or ["the quality gate's checks pass"]
    if acceptance:
        criteria.append(f"{acceptance} acceptance check(s) of this iteration's tasks")
    criteria += [f"stage {x} passes" for x in (g.test_strategy or {}).get("mandatory") or []
                 if isinstance(x, str)]
    held = g.origin in ("a2a", "github", "improvement")
    approvals = []
    if plan_approval:
        approvals.append("a person approves the plan before work starts")
    if held:
        approvals.append(f"a person approves the merge (work from {g.origin} always waits)")
    elif st.auto_merge:
        approvals.append("merged by the owner policy once the gate passes")
    elif merge_approval:
        approvals.append("a person approves the merge")
    else:
        approvals.append("merged when the quality gate passes")
    approvals.append("decisions marked for a person block the merge")
    ts = g.test_strategy or {}
    return {"objective": objective.strip()[:2000] or g.title, "deliverable": deliverable[:2000],
            "deadline": _iso(g.deadline), "constraints": constraints, "success_criteria": criteria,
            "capabilities": roles, "risk": {"level": ts.get("risk") or "", "reasons": ts.get("reasons") or []},
            "approvals": approvals, "status": g.status, "sandbox": mode}
