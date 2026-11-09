"""SPEC-13: capability gaps, learning plans, independent evaluation, experiments, learning metrics."""

import json
import secrets
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import evaluation, evolution, experiments, learning, loop, performance, prefs, registry, router
from agent_company import specs, training
from agent_company.config import settings
from agent_company.api import create_app
from agent_company.db import Agent, EvolutionEvent, Goal, Lesson, Run, Task, now, session


def dev_id():
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "developer", Agent.parent_id.is_(None))).first().id


def outcomes(aid, skill, pattern):
    """Record task outcomes on real work, oldest first: '1' passed, '0' failed."""
    gid = loop.create_goal("Work")
    for i, c in enumerate(pattern):
        learning.learn(aid, gid, f"{skill} task {i} {'passed' if c == '1' else 'failed'}.", c == "1", [skill])


def fake_suite(monkeypatch, rates):
    """evaluation.run_suite returning the given pass rate per side; records what each call asked for."""
    calls = []

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        calls.append({"suite": suite, "label": label, "repeats": repeats, "trial": trial})
        side = (trial or {}).get("side", "")
        rate = rates[side]
        return {"label": label, "runs": 2 * repeats, "passed": round(rate * 2 * repeats), "pass_rate": rate,
                "avg_iterations": 1.0, "avg_seconds": 1.0, "total_cost": 0.0, "cost_per_passed": None}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    return calls


# --- gaps (S13-R-01) ---------------------------------------------------------------------

def test_failing_three_of_the_last_four_tasks_on_a_skill_is_a_poor_performance_gap():
    aid = dev_id()
    outcomes(aid, "sql", "11" + "0010")  # last four: 0, 0, 1, 0 (newest last)
    outcomes(aid, "python", "1111")
    found = {(g["skill"], g["category"]) for g in training.gaps(aid)}
    assert ("sql", "poor_performance") in found
    assert not any(skill == "python" for skill, _ in found)
    g = next(g for g in training.gaps(aid) if g["skill"] == "sql")
    assert g["evidence"]["outcomes"] == 6 and 0 < g["readiness"] < 1


def test_other_gap_categories():
    aid = dev_id()
    outcomes(aid, "css", "11010")  # passed 3 of 5, 2 of the last 4 failed: below the 60 % of proficiency
    outcomes(aid, "docker", "1")
    with session() as s:  # the only docker success is old
        les = next(x for x in s.scalars(select(Lesson).where(Lesson.agent_id == aid)) if "docker" in x.technologies)
        les.created_at = now() - timedelta(days=120)
        gid = loop.create_goal("Needs kubernetes")
        for i in range(2):  # the developer role's tasks ask for a skill no developer has
            s.add(Task(goal_id=gid, iteration=1, key=f"k{i}", title="Deploy", role="developer",
                       contract={"skills": ["kubernetes"]}))
        for i in range(6):
            s.add(Run(agent_id=aid, kind="llm", model_name="m", ok=i == 0))
    cats = {(g["skill"], g["category"]) for g in training.gaps(aid)}
    assert ("docker", "outdated") in cats
    assert ("kubernetes", "missing_capability") in cats
    assert ("*", "low_reliability") in cats
    assert ("css", "insufficient_proficiency") not in cats, "60 % is proficient enough"


def test_gaps_are_raised_once_a_week_and_propose_a_plan_where_a_final_suite_exists():
    aid = dev_id()
    outcomes(aid, "python", "0000")
    outcomes(aid, "sql", "0000")
    out = training.raise_gaps()
    assert out == {"raised": 2, "plans_proposed": 1}, "python has evals/python-final.json, sql has none"
    plan = training.list_plans(aid)[0]
    assert plan["capability"] == "python" and plan["status"] == "planned" and plan["gap"] == "poor_performance"
    assert plan["final_suite"] == "python-final" and plan["practice_suite"] == "python-practice"
    assert training.raise_gaps() == {"raised": 0, "plans_proposed": 0}
    with session() as s:
        assert len(s.scalars(select(EvolutionEvent).where(EvolutionEvent.stage == "gap")).all()) == 2
    assert next(g for g in training.gaps(aid) if g["skill"] == "python")["plan_id"] == plan["id"]
    assert evolution.cycle() == 0  # the cycle raises gaps too, without repeating them


