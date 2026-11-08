"""Owner policy (goal 251): auto_approve_proposals and auto_merge, both off by default (S14-R-02, S04-R-09, S01-R-05)."""

import json

import pytest
from sqlalchemy import select

from agent_company import decisions, executors, loop, ownerpolicy, policies, prefs, projects
from agent_company.db import AuditEvent, Goal, Notification, Proposal, session


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def origin(pid, status="failed"):
    gid = loop.create_goal("Origin work", project_id=pid)
    with session() as s:
        s.get(Goal, gid).status = status
    return gid


def propose(gid, pid, title, kind="follow_up", description=""):
    with session() as s:
        p = Proposal(goal_id=gid, project_id=pid, title=title, description=description, kind=kind)
        s.add(p)
        s.flush()
        return p.id


def prop(pid):
    with session() as s:
        p = s.get(Proposal, pid)
        return p.status, p.decided_by, p.spawned_goal_id


def audits(actor):
    with session() as s:
        return [(a.action, a.target, a.after) for a in s.scalars(select(AuditEvent).where(AuditEvent.actor == actor))]


def status(gid):
    with session() as s:
        return s.get(Goal, gid).status


def test_both_settings_are_off_by_default_and_versioned():
    assert {"auto_approve_proposals", "auto_merge"} <= set(policies.GOVERNANCE_KEYS)
    keys = {d["key"]: d for d in prefs.describe()}
    assert keys["auto_approve_proposals"]["value"] is False and keys["auto_merge"]["value"] is False


