"""SPEC-04 S04-R-13 (FR-27, FR-28, AT-031..037): self-evaluation of the workflow templates, recommended candidates
as strategy experiments, the §20.3 policy, an administrator's rollout and the canary's rollback."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import evaluation, experiments, loop, orchestration, strategies, workflow
from agent_company.api import create_app
from agent_company.db import AuditEvent, Experiment, Goal, Hypothesis, session


def decide(kind, status, iteration=1, max_iterations=3, origin="user", **kw):
    """A goal of the kind (the order chose it, so its rules applied) that ends with `status`."""
    gid = loop.create_goal(f"{kind} goal", kind=kind, origin=origin, max_iterations=max_iterations, **kw)
    with session() as s:
        g = s.get(Goal, gid)
        g.iteration = iteration
        workflow.set_status(s, g, "running")
        if status == "rejected":
            workflow.set_status(s, g, "awaiting_approval")
        workflow.set_status(s, g, status)
    return gid


def fake_suite(monkeypatch, rates, cost=None):
    calls = []

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        calls.append({"suite": suite, "repeats": repeats, "trial": trial})
        side = trial["side"]
        rate = rates[side]
        c = (cost or {}).get(side, 0.0)
        passed = round(rate * 2 * repeats)
        dims = {"compliance": {"compliant_rate": 1.0}, "security": {"secure_rate": 1.0}}
        return {"label": label, "runs": 2 * repeats, "passed": passed, "pass_rate": rate, "avg_iterations": 1.0,
                "avg_seconds": 1.0, "total_cost": c * passed, "cost_per_passed": c if passed else None,
                "dimensions": dims}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    return calls


def strategy_experiments():
    return [e for e in experiments.list_experiments(target="strategy")]


def test_the_report_covers_each_version_with_sample_sizes_and_only_goals_that_ran_under_its_rules():  # AT-031
    decide("feature", "approved")
    decide("feature", "approved", iteration=2)
    decide("feature", "failed", iteration=3)
    decide("feature", "approved", origin="eval")  # a trial, not live evidence
    gid = loop.create_goal("Fix the login crash")  # an inferred label is not the template's rules
    strategies.choose(gid, "feature", "inferred")
    with session() as s:
        g = s.get(Goal, gid)
        workflow.set_status(s, g, "running")
        workflow.set_status(s, g, "failed")
    report = orchestration.evaluate("feature")[0]
    cur = report["current"]
    assert cur["version"] == 1 and cur["n"] == 3 and cur["accepted"] == 2
    assert cur["acceptance_rate"] == 0.667 and cur["first_pass_rate"] == 0.333
    assert cur["at_limit"] == 1 and cur["avg_iterations"] == 2.0
    assert "bottlenecks" in cur and cur["minutes_n"] == 3
    assert not report["enough"] and report["recommendation"]["changes"] == {}
    assert "5 are needed" in report["recommendation"]["reasons"][0]
    assert {r["kind"] for r in orchestration.evaluate()} == set(strategies.KINDS)


def test_goals_running_out_of_iterations_lead_to_one_recorded_candidate():  # AT-032
    for status, it in (("approved", 1), ("approved", 1), ("failed", 3), ("approved", 2)):
        decide("feature", status, iteration=it)
    assert strategy_experiments() == []  # four goals are not enough evidence
    decide("feature", "failed", iteration=3)  # the fifth: 2 of 5 used every iteration
    [e] = strategy_experiments()
    assert e["status"] == "proposed" and e["role"] == "feature" and e["baseline_version"] == "v1"
    assert e["candidate"]["changes"] == {"max_iterations": 4} and e["candidate"]["body"]["max_iterations"] == 4
    assert e["candidate"]["evidence"]["n"] == 5 and "used all 3 iterations" in e["candidate"]["reasons"][0]
    with session() as s:
        h = s.scalars(select(Hypothesis).where(Hypothesis.experiment_id == e["id"])).one()
        assert h.status == "testing"
    decide("feature", "failed", iteration=3)
    assert len(strategy_experiments()) == 1, "an open experiment of the kind is not proposed twice"
    assert strategies.effective("feature")["version"] == 1, "a recommendation never changes the template itself"


@pytest.mark.parametrize("kind,changes,why", [
    ("feature", '{"review": "none"}', "drops the independent review"),
    ("research", '{"writes": true, "checks": "inherit"}', "turns on file changes"),
    ("refactor", '{"protect_tests": false}', "stops protecting the existing tests"),
    ("docs", '{"allowed_paths": ["*"]}', "widens the paths"),
    ("bugfix", '{"recruit": true}', "recruitment"),
    ("feature", '{"approvals": "none"}', "unknown template field"),
])
def test_a_candidate_against_the_policy_is_rejected(kind, changes, why):  # AT-034
    e = experiments.propose("strategy", changes, role=kind, created_by="user:luca")
    assert e["status"] == "rejected" and why in e["decision"]
    with pytest.raises(ValueError, match="is proposed|is rejected"):
        experiments.start(e["id"])


def test_a_candidate_equal_to_the_template_is_refused():
    with pytest.raises(ValueError, match="itself"):
        experiments.propose("strategy", '{"max_iterations": 3}', role="feature")


def test_an_eval_goal_runs_under_its_sides_template_not_the_one_in_force():
    body = {k: v for k, v in strategies.effective("feature").items() if k in strategies.FIELDS}
    gid = loop.create_goal("x", origin="eval", trial={"strategy": {"kind": "feature", "version": "exp7",
                                                                   "body": body | {"max_iterations": 5}}})
    g = loop._goal(gid)
    assert g.kind == "feature" and g.max_iterations == 5 and g.strategy["version"] == "exp7"
    assert strategies.enforced(g)


def test_both_sides_run_the_same_suite_and_a_better_candidate_waits_for_an_administrator(monkeypatch):  # AT-033, 035
    e = experiments.propose("strategy", '{"max_iterations": 4}', role="feature", repeats=2)
    spawned = []
    monkeypatch.setattr(experiments, "_spawn", spawned.append)
    experiments.start(e["id"])
    calls = fake_suite(monkeypatch, {"baseline": 0.5, "candidate": 1.0})
    spawned[0]()
    assert [c["trial"]["strategy"]["body"]["max_iterations"] for c in calls] == [3, 4]
    assert [c["trial"]["strategy"]["version"] for c in calls] == [1, f"exp{e['id']}"]
    assert {(c["suite"], c["repeats"]) for c in calls} == {("smoke", 2)}
    out = experiments.get(e["id"])
    assert out["status"] == "review" and out["decision"].startswith("recommend the candidate feature template")
    assert strategies.effective("feature")["version"] == 1, "the experiment never changes the template in force"
    with pytest.raises(PermissionError):
        experiments.apply(e["id"], "system")


def test_cheaper_with_quality_not_lower_counts_and_a_cheaper_worse_one_does_not(monkeypatch):  # §20.5
    for rates, better in (({"baseline": 0.5, "candidate": 0.5}, True), ({"baseline": 0.5, "candidate": 0.25}, False)):
        e = experiments.propose("strategy", '{"execution": "sequential"}', role="feature")
        spawned = []
        monkeypatch.setattr(experiments, "_spawn", spawned.append)
        experiments.start(e["id"])
        fake_suite(monkeypatch, rates, cost={"baseline": 1.0, "candidate": 0.5})
        spawned[0]()
        cmp = experiments.get(e["id"])["results"]["comparison"]
        assert cmp["better"] is better, cmp
        experiments.reject(e["id"], "user:luca")


def reviewed(changes='{"max_iterations": 4}', baseline=0.5):
    e = experiments.propose("strategy", changes, role="feature")
    with session() as s:
        experiments.update(s, e["id"], status="review", baseline={"pass_rate": baseline, "runs": 2},
                           comparison={"better": True})
    return e["id"]


def test_an_applied_template_is_a_new_version_and_a_worse_canary_restores_the_previous_one():  # AT-036, AT-037
    exp_id = reviewed()
    out = experiments.apply(exp_id, "user:luca")
    assert out["status"] == "canary" and "feature v2" in out["decision"]
    t = strategies.effective("feature")
    assert t["version"] == 2 and t["max_iterations"] == 4
    c = out["results"]["canary"]
    assert c["previous"] == 1 and c["deployed"] == 2 and c["baseline_source"] == "suite" and c["baseline"] == 0.5
    decide("feature", "awaiting_approval", max_iterations=4)  # not a final word yet: does not count
    assert experiments.get(exp_id)["results"]["canary"]["outcomes"] == {}
    decide("feature", "failed", iteration=4, max_iterations=4)
    decide("feature", "rejected", max_iterations=4)  # 0/2: even a pass now (1/3) is below 50 %
    out = experiments.get(exp_id)
    assert out["status"] == "rolled_back" and "v1 restored" in out["decision"]
    t = strategies.effective("feature")
    assert t["version"] == 3 and t["max_iterations"] == 3


def test_a_canary_that_holds_is_rolled_out():
    exp_id = reviewed()
    experiments.apply(exp_id, "user:luca")
    gid = decide("feature", "failed", max_iterations=4, iteration=4)
    with session() as s:  # retried and approved: the goal's last word counts, once
        g = s.get(Goal, gid)
        workflow.set_status(s, g, "recovering")
        workflow.set_status(s, g, "running")
        workflow.set_status(s, g, "approved")
    decide("feature", "approved", max_iterations=4)
    assert experiments.get(exp_id)["status"] == "canary"
    decide("feature", "approved", max_iterations=4)
    out = experiments.get(exp_id)
    assert out["status"] == "rolled_out" and out["results"]["canary"]["outcomes"] == {
        str(gid): True, str(gid + 1): True, str(gid + 2): True}
    assert strategies.effective("feature")["version"] == 2


def test_a_candidate_measured_against_an_older_version_is_not_applied():
    exp_id = reviewed()
    body = {k: v for k, v in strategies.effective("feature").items() if k in strategies.FIELDS}
    strategies.save("feature", body | {"max_iterations": 2}, "user:luca", "by hand")
    with pytest.raises(ValueError, match="v2 now"):
        experiments.apply(exp_id, "user:luca")
    with session() as s:
        assert s.get(Experiment, exp_id).status == "review"


def test_api_reports_recommends_and_keeps_template_experiments_for_administrators():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        report = c.get("/api/strategies/evaluation").json()
        assert {r["kind"] for r in report} == set(strategies.KINDS)
        assert c.get("/api/strategies/evaluation?kind=nope").status_code == 400
        r = c.post("/api/strategies/feature/recommend").json()
        assert r["recorded"] is None and r["changes"] == {}
        e = c.post("/api/experiments", json={"target": "strategy", "role": "feature",
                                             "candidate": '{"max_iterations": 4}'}).json()
        assert e["status"] == "proposed" and e["target"] == "strategy"
        with session() as s:
            experiments.update(s, e["id"], status="review", comparison={"better": True})
        out = c.post(f"/api/experiments/{e['id']}/apply").json()
        assert out["status"] == "canary" and strategies.effective("feature")["version"] == 2
        with session() as s:
            acts = {a.action: a.severity for a in s.scalars(select(AuditEvent))}
        assert acts.get("experiment_apply") == "high" and "strategy_recommend" in acts
