"""EL-R-07: an archive of experiment candidates with lineage; up to three candidates compete on one suite and only the
best without regressions (G-R-11) is proposed."""

import pytest

from agent_company import evaluation, experiments, router
from agent_company.db import Experiment, session

SOLVED = {"Old": "ab", "A": "acde", "B": "abc", "C": "ab"}  # tasks a..e; baseline 40 %, a candidate needs 50 %


def models(*names):
    for n in ("Old", *names):
        router.add_model({"name": n, "kind": "llm", "model_id": f"openai/{n.lower()}"})
    router.set_route("developer", "Old")


def fake_suite(monkeypatch, cost=0.1):
    calls = []

    def run_suite(suite, label, repeats=3, only=None, trial=None, budget_usd=0.0):
        model = (trial.get("models") or {}).get("developer", "Old")
        calls.append((trial["side"], trial["experiment_id"], model, suite, repeats))
        solved = SOLVED[model]
        return {"label": label, "runs": 5, "passed": len(solved), "pass_rate": len(solved) / 5, "total_cost": cost,
                "cost_per_passed": cost / len(solved),
                "by_task": [{"task": t, "solved": t in solved} for t in "abcde"]}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    monkeypatch.setattr(experiments, "_spawn", lambda fn: fn())
    return calls


def side(rate, solved, cost=None):
    return {"runs": 5, "pass_rate": rate, "cost_per_passed": cost, "tasks_run": list("abcde"),
            "solved_tasks": sorted(solved)}


def test_lineage_and_eval_log_are_stored():
    models("A", "B")
    root = experiments.propose("route", "A", role="developer", hypothesis="A solves more")
    child = experiments.propose("route", "B", role="developer", parent_id=root["id"])
    out = experiments.get(child["id"])
    assert out["parent_id"] == root["id"] and out["lineage"] == [root["id"], child["id"]]
    assert experiments.get(root["id"])["lineage"] == [root["id"]] and root["parent_id"] is None
    with session() as s:
        assert experiments.lineage(s, child["id"]) == [root["id"], child["id"]]
        with pytest.raises(KeyError):
            experiments.lineage(s, 9999)
    assert out["eval_log"][0]["event"] == "proposed" and out["eval_log"][0]["parent_id"] == root["id"]
    assert root["eval_log"][0]["note"] == "A solves more" and "parent_id" not in root["eval_log"][0]

    b, c = side(0.4, "ab", 0.25), side(0.6, "abc", 0.1)
    with session() as s:
        experiments.update(s, child["id"], status="review", decision="recommend B", baseline=b, candidate=c,
                           comparison=experiments.compare(b, c))
    log = experiments.get(child["id"])["eval_log"]
    assert [x["event"] for x in log] == ["proposed", "baseline", "candidate", "comparison", "status"]
    assert log[2]["pass_rate"] == 0.6 and log[2]["solved_tasks"] == ["a", "b", "c"]
    assert log[3]["better"] is True and log[3]["regressions"] == [] and log[4]["status"] == "review"
    with session() as s:
        experiments.update(s, child["id"], canary={"window": 3, "outcomes": [True]})
    assert len(experiments.get(child["id"])["eval_log"]) == 5, "a canary-only update is not logged again"
    with session() as s:  # a new session reads the same lineage and log
        e = s.get(Experiment, child["id"])
        assert e.parent_id == root["id"] and len(e.eval_log) == 5

    seeded = experiments.propose("route", "", role="developer", parent_id=root["id"])
    assert seeded["candidate"] == {"model": "A"} and seeded["lineage"] == [root["id"], seeded["id"]]
    assert experiments.seed(root["id"]) == "A"
    with session() as s:
        strategy = experiments.record(s, "strategy", role="bugfix").id
    for bad in (9999, strategy):
        with pytest.raises(ValueError):
            experiments.propose("route", "B", role="developer", parent_id=bad)
    with pytest.raises(ValueError, match="is for developer, not qa"):
        experiments.propose("route", "B", role="qa", parent_id=root["id"])
    with session() as s:
        assert s.query(Experiment).count() == 4, "a refused parent records nothing"


def test_a_strategy_candidate_records_its_parent():
    root = experiments.propose("strategy", '{"max_iterations": 4}', role="feature")
    child = experiments.propose("strategy", '{"max_iterations": 5}', role="feature", parent_id=root["id"])
    assert child["parent_id"] == root["id"] and child["lineage"] == [root["id"], child["id"]]
    assert child["eval_log"][0]["event"] == "proposed" and child["eval_log"][0]["parent_id"] == root["id"]
    assert experiments.seed(root["id"]) == '{"max_iterations": 4}'
    with pytest.raises(ValueError, match="is for feature, not bugfix"):
        experiments.propose("strategy", '{"max_iterations": 5}', role="bugfix", parent_id=root["id"])


