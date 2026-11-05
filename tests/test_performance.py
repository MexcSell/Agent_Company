import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import evaluation, learning, loop, performance, prefs
from agent_company.api import create_app
from agent_company.db import Agent, session


def test_defects_open_close_and_reopen():
    gid = loop.create_goal("x")
    performance.track_defects(gid, 1, [{"check": "command pytest", "passed": False, "detail": "1 failed"},
                                       {"check": "secret scan", "passed": False, "detail": "a.py: AWS"},
                                       {"check": "syntax", "passed": True, "detail": ""}])
    open_ = performance.list_defects("open", gid)
    assert [d["severity"] for d in open_] == ["critical", "high"]
    performance.track_defects(gid, 2, [{"check": "command pytest", "passed": True},
                                       {"check": "secret scan", "passed": False, "detail": "still"}])
    by = {d["check"]: d for d in performance.list_defects(goal_id=gid)}
    assert by["command pytest"]["status"] == "retested" and by["command pytest"]["closed_iteration"] == 2
    assert by["secret scan"]["last_iteration"] == 2
    performance.track_defects(gid, 3, [{"check": "command pytest", "passed": False, "detail": "again"}])
    assert performance.list_defects(goal_id=gid)[0]["status"] in {"reopened", "detected"}
    d = performance.set_defect_status(by["secret scan"]["id"], "accepted_risk")
    assert d["status"] == "accepted_risk"
    with pytest.raises(ValueError):
        performance.set_defect_status(d["id"], "closed")


def test_failing_goal_records_defects_that_close_when_fixed():
    gid = loop.create_goal("Needs a fix", quality_commands=["test -f FIXED"], max_iterations=1)
    loop.run_goal(gid)
    assert performance.list_defects("open", gid)[0]["check"] == "command test -f FIXED"


def test_certification_needs_repeated_evidence():
    with session() as s:
        aid = s.scalars(select(Agent).where(Agent.role == "developer")).first().id
    assert performance.certification(aid)["level"] == 0
    g1 = loop.create_goal("one")
    learning.learn(aid, g1, "Did a hard task.", True, [])
    c = performance.certification(aid)
    assert c["level"] == 2, "one success is not expertise"
    for i in range(12):
        learning.learn(aid, loop.create_goal(f"g{i}"), f"Lesson {i}", True, [])
    c = performance.certification(aid)
    assert c["level"] == 4 and c["evidence"]["goals"] == 13
    assert performance.LEVELS[c["level"]] == c["meaning"]


