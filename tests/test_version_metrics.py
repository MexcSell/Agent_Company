"""UI-EVO-03: what an agent did under each definition version, attributed by time (runs do not record the version):
a task to the version current when it finished, a run to the one current when it started; sample sizes, no estimate."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import agentdefs, api, loop, performance, registry
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, Run, Task, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


def dev():
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "developer", Agent.parent_id.is_(None))).first().id


def at(v):
    return datetime.fromisoformat(v["at"]).astimezone(UTC)


def test_history_reads_the_definition_versions_oldest_first():
    aid = dev()
    agentdefs.sync(aid, "first")
    registry.update_agent(aid, {"goal": "Write careful code"}, "user:admin")
    agentdefs.sync(aid, "mission changed")
    hist = agentdefs.history(aid)
    assert len(hist) >= 2 and hist[-1]["version"] == hist[-2]["version"] + 1
    assert hist[-1]["reason"] == "mission changed" and hist[0]["at"] <= hist[-1]["at"]


def test_work_is_counted_for_the_version_current_at_the_time(monkeypatch):
    aid = dev()
    t0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    hist = [{"version": 1, "commit": "aaa", "at": t0.isoformat(), "reason": "first"},
            {"version": 2, "commit": "bbb", "at": (t0 + timedelta(hours=2)).astimezone().isoformat(),
             "reason": "mission changed"}]  # git reports local time; the database stores UTC
    monkeypatch.setattr(agentdefs, "history", lambda agent_id: hist)
    gid = loop.create_goal("Measured", quality_commands=[])
    hour = timedelta(hours=1)
    with session() as s:
        for key, status, when in (("a", "done", t0 + hour), ("b", "failed", t0 + hour), ("c", "done", t0 + 3 * hour),
                                  ("d", "done", t0 - hour)):
            s.add(Task(goal_id=gid, iteration=1, key=key, title="t", role="developer", agent_id=aid,
                       status=status, finished_at=when.replace(tzinfo=None)))  # stored without a zone, in UTC
        s.add(Run(agent_id=aid, model_name="m", kind="llm", cost=0.5, duration=10.0, ok=True,
                  started_at=(t0 + 3 * hour).replace(tzinfo=None),
                  tool_calls=[{"tool": "Bash", "ok": True}, {"tool": "Edit", "ok": False}]))
        s.add(Run(agent_id=aid, model_name="m", kind="llm", cost=0.25, duration=30.0, ok=True,
                  started_at=(t0 + 3 * hour).replace(tzinfo=None), tool_calls=[]))
    m = performance.version_metrics(aid)
    old, new = m["versions"][1], m["versions"][0]
    assert new["version"] == 2, "newest first"
    assert old["task_success"] == {"rate": 0.5, "hits": 1, "n": 2} and old["runs"] == 0
    assert old["until"] == hist[1]["at"] and new["until"] is None
    assert new["tasks"] == 1 and new["failure_rate"]["rate"] == 0.0 and new["runs"] == 2 and new["cost"] == 0.75
    assert new["mean_run_seconds"] == 20.0 and new["tool_call_failure_rate"] == {"rate": 0.5, "hits": 1, "n": 2}
    assert m["unattributed"] == {"tasks": 1, "runs": 0}, "work before the first version is not credited to it"


def test_the_api_lists_versions_for_any_signed_in_user():
    aid = dev()
    agentdefs.sync(aid, "first")
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    r = TestClient(app).get(f"/api/agents/{aid}/version-metrics")
    assert r.status_code == 200 and r.json()["versions"] and "time" in r.json()["basis"]
    assert TestClient(app).get("/api/agents/99999/version-metrics").status_code == 404
