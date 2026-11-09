"""AICO UI-QA-01/02: the Evaluation & QA figures come from the gate's events only."""

from datetime import timedelta

from fastapi.testclient import TestClient

from agent_company import api, projects, qa
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Event, Goal, Task, now, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


def _gate(gid, iteration, passed, commit, checks, at=None):
    return Event(goal_id=gid, kind="gate", message=f"iteration {iteration}: quality gate {'passed' if passed else 'failed'}",
                 iteration=iteration, created_at=at or now(),
                 data={"iteration": iteration, "passed": passed, "commit": commit, "checks": checks})


def test_executions_flaky_checks_areas_and_pending():
    p = projects.create("QA")
    with session() as s:
        g = Goal(title="Build", description="", repo_path="", project_id=p["id"], status="running", iteration=2)
        waiting = Goal(title="New", description="", repo_path="", project_id=p["id"], status="planning")
        bench = Goal(title="Bench", description="", repo_path="", origin="eval", status="approved")
        s.add_all([g, waiting, bench])
        s.flush()
        s.add(Task(goal_id=g.id, iteration=2, key="b", title="B", role="developer", status="queued", stage="blocked"))
        s.add_all([
            _gate(g.id, 1, False, "abc", [{"check": "pytest", "passed": False, "stage": "unit", "detail": "1 failed"},
                                          {"check": "secrets", "passed": True, "stage": "security"}]),
            _gate(g.id, 1, True, "abc", [{"check": "pytest", "passed": True, "stage": "unit"},
                                         {"check": "review qa", "passed": True, "stage": "review"}]),
            _gate(g.id, 0, True, "old", [{"check": "pytest", "passed": True, "stage": "unit"}],
                  at=now() - timedelta(days=40)),
            _gate(bench.id, 1, True, "b", [{"check": "x", "passed": True, "stage": "unit"}]),
        ])
        gid = g.id
    r = qa.report("7d")
    assert r["executions"] == {"total": 4, "passed": 3, "failed": 1, "blocked": 1, "pending": 1}
    assert r["gates"] == {"total": 2, "passed": 1}
    assert r["flaky"] == ["pytest"], "the same check passed and failed on commit abc"
    row = next(x for x in r["goals"] if x["goal_id"] == gid)
    assert row["passed"] and set(row["areas"]) == {"functional", "acceptance"}, "the latest gate only"
    assert {s["stage"] for s in r["stages"]} == {"unit", "security", "review"}
    assert sum(d["gates"] for d in r["days"]) == 2
    assert qa.report("30d")["gates"]["total"] == 2 and qa.report("24h", set())["executions"]["total"] == 0


def test_api_qa():
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    c = TestClient(app)
    assert c.get("/api/qa?period=30d").json()["period"] == "30d"
    assert c.get("/api/qa?period=2y").status_code == 400
