"""Verification phase 8: continuous evolution, offline. The developer's playbook is the agent version that evolves
(`developer-agent-v1` → a `v2-candidate`); the candidate runs the same fixed suite as the current version, a
regression on a task the current version solved rejects it, a passing one goes on to a live canary, and either the
candidate is promoted with the old version kept, or it is rolled back. The server's own history of 20 experiments is
in the report. Report: docs/verification/phase-8-evolution.md."""
import pytest
from sqlalchemy import select

from agent_company import evaluation, evolution, experiments, learning
from agent_company.db import Agent, Experiment, PlaybookVersion, session

DIMS = {"completion": {"n": 3, "passed": 2, "pass_rate": 0.667},
        "compliance": {"n": 3, "compliant_rate": 1.0, "violations": 0, "contract_failures": 0},
        "tool_use": {"n": 3, "tool_calls": 40, "failed_calls": 2, "accuracy": 0.95, "denied_tools": 0,
                     "refused_commands": 0, "failed_runs": 0},
        "efficiency": {"n": 3, "avg_seconds": 45.0, "avg_cost": 0.1, "avg_tokens": 12000, "avg_iterations": 1.3,
                       "cost_per_passed": 0.15}}


def _developer():
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "developer", Agent.parent_id.is_(None))).first().id


def _candidate(aid):
    """8.1/8.2: a v1 playbook, then failures that make the platform propose a candidate (the agent's improvement)."""
    learning.learn(aid, None, "Run the tests before reporting.", True, ["python"])
    with session() as s:
        v1 = (s.get(Agent, aid).playbook, s.get(Agent, aid).playbook_version)
    for i in range(4):
        learning.learn(aid, None, f"Broke the build {i}.", False, [])
    with session() as s:
        return v1, dict(s.get(Agent, aid).experiment)


def _suite(monkeypatch, by_side, dims=DIMS):
    seen = []

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        side = label.split("-")[2]
        seen.append((suite, side, repeats))
        tasks = by_side[side]
        passed = sum(ok for _, ok in tasks)
        return {"label": label, "runs": len(tasks), "passed": passed, "pass_rate": round(passed / len(tasks), 3),
                "avg_iterations": 1.0, "avg_seconds": 40.0, "total_cost": 0.3, "cost_per_passed": 0.1,
                "by_task": [{"task": t, "solved": ok} for t, ok in tasks], "dimensions": dims}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    return seen


def test_the_candidate_is_kept_apart_from_v1_and_both_run_the_same_suite(monkeypatch):
    monkeypatch.setattr(evolution, "_spawn", lambda fn: None)
    aid = _developer()
    (v1_text, v1_version), exp = _candidate(aid)
    assert exp["stage"] == "smoke" and exp["playbook"] != v1_text
    # 8.3: the candidate is stored beside v1, never over it
    with session() as s:
        a = s.get(Agent, aid)
        assert (a.playbook, a.playbook_version) == (v1_text, v1_version)
        assert s.scalars(select(PlaybookVersion).where(PlaybookVersion.agent_id == aid,
                                                       PlaybookVersion.version == v1_version)).first()
    seen = _suite(monkeypatch, {"baseline": [("a", True), ("b", True), ("c", False)],
                                "candidate": [("a", True), ("b", True), ("c", True)]})
    evolution.smoke_trial(aid)
    # 8.4: the same suite, the same repeats, for v1 and the candidate
    assert [(suite, side) for suite, side, _ in seen] == [("smoke", "baseline"), ("smoke", "candidate")]
    assert seen[0][2] == seen[1][2]
    # 8.1: what is recorded per side: correct and failed tasks, compliance, time, tokens, cost, tool failures
    with session() as s:
        e = s.get(Experiment, exp["experiment_id"])
        base, cand = e.results["baseline"], e.results["candidate"]
    assert base["solved_tasks"] == ["a", "b"] and cand["solved_tasks"] == ["a", "b", "c"]
    for side in (base, cand):
        assert side["dimensions"]["efficiency"]["avg_tokens"] == 12000, "tokens (V27)"
        assert side["dimensions"]["tool_use"]["failed_calls"] == 2, "tool failures (V27)"
        assert side["dimensions"]["compliance"]["compliant_rate"] == 1.0
        assert {"avg_seconds", "total_cost", "cost_per_passed", "pass_rate"} <= set(side)
    assert e.status == "canary" and e.results["comparison"]["regressions"] == []


def test_a_candidate_that_breaks_what_v1_solved_is_rejected_and_v1_stays(monkeypatch):
    """8.5 / 8.6: more tasks passed, but one v1 solved is broken: rejected, v1 kept."""
    monkeypatch.setattr(evolution, "_spawn", lambda fn: None)
    aid = _developer()
    (v1_text, v1_version), exp = _candidate(aid)
    _suite(monkeypatch, {"baseline": [("a", True), ("b", False), ("c", False)],
                         "candidate": [("a", False), ("b", True), ("c", True)]})
    evolution.smoke_trial(aid)
    with session() as s:
        a, e = s.get(Agent, aid), s.get(Experiment, exp["experiment_id"])
        assert a.experiment == {} and (a.playbook, a.playbook_version) == (v1_text, v1_version)
        assert e.status == "rolled_back" and e.results["comparison"]["regressions"] == ["a"]
        assert "breaks a" in e.decision


@pytest.mark.parametrize("live,promoted", [([True, True, True], True), ([False, False, False], False)])
def test_the_live_canary_promotes_or_rolls_back_and_v1_can_be_restored(monkeypatch, live, promoted):
    """8.6: after the suite, the candidate works live for a window; promoted as a new version (v1 kept and restorable)
    or rolled back automatically."""
    monkeypatch.setattr(evolution, "_spawn", lambda fn: None)
    aid = _developer()
    (v1_text, v1_version), exp = _candidate(aid)
    _suite(monkeypatch, {"baseline": [("a", True), ("b", False)], "candidate": [("a", True), ("b", True)]})
    evolution.smoke_trial(aid)
    for i, ok in enumerate(live):
        learning.learn(aid, None, f"Live task {i}.", ok, [])
    with session() as s:
        a, e = s.get(Agent, aid), s.get(Experiment, exp["experiment_id"])
        assert a.experiment == {}
        assert (e.status == "rolled_out") is promoted and (e.status == "rolled_back") is not promoted
        assert (a.playbook == exp["playbook"]) is promoted
        assert (a.playbook_version > v1_version) is promoted
    versions = {v["version"]: v["playbook"] for v in evolution.versions(aid)}
    assert versions[v1_version] == v1_text, "v1 is kept"
    if promoted:  # a person can go back to v1 at any time
        evolution.restore(aid, v1_version)
        with session() as s:
            assert s.get(Agent, aid).playbook == v1_text


def test_the_regression_guard_compares_the_tasks_not_the_rates():
    """8.5: G-R-11 on the stored sides of an experiment."""
    base = experiments.side_metrics({"pass_rate": 0.5, "by_task": [{"task": "x", "solved": True},
                                                                   {"task": "y", "solved": False}]})
    cand = experiments.side_metrics({"pass_rate": 1.0, "by_task": [{"task": "x", "solved": False},
                                                                   {"task": "y", "solved": True},
                                                                   {"task": "z", "solved": True}]})
    assert experiments.regressions(base, cand) == ["x"]