# --- learning plans (S13-R-02, S13-R-03) ---------------------------------------------------------

def test_plans_keep_practice_and_final_sets_apart():
    aid = dev_id()
    with pytest.raises(ValueError, match="not a final"):
        training.create_plan(aid, "python", final_suite="python-practice")
    with pytest.raises(ValueError, match="not a practice"):
        training.create_plan(aid, "python", practice_suite="python-final", final_suite="python-final")
    with pytest.raises(ValueError, match="no final evaluation"):
        training.create_plan(aid, "cobol")
    learning.learn(aid, None, "Use pathlib for python file paths.", True, ["python"])
    p = training.create_plan(aid, "python", created_by="user:admin")
    assert (p["current_level"], p["target_level"], p["status"]) == (0, 1, "planned")
    assert p["resources"], "the plan lists knowledge items to study"
    assert any("python-final" in o for o in p["objectives"])
    with pytest.raises(ValueError, match="already has an open"):
        training.create_plan(aid, "python")


def test_a_plan_practises_then_is_certified_only_by_the_final_evaluation(monkeypatch):
    aid = dev_id()
    with session() as s:
        s.get(Agent, aid).skills = {"python": 1}
    p = training.create_plan(aid, "python", target_level=2)
    spawned = []
    monkeypatch.setattr(training, "_spawn", spawned.append)
    assert training.start_plan(p["id"])["status"] == "running" and len(spawned) == 1
    calls = fake_suite(monkeypatch, {"practice": 0.0, "final": 1.0})
    spawned[0]()
    assert [c["suite"] for c in calls] == ["python-practice", "python-final"]
    assert all(c["trial"]["trainee_id"] == aid and c["trial"]["plan_id"] == p["id"] for c in calls)
    done = training.get_plan(p["id"])
    assert done["status"] == "passed" and "certified L2" in done["recommendation"]
    assert done["results"]["practice"]["pass_rate"] == 0.0, "practice results are kept but do not decide"
    with session() as s:
        assert s.get(Agent, aid).skills["python"] == 4
    cert = performance.certification(aid)
    assert cert["skills"]["python"]["level"] == 2 and cert["skills"]["python"]["plan_id"] == p["id"]
    assert training.create_plan(aid, "python")["current_level"] == 2


def test_a_failed_final_evaluation_recommends_retraining(monkeypatch):
    aid = dev_id()
    p = training.create_plan(aid, "python")
    with session() as s:
        from agent_company.db import LearningPlan

        s.get(LearningPlan, p["id"]).status = "running"
    fake_suite(monkeypatch, {"practice": 1.0, "final": 0.5})
    out = training.run_plan(p["id"])
    assert out["status"] == "failed" and out["recommendation"].startswith("retrain 'python'")
    assert performance.certification(aid)["skills"]["python"]["level"] == 0
    assert evolution.overview()["events"][0]["stage"] == "retrain"
    with pytest.raises(ValueError):
        training.cancel_plan(p["id"])


