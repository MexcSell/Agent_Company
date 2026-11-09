"""Owner policy (the owner's standing order of 2026-11-08: "approve everything"), two DB settings, both off by default.

auto_approve_proposals  pending follow-up proposals are approved as 'auto-approve (owner policy)' through
                        `projects.decide(by_human=False, hold=True)`: product, project and rule proposals and those
                        `projects.restricted` matches stay pending for a person (S14-R-02, SEC-007), so do those the
                        chain guards or the parent's budget (S04-R-09) refuse; an exact-title duplicate of an open
                        goal or an older pending proposal is rejected.
auto_merge              a goal that reaches awaiting_approval is merged by `loop.approve` as 'auto-merge (owner
                        policy)'; whatever approve() refuses (decisions for a person, critical defects,
                        selfevolve.merge_block, conflicts) leaves it waiting, with a notification. A2A and eval goals
                        are never merged this way. Push follows AUTO_PUSH inside approve().

Each action is audited as policy:auto-approve / policy:auto-merge and posted in the project's command window.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from agent_company import governance, workflow
from agent_company.config import settings
from agent_company.db import Goal, Proposal, log_event, session

APPROVER = "auto-approve (owner policy)"
MERGER = "auto-merge (owner policy)"
OPEN = (*workflow.ACTIVE, "awaiting_approval")


def _norm(title: str) -> str:
    return " ".join((title or "").split()).casefold()


def _duplicate(s, p: Proposal, project_id: int | None) -> str:  # noqa: ANN001
    """'goal #N' or 'proposal #N' when an open goal or an older pending proposal of the project has p's title."""
    title = _norm(p.title)
    for gid, gtitle in s.execute(select(Goal.id, Goal.title).where(Goal.project_id == project_id,
                                                                    Goal.status.in_(OPEN)).order_by(Goal.id)):
        if _norm(gtitle) == title:
            return f"goal #{gid}"
    for oid, otitle in s.execute(select(Proposal.id, Proposal.title).where(
            Proposal.project_id == project_id, Proposal.status == "pending", Proposal.id < p.id).order_by(Proposal.id)):
        if _norm(otitle) == title:
            return f"proposal #{oid}"
    return ""


