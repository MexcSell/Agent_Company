"""SE-R-21 (guide §9 version 4): the ranking procedure per query class, learned offline on evals/retrieval-classes.json
and kept only while recall@5 of evals/retrieval.json holds."""

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import learning, prefs, registry
from agent_company.api import create_app
from agent_company.db import AuditEvent, Event, session


@pytest.mark.parametrize("query,cls", [
    ("learning.retrieve", "exact"), ("run_dag", "exact"), ("set_status()", "exact"),
    ("RFC 9110", "document"), ("ADR-0007", "document"), ("see docs/threat-model.md", "document"),
    ("newest crawler release notes", "recent"), ("latest pip-audit advisories", "recent"),
    ("how should we rank research results combining keywords embeddings and reranking", "complex"),
    ("database is locked when writing from a session in SQLite and the retry gives up after three attempts", "complex"),
    ("semantic similarity search over passages", "concept"),
])
def test_queries_are_classed_by_their_form(query, cls):
    assert learning.query_class(query) == cls


def test_the_classifier_agrees_with_every_label_of_the_class_eval():
    r = learning.strategy_report()
    assert r["classifier"]["n"] == r["cases"] and r["classifier"]["agree"] == r["cases"]
    assert set(r["table"]) == set(learning.QUERY_CLASSES)
    assert all(set(row) == set(learning.STRATEGIES) and all(m["n"] >= 2 for m in row.values())
               for row in r["table"].values())


def test_fresh_puts_the_newer_of_two_matching_items_first():
    r = learning.strategy_report()["table"]["recent"]
    assert r["balanced"]["mrr"] < r["fresh"]["mrr"] == 1.0  # bm25 favours the shorter, older notes


def test_literal_ranks_the_item_naming_the_function_first(tmp_path):
    data = {"items": [{"id": 1, "text": "learning.retrieve is the entry point of the knowledge package of a task"},
                      {"id": 2, "text": "Retrieve what learning found"}],  # newer and shorter: bm25 prefers it
            "cases": [{"query": "learning.retrieve", "expected": [1]}]}
    path = tmp_path / "e.json"
    path.write_text(json.dumps(data))
    assert learning.evaluate_retrieval(path, strategies={})["cases"][0]["top"][0] == 2
    lit = learning.evaluate_retrieval(path, strategies={"exact": "literal"})["cases"][0]
    assert lit["top"][0] == 1 and lit["class"] == "exact" and lit["strategy"] == "literal"


def test_learning_applies_the_choices_that_keep_recall_and_logs_the_table():
    out = learning.learn_strategies("user:luca")
    assert out["applied"] == {"recent": "fresh", "complex": "fresh"}
    assert out["guard"]["after"] >= out["guard"]["before"]
    assert learning.strategy_map() == out["applied"]
    assert next(d for d in prefs.describe() if d["key"] == "retrieval_strategies")["value"] == [
        "complex=fresh", "recent=fresh"]
    with session() as s:
        ev = s.scalars(select(Event).where(Event.kind == "retrieval", Event.message.like("retrieval strategies%"))).one()
        assert ev.data["table"]["recent"]["fresh"]["n"] == 2


def test_a_map_that_lowers_recall_is_refused(monkeypatch):
    real = learning.evaluate_retrieval

    def worse(path=None, k=5, strategies=None, **o):
        out = real(path, k, strategies, **o)
        if strategies and strategies.get("concept") == "fresh":
            out["value"] -= 0.1
        return out

    monkeypatch.setattr(learning, "evaluate_retrieval", worse)
    with pytest.raises(ValueError, match="lowers recall@5"):
        prefs.set_value("retrieval_strategies", ["concept=fresh"], actor="user:luca")
    assert learning.strategy_map() == {}
    with pytest.raises(ValueError, match="not <class>=<strategy>"):
        prefs.set_value("retrieval_strategies", ["exact=magic"])
    with pytest.raises(ValueError, match="named twice"):
        prefs.set_value("retrieval_strategies", ["exact=literal", "exact=broad"])


def test_retrieve_uses_the_strategy_of_the_querys_class():
    dev = registry.staff("developer").id
    learning.learn(dev, None, "Pin the parser version in the notes for registry staff.", True, ["python"])
    learning.learn(dev, None, "registry.staff picks agents by proficiency.", True, ["python"])
    prefs.set_value("retrieval_strategies", ["exact=literal"], actor="user:luca")
    assert learning.retrieve(dev, query="registry.staff", limit=1)[0]["text"].startswith("registry.staff")
    with session() as s:
        assert s.scalars(select(Event).where(Event.kind == "retrieval", Event.message.like("retrieval_strategies =%"))
                         ).one().data["after"] >= 0.9


def test_api_reports_and_learns_for_administrators():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        r = c.get("/api/knowledge/retrieval").json()
        assert r["guard"]["metric"] == "recall@5" and "fresh" in r["strategies"] and r["current"] == {}
        out = c.post("/api/knowledge/retrieval/learn").json()
        assert out["applied"] and c.get("/api/knowledge/retrieval").json()["current"] == out["applied"]
        with session() as s:
            assert s.scalars(select(AuditEvent).where(AuditEvent.action == "retrieval_strategies_learn")).one()
