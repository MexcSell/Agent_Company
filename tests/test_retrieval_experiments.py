"""SPEC-13 S13-R-05 `retrieval` target: candidate ranking weights against the current ones on both retrieval evals
and the fixed retrieval benchmark; an administrator applies better weights and can roll them back."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import experiments, hypotheses, learning, prefs
from agent_company.api import create_app
from agent_company.db import AuditEvent, Setting, session


def _run(exp_id: int) -> dict:
    experiments.start(exp_id)  # the background thread is replaced in tests: run it here
    return experiments.run_retrieval(exp_id)


def test_parse_weights_accepts_pairs_and_json_and_refuses_unknown_names():
    base = experiments._current_weights()
    assert experiments.parse_weights("relevance=2.5, recency=0.6", base) == {"relevance": 2.5, "recency": 0.6}
    assert experiments.parse_weights('{"author": 0.1, "relevance": 3}', base) == {"author": 0.1}
    with pytest.raises(ValueError, match="unknown ranking weights: magic"):
        experiments.parse_weights("magic=1", base)
    with pytest.raises(ValueError, match="between 0 and 10"):
        experiments.parse_weights("relevance=11", base)
    with pytest.raises(ValueError, match="changes no ranking weight"):
        experiments.parse_weights("relevance=3", base)
    with pytest.raises(ValueError, match="not name=value"):
        experiments.parse_weights("relevance", base)


def test_better_weights_are_recommended_applied_by_a_person_and_rolled_back():
    prefs.set_weights({"relevance": 0.2}, "test", check=False)  # a poor configuration to improve on
    e = experiments.propose("retrieval", "relevance=3", hypothesis="keyword relevance matters most",
                            created_by="user:luca")
    assert e["dataset"]["suite"] == "retrieval" and e["candidate"]["changes"] == {"relevance": 3.0}
    out = _run(e["id"])
    r = out["results"]
    assert out["status"] == "review" and r["comparison"]["better"], r["comparison"]
    assert r["candidate"]["mrr_all"] > r["baseline"]["mrr_all"] and r["baseline"]["runs"] == r["candidate"]["runs"] > 0
    assert r["candidate"]["benchmark"]["metrics"]["mrr"] > r["baseline"]["benchmark"]["metrics"]["mrr"]
    assert hypotheses.list_hypotheses(experiment_id=e["id"])[0]["status"] == "supported"

    with pytest.raises(PermissionError, match="administrator"):
        experiments.apply(e["id"], "system")  # E5: never the system's own rule
    out = experiments.apply(e["id"], "user:luca")
    assert out["status"] == "rolled_out" and learning.weights()["relevance"] == 3.0
    assert out["results"]["deployed"]["previous"] == {"relevance": 0.2}

    out = experiments.rollback(e["id"], "user:luca")
    assert out["status"] == "rolled_back" and learning.weights()["relevance"] == 0.2
    with pytest.raises(ValueError, match="no applied ranking weights"):
        experiments.rollback(e["id"], "user:luca")


def test_rollback_clears_an_override_that_was_not_there_before():
    prefs.set_weights({"relevance": 0.2}, "test", check=False)
    e = experiments.propose("retrieval", "relevance=3, recency=0.3", created_by="user:luca")
    assert e["candidate"]["changes"] == {"relevance": 3.0}  # recency is unchanged
    _run(e["id"])
    experiments.apply(e["id"], "user:luca")
    prefs.set_weights({"relevance": None}, "test", check=False)  # someone sets them again on Settings
    prefs.set_weights({"relevance": 3.0}, "test", check=False)
    experiments.rollback(e["id"], "user:luca")  # same values: allowed
    with session() as s:
        assert s.get(Setting, "retrieval_w_relevance").value == {"v": 0.2}

    prefs.set_weights({"relevance": None}, "test", check=False)
    e = experiments.propose("retrieval", "author=0.4", created_by="user:luca")
    with session() as s:  # pretend it was measured better, then apply and roll back
        experiments.update(s, e["id"], status="review", comparison={"better": True})
    experiments.apply(e["id"], "user:luca")
    with session() as s:
        assert s.get(Setting, "retrieval_w_author").value == {"v": 0.4}
    experiments.rollback(e["id"], "user:luca")
    with session() as s:
        assert s.get(Setting, "retrieval_w_author") is None
    assert learning.weights()["author"] == learning.WEIGHTS["author"]


def test_worse_weights_are_held_back_and_cannot_be_applied():
    e = experiments.propose("retrieval", "relevance=0.2", hypothesis="less keyword weight helps concepts",
                            created_by="user:luca")
    out = _run(e["id"])
    cmp = out["results"]["comparison"]
    assert out["status"] == "review" and not cmp["better"]
    assert any("recall@5" in h for h in cmp["held_back"]) and any("benchmark" in h for h in cmp["held_back"])
    assert out["decision"].startswith("keep the current ranking weights")
    assert hypotheses.list_hypotheses(experiment_id=e["id"])[0]["status"] == "rejected"
    with pytest.raises(ValueError, match="did not show better weights"):
        experiments.apply(e["id"], "user:luca")
    assert learning.weights()["relevance"] == learning.WEIGHTS["relevance"]


def test_a_benchmark_that_does_not_run_holds_the_candidate_back(monkeypatch):
    from agent_company import selfevolve

    prefs.set_weights({"relevance": 0.2}, "test", check=False)

    def broken(*a, **k):
        raise RuntimeError("not run: the sandbox is required and unavailable")

    monkeypatch.setattr(selfevolve, "run_benchmark", broken)
    e = experiments.propose("retrieval", "relevance=3", created_by="user:luca")
    cmp = _run(e["id"])["results"]["comparison"]
    assert not cmp["better"] and any("did not run" in h for h in cmp["held_back"])


def test_weights_changed_since_the_proposal_stop_it_and_block_the_apply():
    prefs.set_weights({"relevance": 0.2}, "test", check=False)
    e = experiments.propose("retrieval", "relevance=3", created_by="user:luca")
    prefs.set_weights({"recency": 0.5}, "test", check=False)
    out = _run(e["id"])
    assert out["status"] == "failed" and "changed after it was proposed" in out["decision"]

    prefs.set_weights({"recency": None}, "test", check=False)
    e = experiments.propose("retrieval", "relevance=3", created_by="user:luca")
    _run(e["id"])
    prefs.set_weights({"recency": 0.5}, "test", check=False)
    with pytest.raises(ValueError, match="changed since"):
        experiments.apply(e["id"], "user:luca")


def test_set_weights_guards_the_change_as_a_whole():
    with pytest.raises(ValueError, match="lower recall@5"):
        prefs.set_weights({"relevance": 0.2, "recency": 3}, "user:luca")
    assert learning.weights() == learning.WEIGHTS
    with pytest.raises(ValueError, match="unknown ranking weights"):
        prefs.set_weights({"magic": 1}, "user:luca")


def test_api_proposes_applies_and_rolls_back_for_administrators_only():
    prefs.set_weights({"relevance": 0.2}, "test", check=False)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        e = c.post("/api/experiments", json={"target": "retrieval", "candidate": "relevance=3"}).json()
        assert e["target"] == "retrieval" and e["deploy_by"] == "administrator"
        c.post(f"/api/experiments/{e['id']}/start")
        experiments.run_retrieval(e["id"])
        out = c.post(f"/api/experiments/{e['id']}/apply").json()
        assert out["status"] == "rolled_out" and learning.weights()["relevance"] == 3.0
        out = c.post(f"/api/experiments/{e['id']}/rollback").json()
        assert out["status"] == "rolled_back" and learning.weights()["relevance"] == 0.2
        with session() as s:
            actions = {a.action: a.severity for a in s.scalars(select(AuditEvent))}
        assert actions["experiment_apply"] == "high" and actions["experiment_rollback"] == "high"
