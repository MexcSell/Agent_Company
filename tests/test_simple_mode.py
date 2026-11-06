"""REQ-003 Simple mode: per-user mode, the mission form, the Simple overview; REQ-004 report endpoint."""
import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, budgets, governance, projects
from agent_company.api import create_app
from agent_company.db import Event, Goal, Task, Upload, User, now, session


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(api, "_run_quietly", lambda gid: None)  # the loop never runs here
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        yield c


def member(app, name="thomas", **perms):
    """An approved non-admin user signed in on its own client."""
    other = TestClient(app.app, client=("127.0.0.1", 50001))
    s = secrets.token_urlsafe(32)
    uid = other.post("/api/auth/register", json={"name": name, "device_secret": s}).json()["user"]["id"]
    assert app.post(f"/api/users/{uid}/approve").json()["status"] == "active"
    if perms:
        with session() as ss:
            u = ss.get(User, uid)
            u.permissions = {**(u.permissions or {}), **perms}
    assert other.post("/api/auth/device", json={"device_secret": s}).status_code == 200
    return other, uid


def future(days=3):
    return (now() + timedelta(days=days)).isoformat()


def mission(pid, **kw):
    return {"project_id": pid, "title": "Ship the export", "description": "Add CSV export",
            "budget_usd": 5.0, **kw}


def goal_row(pid, status, title="g"):
    with session() as s:
        g = Goal(title=title, description=title, status=status, project_id=pid, iteration=1)
        s.add(g)
        s.flush()
        s.add(Task(goal_id=g.id, iteration=1, key="build", title="Build", role="developer", writes=True,
                   status="done" if status != "running" else "running"))
        return g.id


def test_new_user_defaults_to_simple(app):
    assert app.get("/api/auth/me").json()["ui_mode"] == "simple"
    other, _ = member(app)
    assert other.get("/api/auth/me/ui_mode").json() == {"mode": "simple"}


def test_existing_user_without_value_is_advanced(app):
    with session() as s:
        s.scalars(select(User)).first().ui_mode = ""  # an account from before the setting existed
    assert app.get("/api/auth/me").json()["ui_mode"] == "advanced"
    assert app.get("/api/auth/me/ui_mode").json() == {"mode": "advanced"}


def test_rebind_keeps_ui_mode(app):
    other, uid = member(app)
    assert other.put("/api/auth/me/ui_mode", json={"mode": "advanced"}).status_code == 200
    with session() as s:
        s.get(User, uid).device_hash = None  # the user moves to a new computer
    again = TestClient(app.app, client=("127.0.0.1", 50002))
    again.post("/api/auth/register", json={"name": "thomas", "device_secret": secrets.token_urlsafe(32)})
    with session() as s:
        assert s.get(User, uid).ui_mode == "advanced"


def test_set_ui_mode_changes_only_caller_and_audits(app):
    other, uid = member(app)
    assert other.put("/api/auth/me/ui_mode", json={"mode": "advanced"}).json() == {"mode": "advanced"}
    assert other.get("/api/auth/me").json()["ui_mode"] == "advanced"
    assert app.get("/api/auth/me").json()["ui_mode"] == "simple"
    rows = governance.list_audit(action="ui_mode_set")
    assert rows and rows[0]["actor"] == "user:thomas"


def test_set_ui_mode_rejects_unknown_value(app):
    assert app.put("/api/auth/me/ui_mode", json={"mode": "expert"}).status_code == 400
    assert app.get("/api/auth/me").json()["ui_mode"] == "simple"


def test_mission_creates_goal_with_budget_deadline_and_sections(app):
    p = projects.create("Shop")
    dl = future()
    r = app.post("/api/simple/missions", json=mission(
        p["id"], deadline=dl, notes="We tried pandas before", constraints="No new dependencies",
        resources={"links": ["https://example.com/spec"]}))
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    assert budgets.summary(gid)["limits"]["cost_usd"] == 5.0
    with session() as s:
        g = s.get(Goal, gid)
        assert g.project_id == p["id"] and g.status == "queued" and g.urls == ["https://example.com/spec"]
        assert abs((g.deadline.replace(tzinfo=None) - now().replace(tzinfo=None)).total_seconds() - 3 * 86400) < 60
        assert "## Experience / notes\nWe tried pandas before" in g.description
        assert "## Constraints\nNo new dependencies" in g.description
    assert app.get(f"/api/goals/{gid}").json()["deadline"]
    assert governance.list_audit(action="mission_create")[0]["target"] == str(gid)


def test_mission_unknown_or_invisible_project_404(app):
    assert app.post("/api/simple/missions", json=mission(999)).status_code == 404
    p = projects.create("Admins only")  # created by nobody: a member with "own" projects cannot see it
    other, _ = member(app)
    assert other.post("/api/simple/missions", json=mission(p["id"])).status_code == 404


