"""Defect priority and lifecycle (ASEAO09 S09-R-03, AT-014, AT-015): detected -> assigned -> fix_submitted ->
retested -> closed, with the administrator's alternatives; a fix is verified by running the check again."""

import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_company import loop, performance
from agent_company.api import create_app
from agent_company.db import Defect, Goal, Task, session


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def _defect(gid: int, check: str) -> dict:
    return next(d for d in performance.list_defects(goal_id=gid) if d["check"] == check)


def test_a_submitted_fix_is_not_closed_until_the_gate_runs_the_check_again():
    gid = loop.create_goal("Impossible", quality_commands=["false"], max_iterations=2)
    assert loop.run_goal(gid)["status"] == "failed"
    d = _defect(gid, "command false")
    steps = [(h["from"], h["to"]) for h in d["history"]]
    assert steps == [("", "detected"), ("detected", "assigned"), ("assigned", "fix_submitted"),
                     ("fix_submitted", "reopened")], "the developer's change is a submission, the gate decides"
    assert d["open"] and d["assignee"] and d["priority"] == 2 and d["severity"] == "high"
    v = d["verification"]
    assert v["how"] == "gate" and v["passed"] is False and v["exit_code"] == 1 and v["iteration"] == 2
    assert v["command"] == "false" and len(v["commit"]) == 40


def test_the_gates_pass_retests_and_the_merge_closes():
    gid = loop.create_goal("x")
    performance.track_defects(gid, 1, [{"check": "command pytest", "passed": False, "detail": "1 failed",
                                        "command": "pytest", "commit": "a" * 40, "exit_code": 1, "duration_s": 2.0}])
    with session() as s:
        s.add(Task(goal_id=gid, iteration=2, key="t1", title="fix", role="developer", writes=True, status="done"))
    assert performance.assign(gid, 2, 7) == 1
    assert performance.fix_submitted(gid, 2) == 1
    assert _defect(gid, "command pytest")["status"] == "fix_submitted"
    performance.track_defects(gid, 2, [{"check": "command pytest", "passed": True, "command": "pytest",
                                        "commit": "b" * 40, "exit_code": 0, "duration_s": 1.5}])
    d = _defect(gid, "command pytest")
    assert d["status"] == "retested" and not d["open"] and d["closed_iteration"] == 2
    assert d["verification"] | {"at": ""} == {"how": "gate", "iteration": 2, "passed": True, "at": "",
                                              "command": "pytest", "commit": "b" * 40, "exit_code": 0,
                                              "duration_s": 1.5}
    assert performance.on_merged(gid, "c" * 40) == 1
    d = _defect(gid, "command pytest")
    assert d["status"] == "closed" and d["history"][-1]["note"] == "merged cccccccccc"
    performance.track_defects(gid, 3, [{"check": "command pytest", "passed": False, "detail": "again"}])
    assert _defect(gid, "command pytest")["status"] == "reopened"


def test_no_writing_task_means_no_submitted_fix():
    gid = loop.create_goal("x")
    performance.track_defects(gid, 1, [{"check": "scope", "passed": False, "detail": "x"}])
    performance.assign(gid, 2, 3)
    with session() as s:
        s.add(Task(goal_id=gid, iteration=2, key="r", title="review", role="qa", writes=False, status="done"))
    assert performance.fix_submitted(gid, 2) == 0
    assert _defect(gid, "scope")["status"] == "assigned"