def test_exercises_run_in_scratch_repositories_and_final_checks_stay_hidden(monkeypatch):
    aid = dev_id()
    goals = []
    monkeypatch.setattr(loop, "run_goal", lambda gid: goals.append(gid))
    evaluation.run_suite("python-final", "final-x", repeats=1, trial={"trainee_id": aid})
    evaluation.run_suite("python-practice", "practice-x", repeats=1, trial={"trainee_id": aid})
    with session() as s:
        rows = [s.get(Goal, g) for g in goals]
        final = [g for g in rows if g.trial.get("hide_checks")]
        practice = [g for g in rows if not g.trial.get("hide_checks")]
        assert len(final) == 2 and len(practice) == 2
        assert all(g.max_iterations == 1 and g.origin == "eval" for g in final)
        assert all(g.max_iterations == 2 for g in practice)
        ws = Path(prefs.config.settings().workspaces_dir)
        assert all(Path(g.repo_path).parent == ws and Path(g.repo_path).name.startswith("eval-") for g in rows)
        g = final[0]
        g.worktree = str(Path(g.repo_path))
    specs.write_plan(g, 1, [])
    text = (specs.folder(g) / "requirements.md").read_text()
    assert "Quality commands" not in text and "python3 -c" not in text
    seen = []
    monkeypatch.setattr(loop, "_lesson", lambda aid_, title, ok, feedback, gid, facts:
                        seen.append(feedback) or ("x", {**facts, "limitations": ""}))
    with session() as s:
        s.add(Task(goal_id=g.id, iteration=1, key="a", title="Do it", role="developer", agent_id=aid, status="failed",
                   gate={"acceptance": [{"condition": {"type": "command", "value": "python3 -c 'x'"}, "passed": False,
                                         "detail": "python3 -c 'x' exited 1"}], "findings": ["python3 -c x fails"]}))
    loop._learn_from(g.id, 1, False, "command `python3 -c ...` failed")
    assert seen == [""], "a final evaluation's findings never reach a lesson"
    with session() as s:
        report = s.scalars(select(Lesson).where(Lesson.goal_id == g.id)).one().report
    assert report["failures"] and "python3" not in json.dumps(report), "nor its experience report"
    with pytest.raises(ValueError, match="only eval goals"):
        loop.create_goal("User work", trial={"trainee_id": aid})


def test_eval_goals_staff_their_trainee():
    first = dev_id()
    other = registry.create_agent("developer", title="Second developer")["id"]
    gid = loop.create_goal("Exercise", origin="eval", trial={"trainee_id": other})
    a = registry.staff("developer", ["python"], gid)
    assert a.id == other
    plain = loop.create_goal("Other exercise", origin="eval")
    assert registry.staff("developer", [], plain).id in {first, other}


def test_a_route_experiment_side_overrides_the_model_only_on_eval_goals(monkeypatch):
    router.add_model({"name": "Candidate", "kind": "llm", "model_id": "openai/candidate"})
    seen = []

    def chain(role, pref=None, *a, **k):
        seen.append(pref)
        raise router.NotCleared("stop here")

    monkeypatch.setattr(router, "resolve_chain", chain)
    agent = registry.staff("developer")
    from agent_company import executors

    for origin, trial in (("eval", {"models": {"developer": "Candidate"}}), ("user", None)):
        gid = loop.create_goal("x", origin=origin, trial=trial)
        loop._execute(agent, executors.Request(purpose="task", role="developer", system="", prompt=""), "task",
                      goal_id=gid)
    assert seen == ["Candidate", agent.model_pref]


# --- experiments (S13-R-04..R-06) ------------------------------------------------------------------

def test_restricted_targets_are_never_experiments():
    for t in ("governance", "permissions", "budgets", "approvals", "security"):
        with pytest.raises(PermissionError):
            experiments.check_target(t)
    with pytest.raises(ValueError):
        experiments.check_target("workflow_template")
    assert experiments.may_deploy("playbook", "system") and not experiments.may_deploy("route", "system")
    assert experiments.may_deploy("route", "user:admin")


def test_a_better_route_is_only_recommended_until_an_administrator_applies_it(monkeypatch):
    router.add_model({"name": "Old", "kind": "llm", "model_id": "openai/old"})
    router.add_model({"name": "New", "kind": "llm", "model_id": "openai/new"})
    router.set_route("developer", "Old")
    e = experiments.propose("route", "New", role="developer", hypothesis="New fixes more bugs", repeats=2,
                            created_by="user:admin")
    assert e["status"] == "proposed" and e["baseline_version"] == "Old"
    spawned = []
    monkeypatch.setattr(experiments, "_spawn", spawned.append)
    experiments.start(e["id"])
    calls = fake_suite(monkeypatch, {"baseline": 0.5, "candidate": 1.0})
    spawned[0]()
    assert [c["trial"]["models"] for c in calls] == [{"developer": "Old"}, {"developer": "New"}]
    assert {c["repeats"] for c in calls} == {2}, "same suite, same repeats on both sides"
    out = experiments.get(e["id"])
    assert out["status"] == "review" and out["decision"].startswith("recommend New")
    assert out["results"]["comparison"]["samples"] == {"baseline": 4, "candidate": 4}
    assert router.list_routes()["developer"] == "Old", "a route is never changed by the experiment itself"
    with pytest.raises(PermissionError):
        experiments.apply(e["id"], "system")
    applied = experiments.apply(e["id"], "user:admin")
    assert applied["status"] == "canary" and applied["results"]["canary"]["previous"] == "Old"
    assert router.list_routes()["developer"] == "New"
    assert applied["results"]["canary"]["baseline_source"] == "suite", "no live history: the suite's 50 % stands in"
    aid = dev_id()
    experiments.observe(aid, False)
    experiments.observe(aid, False)  # 0/2: even a pass now (1/3) cannot reach the baseline of 50 %
    out = experiments.get(e["id"])
    assert out["status"] == "rolled_back" and router.list_routes()["developer"] == "Old"


