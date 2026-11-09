import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import evolution, executors, learning, loop, prefs, registry
from agent_company.api import create_app
from agent_company.config import settings
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
    prefs.set_value("candidate_smoke", False)  # the live trial on its own; the smoke stage is tested below
    aid = dev_id()
    learning.learn(aid, None, "Initial lesson.", True, [])
    for i in range(4):
        learning.learn(aid, None, f"Broke the build {i}.", False, [])
    with session() as s:
        exp = s.get(Agent, aid).experiment
        assert exp and exp["playbook"].startswith("AVOID:") and exp["baseline"] < 0.5
    ctx = learning.experience_context(aid)
    assert "(trial version)" in ctx
    for i in range(settings().canary_window):  # the trial does better than the baseline
        learning.learn(aid, None, f"Fixed it {i}.", True, [])
    with session() as s:
        a = s.get(Agent, aid)
        assert a.experiment == {} and a.playbook.startswith("AVOID:")
    stages = [e["stage"] for e in evolution.overview()["events"]]
    assert {"diagnose", "experiment", "evaluate", "promote"} <= set(stages)


def start_candidate(aid):
    learning.learn(aid, None, "Initial lesson.", True, [])
    for i in range(4):
        learning.learn(aid, None, f"Broke the build {i}.", False, [])
    with session() as s:
        return s.get(Agent, aid).experiment


def fake_suite(monkeypatch, aid, rates):
    """evaluation.run_suite that records which playbook eval goals of the agent see on each side."""
    from agent_company import evaluation

    seen = []

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        with session() as s:
            a = s.get(Agent, aid)
            seen.append((label.split("-")[2], evolution.active_playbook(a, eval_run=True),
                         evolution.active_playbook(a)))
        return {"pass_rate": rates[label.split("-")[2]]}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    return seen


def test_a_candidate_reaches_users_goals_only_after_the_smoke_suite(monkeypatch):
    """S13-D-02: the candidate runs the smoke suite against the current playbook before the live trial."""
    spawned = []
    monkeypatch.setattr(evolution, "_spawn", spawned.append)
    aid = dev_id()
    exp = start_candidate(aid)
    assert exp["stage"] == "smoke" and len(spawned) == 1
    assert "(trial version)" not in learning.experience_context(aid), "users' goals keep the current playbook"
    learning.learn(aid, None, "A live outcome during the smoke stage.", True, [])
    with session() as s:
        assert s.get(Agent, aid).experiment["outcomes"] == [], "live outcomes do not count for the candidate"
    assert evolution.start_smoke(aid) is False, "one smoke trial per candidate"
    seen = fake_suite(monkeypatch, aid, {"baseline": 0.0, "candidate": 0.67})
    spawned[0]()
    (b_side, b_eval, b_live), (c_side, c_eval, c_live) = seen[:2]  # the smoke suite; the held-out one follows
    assert (b_side, b_eval[1], b_live[1]) == ("baseline", False, False)
    assert (c_side, c_eval, c_live[1]) == ("candidate", (exp["playbook"], True), False), \
        "only eval goals see the candidate, and only on its side"
    with session() as s:
        exp = s.get(Agent, aid).experiment
        assert exp["stage"] == "live" and exp["smoke"] == {"baseline": 0.0, "candidate": 0.67}
    assert "(trial version)" in learning.experience_context(aid)
    assert "smoke" in [e["stage"] for e in evolution.overview()["events"]]


def test_a_candidate_worse_on_the_smoke_suite_is_rolled_back(monkeypatch):
    aid = dev_id()
    before = start_candidate(aid)
    assert before["stage"] == "smoke"
    fake_suite(monkeypatch, aid, {"baseline": 1.0, "candidate": 0.33})
    assert evolution.smoke_trial(aid) == {"baseline": 1.0, "candidate": 0.33}
    with session() as s:
        assert s.get(Agent, aid).experiment == {}
    assert evolution.overview()["events"][0]["stage"] == "rollback"


def test_eval_goals_do_not_steer_evolution():
    aid = dev_id()
    gid = loop.create_goal("Benchmark", origin="eval")
    learning.learn(aid, None, "Initial lesson.", True, [])
    for i in range(6):
        learning.learn(aid, gid, f"Benchmark failure {i}.", False, [])
    with session() as s:
        assert evolution.success_rate(s, aid, 6) == (1.0, 1)
        assert s.get(Agent, aid).experiment == {}


def test_failed_trial_is_rolled_back_with_a_cooldown():
    prefs.set_value("candidate_smoke", False)
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


