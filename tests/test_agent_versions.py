"""SPEC-02: versioned agent configuration with rollback (S02-R-01) and agent health (S02-R-02)."""

import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import governance, registry, router, usage
from agent_company.api import create_app
from agent_company.db import Agent, AgentVersion, ModelEntry, Notification, Route, now, session


def _agent(role="developer"):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


def _fail(agent_id, n, error="APIError: 500 from the provider"):
    for _ in range(n):
        usage.record(model_name="Offline simulator", kind="offline", ok=False, error=error, agent_id=agent_id)


def test_seeded_agents_have_a_baseline_and_every_update_is_a_version():
    aid = _agent()
    first = registry.config_versions(aid)
    assert [v["reason"] for v in first] == ["baseline"]
    registry.update_agent(aid, {"model_pref": "Claude Haiku 5.5"}, "alice")
    registry.update_agent(aid, {"plugins": ["review"]}, "bob")
    registry.update_agent(aid, {"permissions": ["read"]}, "carol")
    registry.update_agent(aid, {"permissions": ["read"]}, "carol")  # no change: no version
    versions = registry.config_versions(aid)
    assert [v["version"] for v in versions] == [4, 3, 2, 1]
    assert [v["changed_by"] for v in versions[:3]] == ["carol", "bob", "alice"]
    out = registry.rollback(aid, 1, "dave")
    assert out["config_version"] == 5
    with session() as s:
        a = s.get(Agent, aid)
        assert registry.config_of(a) == first[0]["snapshot"], "rollback restores the first snapshot exactly"
    assert registry.config_versions(aid)[0]["reason"] == "rolled back to v1"


def test_created_cloned_paused_stopped_and_lifted_agents_are_versioned():
    new = registry.create_agent("data_engineer", writes=True, actor="admin")
    assert [v["reason"] for v in registry.config_versions(new["id"])] == ["created"]
    clone = registry.clone_agent(new["id"])
    assert registry.config_versions(clone["id"])[0]["reason"].startswith("cloned from")
    registry.set_lifecycle(new["id"], "paused", "admin")
    registry.set_lifecycle(new["id"], "active", "admin")
    governance.stop("E1", new["id"], "ops", "test")
    governance.lift("E1", new["id"], "ops")
    reasons = [v["reason"] for v in registry.config_versions(new["id"])]
    assert reasons == ["stop E1 lifted", "paused by stop E1", "lifecycle active", "lifecycle paused", "created"]


def test_stale_updates_are_refused_and_unknown_models_rejected():
    aid = _agent()
    v = registry.agent_detail(aid)["config_version"]
    registry.update_agent(aid, {"plugins": ["a"]}, "first", expected_version=v)
    with pytest.raises(registry.StaleVersion):
        registry.update_agent(aid, {"plugins": ["b"]}, "second", expected_version=v)
    assert registry.agent_detail(aid)["plugins"] == ["a"], "the stale edit changed nothing"
    with pytest.raises(ValueError, match="not in the model catalog"):
        registry.update_agent(aid, {"model_pref": "Made Up Model 9"})
    with pytest.raises(ValueError):
        registry.update_agent(aid, {"model_pref": "agent:99999"})
    with pytest.raises(ValueError):
        registry.update_agent(aid, {"model_pref": f"agent:{aid}"})
    registry.update_agent(aid, {"model_pref": None})


def test_retired_agents_cannot_be_rolled_back():
    new = registry.create_agent("data_engineer")
    registry.set_lifecycle(new["id"], "retired")
    with pytest.raises(ValueError, match="permanent"):
        registry.rollback(new["id"], 1, "x")


def test_three_executor_errors_degrade_an_agent_and_staffing_picks_another():
    aid = _agent()
    _fail(aid, 2)
    assert registry.agent_detail(aid)["health"] == "ok"
    _fail(aid, 1)
    a = registry.agent_detail(aid)
    assert a["health"] == "degraded" and "3 of its last 3 runs" in a["health_reason"]
    with session() as s:
        assert s.scalars(select(Notification).where(Notification.source == "agents")).first() is not None
    staffed = registry.staff("developer")
    assert staffed.id != aid and staffed.parent_id == aid, "a fresh clone instead of the degraded agent"
    registry.release(staffed.id)
    registry.reset_health(aid, "admin")
    assert registry.agent_detail(aid)["health"] == "ok"
    _fail(aid, 1)
    assert registry.agent_detail(aid)["health"] == "ok", "runs before the reset no longer count"
    assert registry.staff("developer").id == aid