def approve_pending(goal_id: int | None = None, ids: list[int] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Decide the pending proposals of `goal_id` (or exactly `ids`) under the owner policy. Never raises."""
    from agent_company import projects

    out: dict[str, list[dict[str, Any]]] = {"approved": [], "rejected": [], "held": []}
    with session() as s:
        query = select(Proposal).where(Proposal.status == "pending")
        if goal_id is not None:
            query = query.where(Proposal.goal_id == goal_id)
        if ids is not None:
            query = query.where(Proposal.id.in_(list(ids) or [-1]))
        rows = [(p.id, p.goal_id, p.project_id, p.title or "", p.description or "", p.kind or "follow_up")
                for p in s.scalars(query.order_by(Proposal.id))]
    for pid, gid, project_id, title, why, kind in rows:
        try:
            outcome, reason, spawned = _decide_one(projects, pid, gid, project_id, title, why, kind)
        except Exception as e:  # noqa: BLE001 - the policy never breaks the caller (a goal's end, an analysis)
            outcome, reason, spawned = "held", f"{type(e).__name__}: {e}", None
        out[outcome].append({"id": pid, "title": title, "reason": reason, "spawned_goal_id": spawned})
        try:
            governance.audit("policy:auto-approve", f"proposal_auto_{outcome}", f"proposal:{pid}",
                             after={"reason": reason, "spawned_goal_id": spawned})
            if outcome == "held":
                text = f"Proposal stays pending for a person ({reason}): {title}" + (f"\n{why}" if why else "")
                if gid:
                    projects.post_for_goal(gid, text, kind="proposal", proposal_id=pid)
                elif project_id:
                    projects.post(project_id, text, kind="proposal", proposal_id=pid)
        except Exception as e:  # noqa: BLE001
            log_event("error", f"owner policy record of proposal {pid}: {type(e).__name__}: {e}", goal_id=gid)
    if out["held"]:
        try:
            governance.notify(f"{len(out['held'])} proposal(s) need a person's decision",
                              "; ".join(f"#{h['id']} {h['title']} ({h['reason']})" for h in out["held"]),
                              level="warning", source="proposals", goal_id=goal_id, admin_only=True)
        except Exception as e:  # noqa: BLE001
            log_event("error", f"owner policy notification: {type(e).__name__}: {e}", goal_id=goal_id)
    return out


def _decide_one(projects: Any, pid: int, gid: int | None, project_id: int | None, title: str, why: str,
                kind: str) -> tuple[str, str, int | None]:
    if kind in projects.CASE_KINDS:
        return "held", "a new product or project", None
    if kind == "rule":
        return "held", "a project rule", None
    if projects.restricted(title, why):
        return "held", "it touches authority or safety controls", None
    with session() as s:
        if project_id is None and gid is not None:
            g = s.get(Goal, gid)
            project_id = g.project_id if g else None
        dup = _duplicate(s, s.get(Proposal, pid), project_id)
    try:
        if dup:
            projects.decide(pid, False, f"{APPROVER}: duplicate of {dup}", by_human=False)
            return "rejected", f"duplicate of {dup}", None
        res = projects.decide(pid, True, APPROVER, by_human=False, hold=True)
    except (PermissionError, ValueError) as e:
        return "held", str(e), None
    if res.get("held"):
        return "held", str(res["held"]), None
    if res.get("status") != "approved":
        return "held", f"not approved ({res.get('status')})", None
    return "approved", "", res.get("spawned_goal_id")


def on_awaiting_approval(goal_id: int) -> dict[str, Any] | None:
    """Merge a goal that reached awaiting_approval when `auto_merge` is on. None when the policy does not apply;
    {'merged': bool, ...} otherwise. A refusal leaves the goal waiting for a person. Never raises."""
    try:
        if not settings().auto_merge:
            return None
        with session() as s:
            g = s.get(Goal, goal_id)
            if g is None or g.origin in ("a2a", "eval") or g.status != "awaiting_approval":
                return None
            project_id = g.project_id
    except Exception as e:  # noqa: BLE001
        log_event("error", f"owner policy auto-merge check: {type(e).__name__}: {e}", goal_id=goal_id)
        return None
    from agent_company import loop, projects

    try:
        res = loop.approve(goal_id, actor=MERGER)
    except Exception as e:  # noqa: BLE001 - the goal stays awaiting approval for a person
        reason = str(e) or type(e).__name__
        try:
            governance.audit("policy:auto-merge", "goal_auto_merge_refused", f"goal:{goal_id}",
                             after={"reason": reason})
            governance.notify(f"Automatic merge of goal #{goal_id} refused", reason, level="warning",
                              source="goal", goal_id=goal_id, project_id=project_id)
            projects.post_for_goal(goal_id, f"The owner policy (auto_merge) did not merge this goal: {reason}. "
                                            "It waits for a person's approval.", kind="result")
        except Exception as e2:  # noqa: BLE001
            log_event("error", f"owner policy refusal record: {type(e2).__name__}: {e2}", goal_id=goal_id)
        return {"merged": False, "reason": reason}
    sha = res.get("merge_commit", "")
    try:
        governance.audit("policy:auto-merge", "goal_auto_merged", f"goal:{goal_id}", after={"merge_commit": sha})
        projects.post_for_goal(goal_id, "Merged automatically by the owner policy (auto_merge).", kind="result")
    except Exception as e:  # noqa: BLE001
        log_event("error", f"owner policy merge record: {type(e).__name__}: {e}", goal_id=goal_id)
    return {"merged": True, "merge_commit": sha}


TRANSIENT_REFUSAL = ("has uncommitted changes", "switch it to")  # worktrees.merge: the checkout, not the work


def retry_refused() -> list[int]:
    """Merge again the goals whose merge was refused only because the repository's checkout had uncommitted changes
    or was on another branch, once that checkout is clean and on the base branch: with `auto_merge` through
    `on_awaiting_approval`, otherwise only goals that needed no approval (their 'automatic merge refused' event).
    Called every minute by the ScheduleThread; returns the goal ids merged."""
    from agent_company import loop, worktrees
    from agent_company.db import Event

    with session() as s:
        waiting = [(g.id, g.repo_path, g.base_branch, g.origin) for g in s.scalars(
            select(Goal).where(Goal.status == "awaiting_approval").order_by(Goal.id))]
        found = []
        for gid, repo, base, origin in waiting:
            if origin in ("a2a", "eval") or not repo:
                continue
            last = s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "goal")
                             .order_by(Event.id.desc()).limit(1)).first()
            if last is None or "merge refused" not in last.message \
                    or not any(x in last.message for x in TRANSIENT_REFUSAL):
                continue
            auto = s.scalars(select(Event.id).where(Event.goal_id == gid, Event.kind == "goal",
                                                    Event.message.like("automatic merge refused%"))).first()
            found.append((gid, repo, base, auto is not None))
    merged: list[int] = []
    for gid, repo, base, auto in found:
        if not (settings().auto_merge or auto):
            continue
        try:
            if worktrees.git(repo, "rev-parse", "--abbrev-ref", "HEAD") != base \
                    or worktrees.git(repo, "status", "--porcelain", "--untracked-files=no"):
                continue  # still dirty or switched: try again next minute, without another refusal
        except Exception:  # noqa: BLE001 - a missing repository stays for a person
            continue
        if settings().auto_merge:
            res = on_awaiting_approval(gid) or {}
            ok, reason = bool(res.get("merged")), str(res.get("reason") or "")
        else:
            try:
                loop.approve(gid, actor=loop.AUTO_ACTOR)
                ok, reason = True, ""
            except Exception as e:  # noqa: BLE001 - it waits for a person
                ok, reason = False, str(e) or type(e).__name__
        if not ok and not any(x in reason for x in TRANSIENT_REFUSAL):
            # another reason now (a decision, a critical defect): no more retries, one refusal is enough
            log_event("goal", f"merge retry stopped: {reason}"[:1000], goal_id=gid)
        if ok:
            log_event("goal", "merged on retry: the repository's checkout is clean again", goal_id=gid)
            merged.append(gid)
    return merged
