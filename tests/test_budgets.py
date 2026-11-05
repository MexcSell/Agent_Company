"""Goal budget dimensions and their allocation to child goals (S04-R-09)."""

import secrets
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import budgets, loop, projects, usage
from agent_company.api import create_app
from agent_company.db import Event, Goal, Proposal, Task, session
from agent_company.scheduler import run_dag


def spend(gid, cost=0.0, tokens=0):
    usage.record(model_name="offline", kind="api", cost=cost, output_tokens=tokens, goal_id=gid)


def set_status(gid, status):
    with session() as s:
        s.get(Goal, gid).status = status


def test_a_child_goal_asking_for_more_than_the_parents_remainder_is_capped():
    parent = loop.create_goal("Parent", budget_usd=1.0)
    spend(parent, 0.4)
    child = loop.create_goal("Child", parent_goal_id=parent, origin="proposal", budget_usd=5.0)
    with session() as s:
        assert s.get(Goal, child).budget_usd == pytest.approx(0.6)
        ev = s.scalars(select(Event).where(Event.goal_id == parent, Event.kind == "budget")).first()
        assert ev.data["requested"] == 5.0 and ev.data["granted"] == pytest.approx(0.6)
    assert budgets.remaining(parent) == 0  # the running child holds the rest
    with pytest.raises(ValueError, match="budget is used"):
        loop.create_goal("Grandchild's sibling", parent_goal_id=parent, origin="proposal")


def test_a_child_without_its_own_budget_gets_the_parents_remainder_and_inherits_nothing_unlimited():
    parent = loop.create_goal("Parent", budget_usd=2.0, budget_tokens=1000)
    child = loop.create_goal("Child", parent_goal_id=parent, origin="proposal")
    with session() as s:
        g = s.get(Goal, child)
        assert (g.budget_usd, g.budget_tokens) == (2.0, 1000)
    free = loop.create_goal("Free")
    with session() as s:
        assert s.get(Goal, loop.create_goal("Kid", parent_goal_id=free)).budget_usd == 0  # no limit to share


def test_a_finished_childs_unused_budget_returns_to_the_parent():
    parent = loop.create_goal("Parent", budget_usd=1.0)
    child = loop.create_goal("Child", parent_goal_id=parent, origin="proposal", budget_usd=0.5)
    spend(child, 0.1)
    assert budgets.remaining(parent) == pytest.approx(0.5)
    set_status(child, "awaiting_approval")
    assert budgets.remaining(parent) == pytest.approx(0.9)  # only what the child used stays counted


def test_a_parent_with_nothing_left_records_the_proposal_but_spawns_no_goal():
    parent = loop.create_goal("Parent", budget_usd=0.5)
    spend(parent, 0.5)
    with session() as s:
        p = Proposal(goal_id=parent, title="More work", description="why")
        s.add(p)
        s.flush()
        pid = p.id
    out = projects.decide(pid, True, "user:admin")
    assert out["status"] == "rejected" and "budget is used" in out["decided_by"]
    with session() as s:
        assert s.get(Proposal, pid).spawned_goal_id is None
        assert not list(s.scalars(select(Goal).where(Goal.parent_goal_id == parent)))


def test_an_exhausted_token_budget_pauses_the_goal():
    gid = loop.create_goal("Tokens", quality_commands=["test -f WORKLOG.md"], budget_tokens=100)
    spend(gid, tokens=150)
    out = loop.run_goal(gid)
    assert out["status"] == "paused" and "token budget" in out["budget"]
    with pytest.raises(ValueError, match="over budget"):
        loop.resume(gid, actor="user:admin")


def test_the_parents_budget_counts_what_its_children_use():
    parent = loop.create_goal("Parent", quality_commands=["test -f WORKLOG.md"], budget_usd=1.0)
    child = loop.create_goal("Child", parent_goal_id=parent, origin="proposal")
    spend(child, 1.0)
    assert loop.run_goal(parent)["status"] == "paused"


def test_max_agents_caps_how_many_tasks_run_at_once():
    gid = loop.create_goal("Parallel")
    with session() as s:
        rows = [Task(goal_id=gid, iteration=1, key=f"t{i}", title=f"t{i}", role="qa") for i in range(4)]
        s.add_all(rows)
        s.flush()
        ids = [t.id for t in rows]
    lock, now, peak = threading.Lock(), [0], [0]

    def worker(tid):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.05)
        with lock:
            now[0] -= 1
        return True

    assert set(run_dag(ids, worker, "/tmp/none", max_agents=2).values()) == {"done"}
    assert peak[0] == 2


def test_budget_changes_are_for_administrators_audited_and_children_stay_within_the_parent():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        parent = loop.create_goal("Parent", budget_usd=1.0)
        child = loop.create_goal("Child", parent_goal_id=parent, origin="proposal", budget_usd=0.4)
        r = app.post(f"/api/goals/{child}/budget", json={"budget_usd": 0.9})
        assert r.status_code == 200 and r.json()["own"]["budget_usd"] == 0.9
        assert app.post(f"/api/goals/{child}/budget", json={"budget_usd": 1.5}).status_code == 400
        r = app.post(f"/api/goals/{parent}/budget", json={"budget_tokens": 5000, "max_active_agents": 2}).json()
        assert r["limits"]["tokens"] == 5000 and r["limits"]["agents"] == 2
        assert app.get(f"/api/goals/{parent}").json()["budget"]["remaining"]["cost_usd"] == pytest.approx(0.1)
        assert "goal_budget" in {a["action"] for a in app.get("/api/audit").json()["records"]}


def test_a_rooms_daily_budget_covers_its_sub_rooms_and_pauses_the_goals_that_need_them():
    """S04-R-09 room budgets: company → department room → its rooms; used up, their agents take no work today."""
    from sqlalchemy import select

    from agent_company import organization, registry, usage
    from agent_company.db import Agent

    organization.create_room("engineering", "Engineering", "development", actor="test")
    organization.update_room("development", {"parent_key": "engineering"}, "test")
    with pytest.raises(ValueError, match="daily budget"):
        organization.update_room("engineering", {"daily_budget_usd": -1}, "test")
    organization.update_room("engineering", {"daily_budget_usd": 1.0}, "test")
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.role == "developer", Agent.parent_id.is_(None))).first().id
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first().id
    usage.record(model_name="Offline simulator", kind="offline", cost=0.7, agent_id=dev)
    usage.record(model_name="Offline simulator", kind="offline", cost=5.0, agent_id=gm)  # another department
    eng = next(r for r in registry.list_rooms() if r["key"] == "engineering")
    assert eng["daily_budget_usd"] == 1.0 and eng["spent_today_usd"] == 0.7
    assert registry.agent_detail(dev)["quota_reason"] == ""
    usage.record(model_name="Offline simulator", kind="offline", cost=0.4, agent_id=dev)
    assert "room Engineering used $1.10 of its $1.00 daily budget" in registry.agent_detail(dev)["quota_reason"]
    with pytest.raises(registry.QuotaExceeded, match="daily budget"):
        registry.staff("qa", goal_id=loop.create_goal("check"))  # QA sits in the same department
    assert registry.agent_detail(gm)["quota_reason"] == ""

    gid = loop.create_goal("Write the worklog", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert out["status"] == "paused" and g.checkpoint["paused_for"] == "quota"
    assert "daily budget" in g.checkpoint["paused_why"]
    organization.update_room("engineering", {"daily_budget_usd": 0}, "test")  # raised (removed): it may run again
    assert registry.agent_detail(dev)["quota_reason"] == ""