def test_performance_report():
    gid = loop.create_goal("Make it", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    r = performance.report()
    assert r["company"]["passed"] == 1 and r["company"]["pass_rate"] == 1.0
    dev = next(a for a in r["agents"] if a["role"] == "developer")
    assert dev["completion_rate"] == 1.0 and dev["reliability"] == 1.0 and dev["goal_pass_rate"] == 1.0
    assert any(room["room"] == "development" for room in r["rooms"])


def test_eval_harness_runs_compares_and_never_merges(tmp_path):
    suite = tmp_path / "mini.json"
    suite.write_text(json.dumps({"name": "mini", "tasks": [
        {"name": "worklog", "title": "Write a worklog", "checks": ["test -f WORKLOG.md"], "max_iterations": 1},
        {"name": "impossible", "title": "Make MAGIC exist", "files": {"a.txt": "x"},
         "checks": ["test -f MAGIC"], "max_iterations": 1}]}))
    out = evaluation.run_suite(suite, "v1", repeats=2)
    assert out["runs"] == 4 and out["solved"] == 1 and out["pass_rate"] == 0.5
    by = {t["task"]: t for t in out["by_task"]}
    assert by["worklog"]["solved"] and by["impossible"]["end_reasons"] == ["gate_failed"]
    prefs.set_value("max_iterations", 1)
    evaluation.run_suite(suite, "v2", repeats=1, only=["worklog"])
    cmp = evaluation.compare("v1", "v2")
    assert cmp["tasks"][1]["task"] == "worklog" and cmp["pass_rate_change"] == 0.5
    with session() as s:
        from agent_company.db import Goal, Notification

        goals = s.scalars(select(Goal).where(Goal.origin == "eval")).all()
        assert goals and all(g.status in {"approved", "failed"} for g in goals)
        assert s.scalars(select(Notification)).first() is None, "eval runs do not notify"
    assert {lab["label"] for lab in evaluation.labels()} == {"v1", "v2"}
    with pytest.raises(KeyError):
        evaluation.summary("nope")


def test_bundled_suite_loads_and_api():
    assert {s["name"] for s in evaluation.list_suites()} >= {"smoke"}
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        gid = loop.create_goal("Make it", quality_commands=["test -f NOPE"], max_iterations=1)
        loop.run_goal(gid)
        assert app.get(f"/api/goals/{gid}").json()["defects"][0]["status"] == "detected"
        assert app.get("/api/defects?status=open").json()
        assert app.get("/api/certifications").json()[0]["level"] >= 0
        assert app.get("/api/performance").json()["company"]["goals"] == 1
        assert app.get("/api/agents/1").json()["certification"]["skill_family"] == "general_manager"
        assert "smoke" in [s["name"] for s in app.get("/api/evals").json()["suites"]]
        assert app.post("/api/evals/run", json={"suite": "smoke", "label": "bad label!"}).status_code == 422


# --- guides G-R-04: measured gate for enabling more roles ----------------------------------------

def test_gated_roles_stay_off_until_the_pipeline_is_measured_reliable():
    import secrets

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from agent_company import performance
    from agent_company.api import create_app
    from agent_company.db import Agent, EvalRun, Run, session

    gate = performance.enablement_gate()
    assert not gate["met"] and "no smoke suite run yet" in gate["reasons"][0]
    with session() as s:
        sec = s.scalars(select(Agent).where(Agent.role == "security")).first()
        assert sec is not None and not sec.enabled and performance.gated("security")
        sec_id = sec.id
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        r = app.patch(f"/api/agents/{sec_id}", json={"enabled": True})
        assert r.status_code == 409 and "not measured reliable" in r.json()["detail"]
        # a simulator-only run never counts as reliable
        with session() as s:
            for rep in range(3):
                s.add(EvalRun(label="sim", suite="smoke", task="t", repeat=rep, goal_id=900 + rep, passed=True))
                s.add(Run(goal_id=900 + rep, model_name="Offline simulator", kind="offline"))
        assert any("simulator" in x for x in performance.enablement_gate()["reasons"])
        with session() as s:
            for rep in range(3):
                s.add(EvalRun(label="real", suite="smoke", task="t", repeat=rep, goal_id=950 + rep, passed=True))
                s.add(Run(goal_id=950 + rep, model_name="Claude Sonnet 5.5", kind="llm", cost=0.01))
        gate = performance.enablement_gate()
        assert gate["met"] and gate["metrics"]["pass_rate"] == 1.0, gate
        assert app.patch(f"/api/agents/{sec_id}", json={"enabled": True}).json()["enabled"] is True
        assert app.get("/api/performance/enablement").json()["met"] is True


def test_an_administrator_can_override_the_gate_and_it_is_audited():
    import secrets

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from agent_company.api import create_app
    from agent_company.db import Agent, session

    with session() as s:
        sec_id = s.scalars(select(Agent).where(Agent.role == "security")).first().id
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        r = app.patch(f"/api/agents/{sec_id}", json={"enabled": True, "override_gate": True})
        assert r.status_code == 200 and r.json()["enabled"] is True
        assert "enablement_gate_override" in {x["action"] for x in app.get("/api/audit").json()["records"]}


def test_estimates_are_compared_with_what_happened(monkeypatch):
    """S07-R-04: task minutes per role and goal cost, estimate against actual, with sample sizes."""
    from agent_company import executors
    from agent_company.db import Goal, Run, Task

    real = executors.standard_plan

    def plan(goal):
        out = real(goal)
        for t in out["tasks"]:
            t["estimated_minutes"] = 10
        return out | {"estimated_cost_usd": 0.5}

    monkeypatch.setattr(executors, "standard_plan", plan)
    monkeypatch.setattr(performance, "CALIBRATION_MIN", 2)
    gids = [loop.create_goal(f"Build tool {i}", quality_commands=[]) for i in range(2)]
    for gid in gids:
        loop.run_goal(gid)
    with session() as s:
        for r in s.scalars(select(Run).where(Run.task_id.is_not(None))):
            role = s.get(Task, r.task_id).role
            r.duration = {"developer": 1800.0, "architect": 600.0}.get(role, 120.0)  # 3x, 1x, 0.2x the estimate
            r.cost = 0.25
        assert all(s.get(Goal, g).estimated_cost_usd == 0.5 for g in gids)
    cal = performance.report()["calibration"]
    by = {x["role"]: x for x in cal["time_by_role"]}
    assert by["developer"]["samples"] == 2 and by["developer"]["median_ratio"] == 3.0
    assert by["developer"]["bias"] == "underestimates" and by["developer"]["within_50"] == 0
    assert by["architect"]["bias"] == "calibrated" and by["architect"]["mape"] == 0
    assert by["qa"]["bias"] == "overestimates"
    assert cal["goal_cost"]["samples"] == 2 and cal["goal_cost"]["actual"] > cal["goal_cost"]["estimated"] > 0
    monkeypatch.setattr(performance, "CALIBRATION_MIN", 5)
    assert performance.calibration()["time_by_role"][0]["bias"] == "too few samples"
