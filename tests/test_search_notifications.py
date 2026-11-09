"""AICO UI-SRCH-01 (one search, filtered by the user's projects) and UI-NOTE-01 (notification categories)."""

from fastapi.testclient import TestClient

from agent_company import api, governance, projects, worktrees
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Goal, Task, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
OTHER = {"id": 7, "name": "other", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": []}}


def client(user):
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: user
    return TestClient(app)


def test_search_groups_and_permissions():
    p = projects.create("Zebra shop")
    (__import__("pathlib").Path(p["path"]) / "zebra_checkout.py").write_text("x = 1\n")
    worktrees.git(p["path"], "add", "zebra_checkout.py")
    worktrees.git(p["path"], "commit", "-m", "add", check=False)
    with session() as s:
        g = Goal(title="Zebra payments", description="", repo_path="", project_id=p["id"])
        s.add(g)
        s.flush()
        s.add(Task(goal_id=g.id, iteration=1, key="z", title="Wire the zebra API", role="developer"))
        gid = g.id
    r = client(ADMIN).get("/api/search?q=zebra").json()
    kinds = {g["kind"]: g for g in r["groups"]}
    assert {"project", "goal", "task", "file"} <= set(kinds)
    assert kinds["goal"]["items"][0]["href"] == f"#/goals/{gid}"
    assert kinds["file"]["items"][0]["label"] == "zebra_checkout.py"
    assert client(ADMIN).get(f"/api/search?q=%23{gid}").json()["groups"][0]["items"][0]["id"] == gid
    hidden = client(OTHER).get("/api/search?q=zebra").json()
    assert not {g["kind"] for g in hidden["groups"]} & {"project", "goal", "task", "file"}
    assert client(ADMIN).get("/api/search?q=").json()["groups"] == []


def test_notification_categories():
    cat = governance.category
    assert cat("goal", "success", "Goal #4 passed the gate and waits for approval") == {"category": "approval", "action": True}
    assert cat("goal", "success", "Goal #4 passed the gate and was merged")["category"] == "completion"
    assert cat("goal", "error", "Goal #4 failed after 2 iteration(s)")["category"] == "failure"
    assert cat("goal", "warning", "Goal #4 paused: a task is blocked")["category"] == "blocker"
    assert cat("security", "warning", "Refused command")["category"] == "security"
    assert cat("proposals", "warning", "2 proposal(s) need a person's decision")["action"] is True
    assert cat("notice:admin", "info", "Maintenance tonight")["category"] == "notice"
    governance.notify("Goal #9 failed after 1 iteration(s)", "x", level="error", source="goal")
    item = governance.list_notifications(ADMIN)["items"][0]
    assert item["category"] == "failure" and item["action"] is False