def test_stopped_runs_do_not_degrade_and_a_model_change_resets():
    aid = _agent()
    _fail(aid, 4, error="exit 1\nterminated by signal 9")
    assert registry.agent_detail(aid)["health"] == "ok", "emergency stops are not the agent's fault"
    _fail(aid, 3)
    assert registry.agent_detail(aid)["health"] == "degraded"
    registry.update_agent(aid, {"model_pref": "Claude Haiku 5.5"}, "admin")
    assert registry.agent_detail(aid)["health"] == "ok"


def test_a_failing_model_test_degrades_the_agents_that_ask_for_it(monkeypatch):
    aid = _agent()
    registry.update_agent(aid, {"model_pref": "Claude Haiku 5.5"})
    with session() as s:
        haiku = s.scalars(select(ModelEntry).where(ModelEntry.name == "Claude Haiku 5.5")).first()
        haiku.enabled = True
        mid = haiku.id
    monkeypatch.setattr(router, "available", lambda m: (True, ""))

    class Broken:
        def run(self, req):
            from agent_company.executors import Result

            return Result(text="", ok=False, error="401 invalid key")

    from agent_company import executors

    monkeypatch.setattr(executors, "for_choice", lambda choice, timeout=0: Broken())
    assert router.test_model(mid)["ok"] is False
    assert registry.agent_detail(aid)["health"] == "degraded"
    assert "failed its test" in registry.agent_detail(aid)["health_reason"]
    with session() as s:
        qa_route = s.get(Route, "qa")
        assert qa_route is None or qa_route.model_name != "Claude Haiku 5.5"
    assert registry.agent_detail(_agent("qa"))["health"] == "ok", "agents on other models are untouched"


def test_inactive_agents_are_listed_for_review():
    aid = _agent("architect")
    with session() as s:
        a = s.get(Agent, aid)
        a.last_active_at = now() - timedelta(days=45)
    listed = {x["id"]: x for x in registry.inactive(30)}
    assert aid in listed and listed[aid]["idle_days"] >= 45
    assert _agent("general_manager") not in listed
    assert aid not in {x["id"] for x in registry.inactive(60)}


def test_agent_version_api():
    from agent_company import auth
    from agent_company.db import User

    auth.register("admin", secrets.token_urlsafe(32))
    aid = _agent()
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        secret = secrets.token_urlsafe(32)
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(secret)
        assert app.post("/api/auth/device", json={"device_secret": secret}).status_code == 200
        v = app.get(f"/api/agents/{aid}").json()["config_version"]
        r = app.patch(f"/api/agents/{aid}", json={"plugins": ["x"], "expected_version": v})
        assert r.status_code == 200 and r.json()["config_version"] == v + 1
        stale = app.patch(f"/api/agents/{aid}", json={"plugins": ["y"], "expected_version": v})
        assert stale.status_code == 409 and "changed meanwhile" in stale.json()["detail"]
        assert app.patch(f"/api/agents/{aid}", json={"model_pref": "Nope"}).status_code == 400
        versions = app.get(f"/api/agents/{aid}/config-versions").json()
        assert versions[0]["version"] == v + 1
        assert app.post(f"/api/agents/{aid}/rollback?version={v}").json()["plugins"] == []
        assert app.post(f"/api/agents/{aid}/rollback?version=999").status_code == 404
        _fail(aid, 3)
        h = app.get("/api/agents/health").json()
        assert [x["id"] for x in h["degraded"]] == [aid] and "inactive" in h
        assert app.post(f"/api/agents/{aid}/health/reset").json()["health"] == "ok"
        actions = {r["action"] for r in app.get("/api/audit").json()["records"]}
        assert {"agent_update", "agent_rollback", "agent_health_reset"} <= actions
    with session() as s:
        assert s.scalars(select(AgentVersion).where(AgentVersion.agent_id == aid)).first() is not None