def test_a_canary_that_holds_is_rolled_out():
    router.add_model({"name": "New", "kind": "llm", "model_id": "openai/new"})
    aid = dev_id()
    outcomes(aid, "python", "1100")  # live baseline 50 %
    e = experiments.propose("route", "New", role="developer")
    with session() as s:
        experiments.update(s, e["id"], status="review")
    experiments.apply(e["id"], "user:admin")
    for ok in (True, False, True):
        experiments.observe(aid, ok)
    assert experiments.get(e["id"])["status"] == "rolled_out"
    assert router.list_routes()["developer"] == "New"


def test_evolution_candidates_are_experiments_with_both_sides_and_a_canary(monkeypatch):
    aid = dev_id()
    learning.learn(aid, None, "Initial lesson.", True, [])
    for i in range(4):
        learning.learn(aid, None, f"Broke the build {i}.", False, [])
    exp_id = experiments.list_experiments()[0]["id"]
    assert experiments.get(exp_id)["status"] == "baseline"
    calls = fake_suite(monkeypatch, {"baseline": 0.0, "candidate": 0.5})
    evolution.smoke_trial(aid)
    assert all(c["trial"] == {"trainee_id": aid, "side": c["trial"]["side"], "experiment_id": exp_id} for c in calls)
    e = experiments.get(exp_id)
    assert e["status"] == "canary" and e["results"]["candidate"]["pass_rate"] == 0.5
    assert e["results"]["baseline"]["runs"] == 2 * settings().candidate_repeats  # V28: 2 repeats per side
    for i in range(3):  # 0/3 of 5: the rest cannot reach the 50 % floor (V28)
        learning.learn(aid, None, f"Live failure {i}.", False, [])
    e = experiments.get(exp_id)
    assert e["status"] == "rolled_back" and e["results"]["canary"]["outcomes"] == [False, False, False]
    with session() as s:
        assert s.get(Agent, aid).experiment == {}
    assert training.metrics()["benchmark_by_playbook_version"][0]["candidate"] == 0.5


def test_a_users_playbook_experiment_runs_the_same_path(monkeypatch):
    aid = dev_id()
    e = experiments.propose("playbook", "DO: write the test first.", agent_id=aid, suite="smoke")
    with pytest.raises(ValueError, match="no live outcomes"):
        experiments.start(e["id"])  # with nothing to compare against, the canary could never promote it
    outcomes(aid, "python", "1010")  # 50 %: room to show an improvement of min_improvement points
    spawned = []
    monkeypatch.setattr(evolution, "_spawn", spawned.append)
    assert experiments.start(e["id"])["status"] == "baseline" and len(spawned) == 1
    with pytest.raises(ValueError):
        experiments.start(e["id"])
    fake_suite(monkeypatch, {"baseline": 0.5, "candidate": 0.5})
    spawned[0]()
    assert experiments.get(e["id"])["status"] == "canary", "a tie on the suite may go on to the canary"
    for i in range(settings().canary_window):
        learning.learn(aid, None, f"Live pass {i}.", True, [])
    e = experiments.get(e["id"])
    assert e["status"] == "rolled_out" and e["results"]["canary"]["outcomes"] == [True] * settings().canary_window
    with session() as s:
        assert s.get(Agent, aid).playbook == "DO: write the test first."


def test_a_healthy_agent_has_no_room_for_a_playbook_experiment():
    aid = dev_id()
    outcomes(aid, "python", "1111")
    e = experiments.propose("playbook", "DO: x", agent_id=aid)
    with pytest.raises(ValueError, match="cannot beat it"):
        experiments.start(e["id"])


