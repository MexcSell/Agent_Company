"""UI-GM-05 (FR-003.7): an order's id and what it led to — the goal it started and its follow-ups, their requirements,
tasks, decisions, merge and release record — from stored records only."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import auth, loop, projects
from agent_company.api import create_app
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Decision, Deliverable, Message, Requirement, RequirementLink, Task, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    auth.register("admin", secrets.token_urlsafe(32))


def test_an_order_names_its_goal_and_the_trace_follows_its_follow_ups():
    p = projects.create("Traced")["id"]
    out = projects.command(p, ADMIN, "Add an export\nDECISION: CSV first :: customers ask for it")
    mid, gid = out["message_id"], out["goal_id"]
    with session() as s:
        assert s.get(Message, mid).goal_id == gid, "the order message is linked to its goal"
    child = loop.create_goal("Add export tests", project_id=p, parent_goal_id=gid, origin="proposal")
    other = loop.create_goal("Unrelated", project_id=p)
    with session() as s:
        req = Requirement(project_id=p, key="REQ-007", text="Export the data")
        s.add(req)
        s.flush()
        s.add(RequirementLink(requirement_id=req.id, kind="task", ref="task:1", goal_id=child))
        s.add(Task(goal_id=gid, iteration=1, key="t1", title="Write it", role="developer", status="done"))
        s.add(Task(goal_id=gid, iteration=1, key="t2", title="Review it", role="qa", status="failed"))
        s.add(Deliverable(project_id=p, goal_id=gid, merge_commit="abc1234def", approved_by="admin"))
        s.add(Decision(goal_id=child, project_id=p, topic="Use the csv module"))
    t = projects.trace(mid, ADMIN)
    assert [g["id"] for g in t["goals"]] == [gid, child] and other not in [g["id"] for g in t["goals"]]
    root, follow = t["goals"]
    assert root["tasks"] == {"done": 1, "failed": 1} and root["release"]["merge_commit"] == "abc1234def"
    assert follow["parent_goal_id"] == gid and follow["requirements"] == ["REQ-007"] and follow["release"] is None
    assert [d["topic"] for d in follow["decisions"]] == ["Use the csv module"]
    assert [d["topic"] for d in t["decisions"]] == ["CSV first"], "the decision the order itself recorded"


def test_older_orders_find_their_goal_through_the_reply_but_never_the_next_orders():
    p = projects.create("Legacy")["id"]
    gid = loop.create_goal("Old order", project_id=p)
    first = projects.post(p, "Old order", "user", "command")
    second = projects.post(p, "Another order", "user", "command")
    projects.post(p, f"{projects.ORDER_REPLY}{gid}. Planning now.", "agent", goal_id=gid)
    assert projects.trace(second, ADMIN)["goals"][0]["id"] == gid
    assert projects.trace(first, ADMIN)["goals"] == [], "the reply after the next order is that order's"


def test_the_trace_needs_the_project_to_be_visible():
    p = projects.create("Hidden")["id"]
    mid = projects.command(p, ADMIN, "Secret order")["message_id"]
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.app.dependency_overrides.clear()
        from agent_company import api

        app.app.dependency_overrides[api.current_user] = lambda: ADMIN
        assert app.get(f"/api/messages/{mid}/trace").json()["goals"][0]["title"] == "Secret order"
        guest = {"id": 9, "name": "guest", "is_admin": False, "permissions": dict(DEFAULT_PERMISSIONS)}
        app.app.dependency_overrides[api.current_user] = lambda: guest
        assert app.get(f"/api/messages/{mid}/trace").status_code == 404
        assert app.get("/api/messages/99999/trace").status_code == 404