def test_a_released_clone_merges_into_its_original_at_once():
    orig = dev_id()
    registry.staff("developer")  # the original is busy: the next one is a clone
    clone = registry.staff("developer")
    assert clone.parent_id == orig
    with session() as s:
        s.get(Agent, clone.id).xp = 7
        before = s.get(Agent, orig).xp or 0
    registry.release(clone.id)
    with session() as s:
        c, o = s.get(Agent, clone.id), s.get(Agent, orig)
        assert c.lifecycle == "retired" and not c.enabled and c.xp == 0
        assert o.xp == before + 7
    learning.learn(clone.id, None, "Learned after the release.", True, [])  # the iteration's lesson comes later
    with session() as s:
        assert s.scalars(select(Lesson).where(Lesson.agent_id == clone.id)).first() is None
        assert s.scalars(select(Lesson).where(Lesson.agent_id == orig)).first() is not None
        assert s.get(Agent, orig).tasks_done == 1 and s.get(Agent, clone.id).tasks_done == 0
    assert registry.staff("developer").id not in (orig, clone.id), "a retired clone is never staffed again"
    registry.release(orig)


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
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
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


def test_a_candidate_that_breaks_a_solved_task_is_rolled_back_despite_its_pass_rate(monkeypatch):
    """G-R-11: as many runs passed, but not the ones the current playbook solved."""
    from agent_company import evaluation

    aid = dev_id()
    assert start_candidate(aid)["stage"] == "smoke"
    by_side = {"baseline": [("a", True), ("b", True), ("c", False)],
               "candidate": [("a", True), ("b", False), ("c", True)]}

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        tasks = by_side[label.split("-")[2]]
        return {"pass_rate": 0.67, "by_task": [{"task": t, "solved": ok} for t, ok in tasks]}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    assert evolution.smoke_trial(aid) == {"baseline": 0.67, "candidate": 0.67}
    with session() as s:
        assert s.get(Agent, aid).experiment == {}
    ev = evolution.overview()["events"]
    assert ev[0]["stage"] == "rollback" and "breaks b" in ev[1]["title"]


def test_v28_a_candidate_must_also_not_lose_on_the_held_out_suite(monkeypatch):
    """V28: after the smoke suite, the held-out suite (hidden checks) with the same repeats."""
    from agent_company import evaluation

    aid = dev_id()
    assert start_candidate(aid)["stage"] == "smoke"
    calls = []

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        side = label.split("-")[2]
        calls.append((suite, side, repeats))
        return {"pass_rate": {"smoke": {"baseline": 0.33, "candidate": 0.67},
                              "python-final": {"baseline": 1.0, "candidate": 0.5}}[suite][side]}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    assert evolution.smoke_trial(aid) == {"baseline": 0.33, "candidate": 0.67}
    assert calls == [("smoke", "baseline", 2), ("smoke", "candidate", 2),
                     ("python-final", "baseline", 2), ("python-final", "candidate", 2)]
    with session() as s:
        assert s.get(Agent, aid).experiment == {}, "worse on the held-out suite: rolled back"
    assert "python-final" in evolution.overview()["events"][0]["title"]


def test_v28_the_held_out_suite_is_skipped_after_a_failed_smoke_suite_and_can_be_switched_off(monkeypatch):
    aid = dev_id()
    start_candidate(aid)
    seen = fake_suite(monkeypatch, aid, {"baseline": 1.0, "candidate": 0.33})
    evolution.smoke_trial(aid)
    assert len(seen) == 2
    prefs.set_value("holdout_suite", "")
    aid2 = registry.create_agent("developer", "Second Developer")["id"]
    start_candidate(aid2)
    seen = fake_suite(monkeypatch, aid2, {"baseline": 0.0, "candidate": 1.0})
    evolution.smoke_trial(aid2)
    assert len(seen) == 2
    with session() as s:
        assert s.get(Agent, aid2).experiment["stage"] == "live"
    with pytest.raises(ValueError):
        prefs.set_value("holdout_suite", "no-such-suite")


def test_v28_a_canary_below_the_minimum_pass_rate_is_not_promoted():
    """#16 was promoted on 1/3 live: a low baseline plus min_improvement is not enough on its own."""
    prefs.set_value("candidate_smoke", False)
    aid = dev_id()
    exp = start_candidate(aid)  # its last failure is the canary's first outcome
    assert exp["window"] == 5 and exp["baseline"] == 0.25 and exp["outcomes"] == [False]
    for ok in (True, True, False):  # 2/4: 3/5 is still reachable
        learning.learn(aid, None, f"Outcome {ok}.", ok, [])
    with session() as s:
        assert len(s.get(Agent, aid).experiment["outcomes"]) == 4
    learning.learn(aid, None, "Last outcome.", False, [])  # 2/5 = 40 %: beats 25 % + 10 points, not the 50 % floor
    with session() as s:
        assert s.get(Agent, aid).experiment == {}
    stages = [e["stage"] for e in evolution.overview()["events"]]
    assert "promote" not in stages and "rollback" in stages
