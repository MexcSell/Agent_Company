import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import evolution, executors, learning, loop, registry
from agent_company.api import create_app
from agent_company.db import Agent, Lesson, session


def dev_id():
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "developer", Agent.parent_id.is_(None))).first().id


def test_playbook_versions_are_curated_and_restorable():
    aid = dev_id()
    learning.learn(aid, None, "Write the test first.", True, ["python"])
    assert evolution.versions(aid)[0]["version"] == 1
    for t in ["Keep functions small.", "Run the linter.", "Name things clearly."]:
        learning.learn(aid, None, t, True, [])
    vs = evolution.versions(aid)
    assert vs[0]["version"] == 2 and "Name things clearly." in vs[0]["playbook"]
    evolution.restore(aid, 1)
    with session() as s:
        a = s.get(Agent, aid)
        assert a.playbook_version == 3 and a.playbook == vs[-1]["playbook"]
    with pytest.raises(KeyError):
        evolution.restore(aid, 99)


def test_low_success_starts_an_experiment_then_promotes_or_rolls_back():
    aid = dev_id()
    learning.learn(aid, None, "Initial lesson.", True, [])
    for i in range(4):
        learning.learn(aid, None, f"Broke the build {i}.", False, [])
    with session() as s:
        exp = s.get(Agent, aid).experiment
        assert exp and exp["playbook"].startswith("AVOID:") and exp["baseline"] < 0.5
    ctx = learning.experience_context(aid)
    assert "(trial version)" in ctx
    for i in range(evolution.EVAL_WINDOW):  # the trial does better than the baseline
        learning.learn(aid, None, f"Fixed it {i}.", True, [])
    with session() as s:
        a = s.get(Agent, aid)
        assert a.experiment == {} and a.playbook.startswith("AVOID:")
    stages = [e["stage"] for e in evolution.overview()["events"]]
    assert {"diagnose", "experiment", "evaluate", "promote"} <= set(stages)


def test_failed_trial_is_rolled_back_with_a_cooldown():
    aid = dev_id()
    learning.learn(aid, None, "Initial lesson.", True, [])
    before = None
    for i in range(10):
        learning.learn(aid, None, f"Failure {i}.", False, [])
        with session() as s:
            a = s.get(Agent, aid)
            if a.experiment and before is None:
                before = a.playbook  # the playbook the trial competes against
        if evolution.overview()["events"][0]["stage"] == "rollback":
            break
    with session() as s:
        a = s.get(Agent, aid)
        assert a.experiment == {} and a.playbook == before
    learning.learn(aid, None, "Another failure.", False, [])
    with session() as s:
        assert s.get(Agent, aid).experiment == {}, "no new trial straight after a rollback"


def test_clones_merge_back_and_retire_after_a_goal(monkeypatch):
    orig = dev_id()
    first = registry.staff("developer")  # keep the original busy so the goal must clone
    assert first.id == orig
    gid = loop.create_goal("Busy team")
    loop.run_goal(gid)
    with session() as s:
        clone = s.scalars(select(Agent).where(Agent.parent_id == orig)).first()
        assert clone.lifecycle == "retired" and not clone.enabled
        o = s.get(Agent, orig)
        assert o.tasks_done == 1 and s.scalars(select(Lesson).where(Lesson.agent_id == orig)).first() is not None
    registry.release(orig)
    stages = [e["stage"] for e in evolution.overview()["events"]]
    assert "merge" in stages


def test_handover_package_on_retry(monkeypatch):
    calls = {"n": 0}
    gid = loop.create_goal("Retry me", quality_commands=["false"], max_iterations=2)
    prompts = []
    real = executors.OfflineExecutor._task

    def task(self, req):
        prompts.append(req.prompt)
        calls["n"] += 1
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    loop.run_goal(gid)
    second_build = [p for p in prompts if "Your task: Implement the specification" in p][1]
    assert "Handover from your previous attempt (iteration 1, done)" in second_build
    assert '"objective": "Implement the specification"' in second_build


def test_staffing_prefers_proven_collaborators():
    a1 = registry.create_agent("qa", title="QA Alpha")
    a2 = registry.create_agent("qa", title="QA Beta")
    with session() as s:
        dev = dev_id()
        registry.connect(s, a2["id"], dev, "collaborated", 0.6)
        for qid in (a1["id"], a2["id"]):
            s.get(Agent, qid).skills = {"testing": 7}
        base_qa = s.scalars(select(Agent).where(Agent.role == "qa", Agent.title == "QA Engineer")).first()
        base_qa.enabled = False
    gid = loop.create_goal("Team")
    from agent_company.db import Task

    with session() as s:
        s.add(Task(goal_id=gid, iteration=1, key="b", title="b", role="developer", agent_id=dev_id()))
    picked = registry.staff("qa", ["testing"], gid)
    assert picked.id == a2["id"]


def test_lifecycle_and_manual_agents():
    a = registry.create_agent("data_engineer", skills={"sql": 6}, writes=True)
    assert a["room"] == "development" and "write" in a["permissions"]
    c = registry.clone_agent(a["id"])
    assert c["parent_id"] == a["id"]
    assert registry.set_lifecycle(a["id"], "paused")["enabled"] is False
    assert registry.resolve_role("data_engineer")[0] == "data_engineer"  # clone still active
    assert registry.set_lifecycle(a["id"], "retired")["lifecycle"] == "retired"
    with pytest.raises(ValueError):
        registry.set_lifecycle(a["id"], "active")
    with pytest.raises(ValueError):
        registry.update_agent(a["id"], {"enabled": True})
    gm = registry.list_agents()[0]
    with pytest.raises(ValueError):
        registry.set_lifecycle(gm["id"], "retired")
    with pytest.raises(ValueError):
        registry.create_agent("x", room="nowhere")
    with pytest.raises(ValueError):
        registry.update_agent(a["id"], {"permissions": ["root"]})


def test_evolution_api():
    with TestClient(create_app(start_background=False)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        a = app.post("/api/agents", json={"role": "translator", "title": "Translator"}).json()
        assert app.post(f"/api/agents/{a['id']}/clone").json()["parent_id"] == a["id"]
        assert app.post(f"/api/agents/{a['id']}/lifecycle", json={"state": "paused"}).json()["lifecycle"] == "paused"
        assert app.post(f"/api/agents/{a['id']}/merge").status_code == 400  # not a clone
        merged = app.post("/api/agents/consolidate", json={"role": "translator"}).json()
        assert len(merged) == 1
        assert app.get("/api/evolution").json()["events"][0]["stage"] == "merge"
        assert app.post("/api/evolution/cycle").json() == {"experiments_started": 0}
        d = app.get(f"/api/agents/{a['id']}").json()
        assert d["lifecycle"] == "paused" and "handovers" in d and app.get(f"/api/agents/{a['id']}/versions").json() == []