def test_a_critical_defect_with_a_submitted_fix_still_blocks_the_merge():
    gid = loop.create_goal("Critical defect", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    with session() as s:
        s.add(Defect(goal_id=gid, check="secret scan", severity="critical", status="fix_submitted"))
    with pytest.raises(ValueError, match="critical"):
        loop.approve(gid)
    performance.set_defect_status(_defect(gid, "secret scan")["id"], "accepted_risk", "test key, rotated")
    loop.approve(gid)
    assert _defect(gid, "secret scan")["status"] == "accepted_risk"


def test_administrator_alternatives():
    gid, other = loop.create_goal("x"), loop.create_goal("y")
    performance.track_defects(gid, 1, [{"check": "command a", "passed": False},
                                       {"check": "command b", "passed": False}])
    performance.track_defects(other, 1, [{"check": "command a", "passed": False}])
    a, b, foreign = _defect(gid, "command a"), _defect(gid, "command b"), _defect(other, "command a")
    with pytest.raises(ValueError, match="same goal"):
        performance.set_defect_status(b["id"], "duplicate", duplicate_of=foreign["id"])
    with pytest.raises(ValueError, match="same goal"):
        performance.set_defect_status(b["id"], "duplicate")
    d = performance.set_defect_status(b["id"], "duplicate", "same root cause", duplicate_of=a["id"])
    assert d["status"] == "duplicate" and d["duplicate_of"] == a["id"] and d["resolution"] == "same root cause"
    with pytest.raises(ValueError, match="already a duplicate"):
        performance.set_defect_status(a["id"], "duplicate", duplicate_of=b["id"])
    with pytest.raises(ValueError, match="retest"):
        performance.set_defect_status(a["id"], "not_reproducible")
    for bad in ("closed", "retested", "fix_submitted"):
        with pytest.raises(ValueError):
            performance.set_defect_status(a["id"], bad)
    d = performance.set_defect_status(b["id"], "detected")
    assert d["open"] and d["duplicate_of"] is None and d["resolution"] == ""
    assert performance.set_defect_priority(a["id"], 1)["priority"] == 1
    with pytest.raises(ValueError):
        performance.set_defect_priority(a["id"], 0)
    assert performance.list_defects("open", gid)[0]["id"] == a["id"], "priority orders the list"


def test_retest_runs_the_check_again_on_the_goals_code():
    gid = loop.create_goal("Make it", quality_commands=["test -f FIXED"], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "failed"
    d = _defect(gid, "command test -f FIXED")
    with pytest.raises(ValueError, match="retest the defect first"):
        performance.set_defect_status(d["id"], "not_reproducible")
    r = performance.retest(d["id"], actor="admin")
    assert r["status"] == "detected" and r["verification"]["how"] == "retest" and not r["verification"]["passed"]
    with session() as s:
        worktree = Path(s.get(Goal, gid).worktree)
    (worktree / "FIXED").write_text("yes")
    r = performance.retest(d["id"], actor="admin")
    v = r["verification"]
    assert r["status"] == "retested" and v["passed"] and v["exit_code"] == 0 and v["by"] == "admin"
    assert r["history"][-1] == r["history"][-1] | {"from": "detected", "to": "retested", "by": "admin"}
    (worktree / "FIXED").unlink()
    assert performance.retest(d["id"])["status"] == "reopened"
    performance.track_defects(gid, 1, [{"check": "syntax", "passed": False}])
    with pytest.raises(ValueError, match="only command checks"):
        performance.retest(_defect(gid, "syntax")["id"])


def test_api_is_admin_only_and_audited():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        gid = loop.create_goal("x")
        performance.track_defects(gid, 1, [{"check": "command a", "passed": False}])
        did = _defect(gid, "command a")["id"]
        assert app.post(f"/api/defects/{did}/status", json={"status": "closed"}).status_code == 400
        assert app.post(f"/api/defects/{did}/status", json={"status": "duplicate"}).status_code == 400
        r = app.post(f"/api/defects/{did}/priority", json={"priority": 1})
        assert r.status_code == 200 and r.json()["priority"] == 1
        r = app.post(f"/api/defects/{did}/status", json={"status": "deferred", "note": "next release"})
        assert r.json()["status"] == "deferred" and r.json()["resolution"] == "next release"
        assert app.post("/api/defects/999/priority", json={"priority": 1}).status_code == 404
        actions = [a["action"] for a in app.get("/api/audit").json()["records"]]
        assert "defect_status" in actions and "defect_priority" in actions
        app.cookies.clear()
        assert app.post(f"/api/defects/{did}/status", json={"status": "deferred"}).status_code in (401, 403)