def test_the_best_of_three_without_regressions_is_the_one_proposed(monkeypatch):
    models("A", "B", "C")
    a, b, c = (experiments.propose("route", m, role="developer", repeats=2)["id"] for m in ("A", "B", "C"))
    calls = fake_suite(monkeypatch)
    out = experiments.compete([c, a, b], actor="user:admin")
    assert out["id"] == a and out["winner"]["id"] == b
    assert [x[0] for x in calls].count("baseline") == 1, "one shared baseline run"
    assert sorted(x[1] for x in calls if x[0] == "candidate") == [a, b, c]
    assert {(x[3], x[4]) for x in calls} == {("smoke", 2)}, "the same suite and repeats in every run"
    assert calls[0][:3] == ("baseline", a, "Old")

    got = {i: experiments.get(i) for i in (a, b, c)}
    assert got[b]["status"] == "review" and "competition #" in got[b]["decision"]
    assert got[b]["decision"].startswith("recommend B for developer")
    assert got[a]["status"] == got[c]["status"] == "rejected"
    for i in (a, c):
        assert f"#{b}" in got[i]["decision"] and got[i]["reviewed_by"] == "system:competition"
    assert got[a]["results"]["comparison"]["pass_rate_change"] > got[b]["results"]["comparison"]["pass_rate_change"]
    assert got[a]["results"]["comparison"]["regressions"] == ["b"] and "breaks b" in got[a]["decision"]
    last_a = [x for x in got[a]["eval_log"] if x["event"] == "competition"][-1]
    assert "breaks b" in last_a["note"] and last_a["winner"] == b
    assert "not better" in got[c]["decision"]
    assert got[a]["results"]["baseline"] == got[b]["results"]["baseline"] == got[c]["results"]["baseline"]
    assert all(g["dataset"]["competition"] == a and g["competition"] == a for g in got.values())
    assert all(g["dataset"]["competitors"] == [a, b, c] for g in got.values())
    assert [x["status"] for x in got[b]["eval_log"] if x["event"] == "status"] == ["baseline", "candidate", "review"]
    assert experiments.get_competition(a)["winner"]["id"] == b
    assert router.list_routes()["developer"] == "Old", "the winner waits for an administrator"
    assert not experiments._running


def test_competition_size_and_shape_are_checked():
    models("A", "B", "C", "D")
    ids = [experiments.propose("route", m, role="developer")["id"] for m in ("A", "B", "C", "D")]
    router.set_route("qa", "Old")
    qa = experiments.propose("route", "A", role="qa")["id"]
    more = experiments.propose("route", "B", role="developer", repeats=2)["id"]
    with session() as s:
        playbook = experiments.record(s, "playbook", agent_id=1, role="developer").id
    done = experiments.propose("route", "C", role="developer")["id"]
    experiments.reject(done, "user:admin")
    for bad in (ids, ids[:1], [ids[0], qa], [ids[0], more], [ids[0], done], [ids[0], playbook], [ids[0], ids[0]]):
        with pytest.raises(ValueError):
            experiments.compete(bad)
    with pytest.raises(KeyError):
        experiments.compete([ids[0], 9999])
    with session() as s:
        for e in s.query(Experiment).filter(Experiment.id != done):
            assert e.status == "proposed" and "competition" not in (e.dataset or {})


def test_pick_parent_best_and_diverse():
    assert experiments.pick_parent("route", role="developer") is None
    assert experiments.pick_parent("route", role="developer", mode="diverse") is None
    with pytest.raises(ValueError):
        experiments.pick_parent("route", role="developer", mode="random")
    base = side(0.4, "a")
    rows = {"e1": side(0.6, "ab", 1.0), "e2": side(0.8, "acd"), "e3": side(0.6, "ac", 0.5), "e4": side(0.4, "de")}
    ids = {}
    with session() as s:
        for name, cand in rows.items():
            e = experiments.record(s, "route", role="developer", candidate={"model": name})
            cmp = experiments.compare(base, cand) | ({"regressions": ["x"]} if name == "e2" else {})
            experiments.update(s, e.id, status="review", baseline=base, candidate=cand, comparison=cmp)
            ids[name] = e.id
        ids["e5"] = experiments.record(s, "route", role="developer", candidate={"model": "e5"}).id
        assert [x["id"] for x in experiments.archive(s, "route", role="developer")] == [ids[k] for k in rows]
    assert experiments.pick_parent("route", role="developer") == ids["e3"], "same rate as e1, cheaper; e2 breaks x"
    assert experiments.pick_parent("route", role="developer", mode="diverse") == ids["e4"]
    assert experiments.pick_parent("route", role="qa") is None
    assert experiments.jaccard_distance("de", "ac") == 1.0 and experiments.jaccard_distance([], []) == 0.0

    ok = {"better": True, "regressions": []}
    pick = experiments.select_best
    assert pick([{"id": 1, "candidate": {"pass_rate": .6, "cost_per_passed": 1.0}, "comparison": ok},
                 {"id": 2, "candidate": {"pass_rate": .6, "cost_per_passed": .5}, "comparison": ok}]) == 2
    assert pick([{"id": 3, "candidate": {"pass_rate": .6}, "comparison": ok},
                 {"id": 2, "candidate": {"pass_rate": .6}, "comparison": ok}]) == 2
    assert pick([{"id": 1, "candidate": {"pass_rate": .9}, "comparison": {"better": False, "regressions": []}},
                 {"id": 2, "candidate": {"pass_rate": .5}, "comparison": ok}]) == 2
    assert pick([{"id": 1, "candidate": {"pass_rate": .9}, "comparison": {"better": True, "regressions": ["b"]}},
                 {"id": 2, "candidate": {"pass_rate": .5}, "comparison": {"better": False}}]) is None


def test_a_competition_stops_all_candidates_on_its_shared_budget(monkeypatch):
    models("A", "B")
    a, b = (experiments.propose("route", m, role="developer", budget_usd=0.1)["id"] for m in ("A", "B"))
    calls = fake_suite(monkeypatch, cost=0.15)
    experiments.compete([a, b])
    assert [x[0] for x in calls] == ["baseline", "candidate"], "the second candidate never starts"
    for i in (a, b):
        e = experiments.get(i)
        assert e["status"] == "failed" and "stopped on its budget" in e["decision"]
        assert e["decision"].startswith(f"competition #{a}")
    assert not experiments._running