def test_settings_off_keep_proposals_pending_and_goals_waiting():
    pid = projects.create("Off")["id"]
    gid = origin(pid)
    prop_id = propose(gid, pid, "Add regression tests")
    projects.settle_proposals(gid)
    assert prop(prop_id)[0] == "pending"
    assert audits("policy:auto-approve") == []

    goal = loop.create_goal("Waits", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(goal)["status"] == "awaiting_approval"
    assert ownerpolicy.on_awaiting_approval(goal) is None
    assert status(goal) == "awaiting_approval" and audits("policy:auto-merge") == []


def test_follow_up_is_approved_and_audited():
    prefs.set_value("auto_approve_proposals", True)
    pid = projects.create("On")["id"]
    gid = origin(pid)
    prop_id = propose(gid, pid, "Add regression tests")
    projects.settle_proposals(gid)
    st, by, spawned = prop(prop_id)
    assert st == "approved" and by == "auto-approve (owner policy)" and spawned
    with session() as s:
        child = s.get(Goal, spawned)
        assert child.origin == "proposal" and child.parent_goal_id == gid
    assert ("proposal_auto_approved", f"proposal:{prop_id}") in [(a, t) for a, t, _ in audits("policy:auto-approve")]


def test_restricted_rule_and_project_proposals_stay_pending():
    prefs.set_value("auto_approve_proposals", True)
    pid = projects.create("Held")["id"]
    gid = origin(pid)
    ids = [propose(gid, pid, "Widen the developer permission set"),
           propose(gid, pid, "Rule: run the tests first", kind="rule"),
           propose(gid, pid, "A new product", kind="product")]
    out = ownerpolicy.approve_pending(gid)
    assert sorted(h["id"] for h in out["held"]) == ids and not out["approved"]
    assert [prop(i)[0] for i in ids] == ["pending"] * 3
    assert {a for a, _, _ in audits("policy:auto-approve")} == {"proposal_auto_held"}
    texts = [m["content"] for m in projects.messages(pid)]
    assert any(t.startswith("Proposal stays pending for a person (it touches authority") for t in texts)
    with session() as s:
        assert s.scalars(select(Notification).where(Notification.source == "proposals")).first() is not None


def test_a_chain_guard_leaves_the_proposal_pending_not_rejected():
    prefs.set_value("auto_approve_proposals", True)
    prefs.set_value("max_proposal_goals", 0)
    pid = projects.create("Guard")["id"]
    gid = origin(pid)
    prop_id = propose(gid, pid, "Add regression tests")
    out = ownerpolicy.approve_pending(gid)
    assert out["held"][0]["reason"] == "limit of 0 proposal goals reached"
    assert prop(prop_id) == ("pending", "", None)
    # a person's decision still rejects it as before
    assert projects.decide(prop_id, True, "admin")["status"] == "rejected"


def test_a_parent_budget_that_refuses_the_goal_leaves_the_proposal_pending(monkeypatch):
    prefs.set_value("auto_approve_proposals", True)
    pid = projects.create("Budget")["id"]
    gid = origin(pid)
    prop_id = propose(gid, pid, "Add regression tests")

    def refuse(*a, **kw):
        raise ValueError("the parent goal's cost budget is used")

    monkeypatch.setattr(loop, "create_goal", refuse)
    out = ownerpolicy.approve_pending(ids=[prop_id])
    assert out["held"][0]["reason"] == "the parent goal's cost budget is used"
    assert prop(prop_id) == ("pending", "", None)


def test_an_exact_title_duplicate_of_an_open_goal_is_rejected():
    prefs.set_value("auto_approve_proposals", True)
    pid = projects.create("Dup")["id"]
    gid = origin(pid)
    open_goal = loop.create_goal("Add docs", project_id=pid)
    first = propose(gid, pid, "  add   DOCS ")
    other = propose(gid, pid, "Write a changelog")
    again = propose(gid, pid, "write a CHANGELOG")
    out = ownerpolicy.approve_pending(gid)
    assert prop(first)[0] == "rejected" and f"duplicate of goal #{open_goal}" in prop(first)[1]
    assert prop(other)[0] == "approved"
    assert prop(again)[0] == "rejected" and f"duplicate of goal #{prop(other)[2]}" in prop(again)[1]
    assert [r["id"] for r in out["rejected"]] == [first, again]


def test_a_duplicate_of_an_older_pending_proposal_is_rejected():
    pid = projects.create("Dup2")["id"]
    gid = origin(pid)
    older = propose(gid, pid, "Refactor the parser")
    newer = propose(gid, pid, "refactor the  parser")
    out = ownerpolicy.approve_pending(ids=[newer])
    assert out["rejected"][0]["reason"] == f"duplicate of proposal #{older}"
    assert prop(older)[0] == "pending" and prop(newer)[0] == "rejected"


def test_auto_merge_merges_a_goal_that_passed_the_gate():
    prefs.set_value("auto_merge", True)
    gid = loop.create_goal("Merge me", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "approved"
    assert status(gid) == "approved"
    [(action, target, after)] = audits("policy:auto-merge")
    assert action == "goal_auto_merged" and target == f"goal:{gid}" and after["merge_commit"]


def test_on_awaiting_approval_merges_directly():
    gid = loop.create_goal("Merge later", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    prefs.set_value("auto_merge", True)
    assert ownerpolicy.on_awaiting_approval(gid)["merged"] is True
    assert status(gid) == "approved"


def test_a_decision_for_a_person_keeps_the_goal_waiting(monkeypatch):
    def plan(self, req):
        data = executors.standard_plan(req.data.get("goal", "g"))
        data["decisions"] = [{"topic": "Database", "alternatives": ["SQLite", "PostgreSQL"],
                              "recommendation": "SQLite", "reasoning": "one host", "confidence": 0.7,
                              "category": "technical", "requires_human_approval": True}]
        return json.dumps(data)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    prefs.set_value("auto_merge", True)
    gid = loop.create_goal("Decide", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    assert status(gid) == "awaiting_approval" and decisions.blocking(gid)
    [(action, _, after)] = audits("policy:auto-merge")
    assert action == "goal_auto_merge_refused" and "Database" in after["reason"]
    with session() as s:
        n = s.scalars(select(Notification).where(Notification.title == f"Automatic merge of goal #{gid} refused")).one()
        assert "decision(s) waiting for a person" in n.body


def test_an_a2a_goal_is_never_auto_merged():
    prefs.set_value("auto_merge", True)
    gid = loop.create_goal("Outside work", origin="a2a")
    with session() as s:
        s.get(Goal, gid).status = "awaiting_approval"
    assert ownerpolicy.on_awaiting_approval(gid) is None
    assert status(gid) == "awaiting_approval" and audits("policy:auto-merge") == []