def test_learning_metrics():
    gid = loop.create_goal("Done first time")
    with session() as s:
        g = s.get(Goal, gid)
        g.status, g.iteration = "approved", 1
    m = training.metrics()
    assert m["first_pass_success_rate"] == 1.0 and m["rework_frequency"] == 1.0


def test_learning_api():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        aid = dev_id()
        assert app.get("/api/learning/gaps").json() == []
        p = app.post("/api/learning/plans", json={"agent_id": aid, "capability": "python"}).json()
        assert p["final_suite"] == "python-final"
        assert app.post(f"/api/learning/plans/{p['id']}/start").json()["status"] == "running"
        assert app.post(f"/api/learning/plans/{p['id']}/cancel").json()["status"] == "cancelled"
        assert app.get("/api/learning/plans").json()[0]["id"] == p["id"]
        assert app.post("/api/experiments", json={"target": "budgets", "candidate": "x"}).status_code == 403
        bad = app.post("/api/experiments", json={"target": "route", "candidate": "Nope", "role": "developer"})
        assert bad.status_code == 400
        e = app.post("/api/experiments", json={"target": "playbook", "candidate": "DO: x", "agent_id": aid}).json()
        assert app.post(f"/api/experiments/{e['id']}/apply").status_code == 400
        assert app.post(f"/api/experiments/{e['id']}/reject", json={"note": "no"}).json()["status"] == "rejected"
        listed = app.get("/api/experiments").json()
        assert listed["experiments"][0]["id"] == e["id"] and listed["autonomy"][-1]["level"] == "E6"
        assert "first_pass_success_rate" in app.get("/api/learning/metrics").json()
        assert "skills" in app.get("/api/certifications").json()[0]
        kinds = {x["name"]: x["kind"] for x in app.get("/api/evals").json()["suites"]}
        assert kinds["python-final"] == "final" and kinds["smoke"] == "benchmark"


def test_experiments_record_their_commit_and_stop_on_their_budget(monkeypatch):
    """ASEAO10 S10-R-02: reproducible (the platform's commit) and stopped on a budget (AT-025, AT-026)."""
    from agent_company.db import EvalRun, session

    router.add_model({"name": "New", "kind": "llm", "model_id": "openai/new"})
    e = experiments.propose("route", "New", role="developer", repeats=3, budget_usd=0.25)
    assert e["dataset"]["budget_usd"] == 0.25 and len(e["dataset"]["source_commit"]) == 40
    with pytest.raises(ValueError):
        experiments.propose("route", "New", role="developer", budget_usd=-1)
    ran = []

    def run_goal(gid):  # every eval run costs $0.10
        ran.append(gid)
        loop._update_goal(gid, status="planning", reason="test")
        loop._update_goal(gid, status="failed", reason="test")

    monkeypatch.setattr(loop, "run_goal", run_goal)
    monkeypatch.setattr(evaluation, "label_cost", lambda label: 0.10 * len(ran))
    out = evaluation.run_suite("smoke", "budgeted", repeats=3, budget_usd=0.25)
    assert out["stopped_on_budget"] and len(ran) == 3, "stops before the next run once the label cost $0.25"
    with session() as s:
        assert s.query(EvalRun).filter(EvalRun.label == "budgeted").count() == 3

    monkeypatch.setattr(experiments, "_spawn", lambda fn: fn())
    calls = []

    def suite(suite, label, repeats=3, only=None, trial=None, budget_usd=0.0):
        calls.append(budget_usd)
        return {"label": label, "runs": 2, "passed": 1, "pass_rate": 0.5, "avg_iterations": 1.0, "avg_seconds": 1.0,
                "total_cost": 0.2, "cost_per_passed": 0.2, "stopped_on_budget": len(calls) == 2}

    monkeypatch.setattr(evaluation, "run_suite", suite)
    experiments.start(e["id"])
    out = experiments.get(e["id"])
    assert calls == [0.25, pytest.approx(0.05)], "the candidate gets what the baseline left"
    assert out["status"] == "failed" and out["decision"].startswith("stopped on its budget")
    assert "candidate" in out["decision"]
