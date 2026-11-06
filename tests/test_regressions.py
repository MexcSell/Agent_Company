"""G-R-11: an improvement must not break what the baseline solved (research guide: 'improve performance on a fixed
test set without damaging existing capabilities')."""

from agent_company import evaluation, experiments, orchestration
from agent_company.db import EvalRun, session


def side(pass_rate, solved, ran=("a", "b", "c", "d"), cost=1.0):
    return {"runs": len(ran), "passed": len(solved), "pass_rate": pass_rate, "cost_per_passed": cost,
            "tasks_run": sorted(ran), "solved_tasks": sorted(solved)}


def test_a_higher_pass_rate_that_breaks_a_solved_task_is_not_better():
    base = side(0.5, ["a", "b"])
    swapped = experiments.compare(base, side(0.75, ["a", "c", "d"]))
    assert swapped["better"] is False and swapped["regressions"] == ["b"] and swapped["regression_rate"] == 0.5
    kept = experiments.compare(base, side(0.75, ["a", "b", "c"]))
    assert kept["better"] is True and kept["regressions"] == [] and kept["regression_rate"] == 0.0
    skipped = experiments.compare(base, side(0.75, ["a", "c", "d"], ran=("a", "c", "d")))
    assert skipped["regressions"] == [], "a task the candidate did not run is no evidence of a regression"
    old = {"runs": 4, "passed": 2, "pass_rate": 0.5}  # recorded before the per-task record
    legacy = experiments.compare(old, side(0.75, ["c", "d", "a"]))
    assert legacy["better"] is True and legacy["regressions"] == [] and legacy["regression_rate"] is None


def test_side_metrics_keep_the_tasks_run_and_solved():
    m = experiments.side_metrics({"runs": 2, "pass_rate": 0.5, "by_task": [{"task": "y", "solved": False},
                                                                             {"task": "x", "solved": True}]})
    assert m["tasks_run"] == ["x", "y"] and m["solved_tasks"] == ["x"]
    assert experiments.regressions(m, {"pass_rate": 1.0}) is None


def test_a_cheaper_template_that_breaks_a_task_is_not_better():
    base = side(0.5, ["a", "b"], cost=1.0)
    cheaper = orchestration.compare(base, side(0.5, ["a", "c"], cost=0.5))
    assert cheaper["better"] is False and any(g.startswith("breaks b") for g in cheaper["quality_guards"])
    same_tasks = orchestration.compare(base, side(0.5, ["a", "b"], cost=0.5))
    assert same_tasks["better"] is True and same_tasks["why"].startswith("efficiency")


def test_eval_compare_lists_the_regressions():
    with session() as s:
        for task, a_ok, b_ok in (("a", [True, True], [True, True]), ("b", [True, True], [True, False]),
                                 ("c", [False, False], [True, True])):
            for rep, (pa, pb) in enumerate(zip(a_ok, b_ok), 1):
                s.add(EvalRun(label="v1", suite="mini", task=task, repeat=rep, passed=pa))
                s.add(EvalRun(label="v2", suite="mini", task=task, repeat=rep, passed=pb))
    out = evaluation.compare("v1", "v2")
    assert out["pass_rate_change"] > 0 and out["regressions"] == ["b"] and out["regression_rate"] == 0.5
