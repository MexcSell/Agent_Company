"""G5 Control Hub UI-G5-CC: the Command Center's operational metrics, alerts and budget monitor (command.py)."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, command, projects
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, Goal, Incident, Risk, Room, Run, Task, now, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "member", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": "own"}}


def client(user):
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: user
    return TestClient(app)


def seed():
    """Three finished missions in the last day (two accepted, one of them after a failed task) and an old one."""
    p = projects.create("Hub")
    t = now()
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.role == "developer")).first()
        goals = [Goal(title=f"m{i}", description="", repo_path="", project_id=p["id"], status=st,
                      updated_at=t - timedelta(hours=1)) for i, st in enumerate(("approved", "approved", "failed"))]
        old = Goal(title="old", description="", repo_path="", status="approved", updated_at=t - timedelta(days=3))
        bench = Goal(title="bench", description="", repo_path="", status="failed", origin="eval",
                     updated_at=t - timedelta(hours=1))
        s.add_all([*goals, old, bench])
        s.flush()
        s.add_all([
            Task(goal_id=goals[0].id, iteration=1, key="a", title="a", role="developer", status="failed",
                 finished_at=t - timedelta(hours=2)),
            Task(goal_id=goals[0].id, iteration=2, key="a", title="a", role="developer", status="done",
                 finished_at=t - timedelta(hours=1)),
            Task(goal_id=goals[1].id, iteration=1, key="b", title="b", role="developer", status="done",
                 finished_at=t - timedelta(hours=1)),
            Task(goal_id=goals[2].id, iteration=1, key="c", title="c", role="developer", status="failed",
                 finished_at=t - timedelta(hours=1)),
            Run(agent_id=dev.id, goal_id=goals[0].id, model_name="m", kind="llm", cost=0.3, duration=600.0,
                started_at=t - timedelta(hours=2)),
            Run(agent_id=dev.id, goal_id=goals[1].id, model_name="m", kind="llm", cost=0.1, duration=60.0,
                started_at=t - timedelta(hours=1)),
        ])
        return p["id"], [g.id for g in goals], dev.id


def test_metrics_count_the_period_with_their_sample_sizes():
    pid, gids, _ = seed()
    c = command.center(ADMIN, "24h")
    m = c["metrics"]
    assert m["mission_success_rate"] == {"rate": 0.667, "hits": 2, "n": 3, "basis": "period"}, "eval goals left out"
    assert m["task_completion_rate"]["hits"] == 2 and m["task_completion_rate"]["n"] == 4
    assert m["cost_per_successful_mission"]["usd"] == 0.2 and m["cost_per_successful_mission"]["n"] == 2
    # the failed mission had a failed task too: 1 of the 2 troubled missions recovered
    assert m["recovery_rate"]["hits"] == 1 and m["recovery_rate"]["n"] == 2
    assert 0 < m["agent_utilisation"]["rate"] < 1 and m["agent_utilisation"]["busy_seconds"] == 660.0
    assert m["validation_score"]["basis"] == "all" and m["approval_wait"]["basis"] == "all"
    assert m["improvement_gain"]["pass_rate_change"] is None and m["improvement_gain"]["n"] == 0
    # the period before has none of them: rates are None, never 0
    assert c["previous"]["mission_success_rate"]["rate"] is None
    assert c["previous"]["recovery_rate"] == {"rate": None, "hits": 0, "n": 0, "basis": "period"}


def test_alerts_list_the_most_severe_first_and_hide_other_projects_from_members():
    pid, gids, dev = seed()
    with session() as s:
        s.add(Incident(category="behaviour", severity="SEV-1", title="Runaway agent"))
        s.add(Risk(project_id=pid, kind="blocker", title="No credentials", severity="high"))
        s.get(Agent, dev).health, s.get(Agent, dev).health_reason = "degraded", "three failures"
    al = command.center(ADMIN, "24h")["alerts"]
    kinds = [a["kind"] for a in al]
    assert kinds[0] == "incident" and al[0]["level"] == "critical"
    assert {"blocker", "agent", "mission"} <= set(kinds)
    assert any(a["link"] == f"#/goals/{gids[2]}" for a in al), "the failed mission links to its page"
    member = command.center(MEMBER, "24h", seen=set())
    assert not any(a["kind"] in ("incident", "blocker") for a in member["alerts"])
    assert not any(a["kind"] == "mission" for a in member["alerts"]), "a member sees no mission of others' projects"
    assert "host" not in member["budget"], "the host's figures are for administrators"


def test_budget_monitor_has_seven_days_and_the_daily_room_budget():
    seed()
    with session() as s:
        room = s.scalars(select(Room).where(Room.parent_key.is_(None))).first()
        room.daily_budget_usd = 2.5
    b = command.center(ADMIN, "24h")["budget"]
    assert len(b["days"]) == 7 and b["daily_budget_usd"] >= 2.5
    assert round(sum(d["usd"] for d in b["days"]), 4) == 0.4
    assert set(b["host"]) >= {"cpus", "load", "memory_total", "disk_free"}


def test_the_endpoint_checks_the_period_and_answers_members():
    seed()
    assert client(ADMIN).get("/api/command-center?period=1y").status_code == 400
    r = client(MEMBER).get("/api/command-center?period=7d")
    assert r.status_code == 200 and r.json()["period"] == "7d"
    assert r.json()["missions"]["active"] == 0