def test_mission_foreign_repository_403_and_audited(app, tmp_path):
    other, uid = member(app)
    p = projects.create("Mine", created_by=uid)
    r = other.post("/api/simple/missions", json=mission(p["id"], resources={"repositories": [str(tmp_path)]}))
    assert r.status_code == 403
    assert governance.list_audit(action="mission_create_denied")
    with session() as s:
        assert s.scalars(select(Goal)).first() is None


def test_mission_admin_may_name_other_repository(app):
    p = projects.create("Shop")
    other = projects.create("Other")
    r = app.post("/api/simple/missions", json=mission(p["id"], resources={"repositories": [other["path"]]}))
    assert r.status_code == 200, r.text
    with session() as s:
        assert s.get(Goal, r.json()["id"]).repo_path == other["path"]


def test_mission_rejects_nonpositive_or_excessive_budget(app):
    p = projects.create("Shop")
    for b in (0, -1, 10_001):
        assert app.post("/api/simple/missions", json=mission(p["id"], budget_usd=b)).status_code == 422


def test_mission_rejects_past_deadline(app):
    p = projects.create("Shop")
    r = app.post("/api/simple/missions", json=mission(p["id"], deadline=(now() - timedelta(hours=1)).isoformat()))
    assert r.status_code == 400 and "future" in r.text


def test_mission_rejects_other_users_upload(app):
    p = projects.create("Shop")
    with session() as s:
        s.add(Upload(id="u1", name="x.txt", path="/tmp/x.txt", user_id=12345))
    r = app.post("/api/simple/missions", json=mission(p["id"], resources={"files": ["u1"]}))
    assert r.status_code == 400
    assert app.post("/api/simple/missions", json=mission(p["id"], resources={"files": ["nope"]})).status_code == 400


def test_mission_rejects_bad_link(app):
    p = projects.create("Shop")
    for link in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x"):
        assert app.post("/api/simple/missions",
                        json=mission(p["id"], resources={"links": [link]})).status_code == 400


def test_mission_paused_project_400(app):
    p = projects.create("Shop")
    projects.set_status(p["id"], "paused", "admin")
    assert app.post("/api/simple/missions", json=mission(p["id"])).status_code == 400


def test_mission_needs_run_permission(app):
    other, uid = member(app, run=False)
    p = projects.create("Mine", created_by=uid)
    assert other.post("/api/simple/missions", json=mission(p["id"])).status_code == 403
    assert governance.list_audit(action="goal_create_denied")


def test_simple_overview_shape_and_scope(app):
    other, uid = member(app)
    mine = projects.create("Mine", created_by=uid)
    hidden = projects.create("Hidden")
    a = goal_row(mine["id"], "running", "visible run")
    goal_row(hidden["id"], "running", "hidden run")
    d = other.get("/api/simple/overview").json()
    assert set(d) == {"running", "approvals", "blocked", "delivered", "decisions"}
    assert [c["id"] for c in d["running"]] == [a]
    c = d["running"][0]
    for k in ("title", "project", "status", "progress", "tasks_done", "tasks", "cost_usd", "budget_usd",
              "elapsed_s", "deadline", "remaining_s", "overdue", "blockers"):
        assert k in c
    assert c["progress"] == 0.0 and c["tasks"] == 1


def test_simple_overview_lists_awaiting_approval_and_delivered_summary(app):
    p = projects.create("Shop")
    wait = goal_row(p["id"], "awaiting_approval", "waits")
    done = goal_row(p["id"], "approved", "merged")
    d = app.get("/api/simple/overview").json()
    assert [c["id"] for c in d["approvals"]] == [wait]
    delivered = {c["id"]: c for c in d["delivered"]}
    assert set(delivered) == {wait, done}
    assert delivered[done]["report"] == f"/api/goals/{done}/report"
    assert delivered[done]["labels"] == {"verified": 0, "partial": 1, "failed": 0}  # done without evidence


def test_goal_report_endpoint_409_while_running(app):
    p = projects.create("Shop")
    gid = goal_row(p["id"], "running")
    assert app.get(f"/api/goals/{gid}/report").status_code == 409


def test_goal_report_endpoint_json_and_markdown(app):
    p = projects.create("Shop")
    gid = goal_row(p["id"], "approved")
    with session() as s:
        t = s.scalars(select(Task).where(Task.goal_id == gid)).one()
        t.gate = {"acceptance": [{"type": "command", "passed": True, "detail": "ok"}]}
        s.add(Event(kind="gate", message="iteration 1: quality gate passed", goal_id=gid,
                    data={"passed": True, "checks": [{"check": "task build", "passed": True}], "iteration": 1}))
    d = app.get(f"/api/goals/{gid}/report").json()
    assert d["tasks"]["items"][0]["label"] == "verified"
    r = app.get(f"/api/goals/{gid}/report?format=md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "## Verification" in r.text and "[verified] build" in r.text


def test_goal_report_endpoint_404_for_hidden_project(app):
    other, _ = member(app)
    hidden = projects.create("Hidden")
    gid = goal_row(hidden["id"], "approved")
    assert other.get(f"/api/goals/{gid}/report").status_code == 404
    assert app.get("/api/goals/999/report").status_code == 404
