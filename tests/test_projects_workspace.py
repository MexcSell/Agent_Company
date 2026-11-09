"""AICO UI-PROJ-01/02/05: the portfolio, the Tasks board's columns and the registration fields."""

import pytest
from fastapi.testclient import TestClient

from agent_company import api, dashboard, projects, requirements
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Goal, Task, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


def client():
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    app.dependency_overrides[api.admin_user] = lambda: ADMIN
    return TestClient(app)


def test_registration_stores_outcome_strategy_brief_and_documents():
    c = client()
    first = c.post("/api/projects", json={"name": "Base"}).json()
    up = c.post("/api/uploads", files={"file": ("brief.txt", b"the brief", "text/plain")}).json()
    r = c.post("/api/projects", json={"name": "Shop", "description": "An online shop",
                                      "requirements": "Customers pay by card.", "attachments": [up["id"]],
                                      "target_outcome": "First sale in March", "related_project_id": first["id"],
                                      "team_strategy": "reuse"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["target_outcome"], p["related_project_id"], p["team_strategy"]) == ("First sale in March", first["id"],
                                                                                   "reuse")
    reqs = requirements.list_requirements(p["id"])
    assert [x["text"] for x in reqs] == ["Customers pay by card."] and reqs[0]["status"] == "draft"
    docs = [m for m in projects.messages(p["id"]) if m["attachments"]]
    assert docs and docs[0]["attachments"][0]["name"] == "brief.txt"
    assert c.post("/api/projects", json={"name": "Bad", "team_strategy": "random"}).status_code == 400
    assert c.post("/api/projects", json={"name": "Bad2", "related_project_id": 9999}).status_code == 400
    out = c.patch(f"/api/projects/{p['id']}", json={"team_strategy": "new", "target_outcome": "Ten sales"}).json()
    assert out["team_strategy"] == "new" and out["target_outcome"] == "Ten sales"


@pytest.mark.parametrize("status,stage,deps,col", [
    ("queued", "planned", True, "ready"), ("queued", "planned", False, "waiting"), ("queued", "created", True, "queued"),
    ("running", "in_progress", True, "running"), ("running", "review", True, "running"), ("queued", "blocked", True, "blocked"),
    ("failed", "failed", True, "failed"), ("done", "approved", True, "succeeded"), ("done", "completed", True, "succeeded"),
    ("skipped", "skipped", True, "cancelled"), ("queued", "cancelled", True, "cancelled"), ("skipped", "blocked", True, "blocked")])
def test_board_columns(status, stage, deps, col):
    assert dashboard.board_column(status, stage, deps) == col


def test_portfolio_and_project_tasks():
    p = projects.create("Board")
    with session() as s:
        g = Goal(title="Build", description="", repo_path="", project_id=p["id"], status="running", iteration=1)
        old = Goal(title="Bench", description="", repo_path="", project_id=p["id"], status="approved", origin="eval")
        s.add_all([g, old])
        s.flush()
        s.add_all([Task(goal_id=g.id, iteration=1, key="a", title="A", role="developer", status="done", stage="approved"),
                   Task(goal_id=g.id, iteration=1, key="b", title="B", role="qa", status="queued", stage="planned",
                        depends_on=["a"]),
                   Task(goal_id=g.id, iteration=1, key="c", title="C", role="qa", status="queued", stage="planned",
                        depends_on=["b"]),
                   Task(goal_id=old.id, iteration=0, key="x", title="X", role="developer", status="done")])
    c = client()
    rows = c.get(f"/api/projects/{p['id']}/tasks").json()
    assert {r["key"]: r["column"] for r in rows} == {"a": "succeeded", "b": "ready", "c": "waiting"}
    port = {x["id"]: x for x in c.get("/api/projects-portfolio").json()}
    me = port[p["id"]]
    assert me["phase"] == "active" and me["tasks_done"] == 1 and me["tasks_total"] == 3 and me["goals"] == 1
    assert me["manager"] and me["last_activity"]
    with session() as s:
        s.get(Goal, rows[0]["goal_id"]).status = "approved"
    projects.set_status(p["id"], "archived", "test")
    assert next(x for x in c.get("/api/projects-portfolio").json() if x["id"] == p["id"])["phase"] == "archived"
