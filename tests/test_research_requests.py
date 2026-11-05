"""Richer research requests (ASEAO06 S06-R-01): scope, keywords, source preferences, priority, budget, deadline,
recurrence and duplicate detection."""

import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import blackboard, executors, governance, loop, research
from agent_company.api import create_app
from agent_company.db import Document, ResearchReport, now, session


def doc(url, title, text):
    with session() as s:
        s.add(Document(url=url, title=title, text=text, source_url=url, category=research.classify_source(url),
                       flags=[]))


def test_a_request_carries_its_scope_and_terms():
    gid = loop.create_goal("Pick a queue")
    out = research.submit("Which message queue fits small teams?", goal_id=gid, keywords=["rabbitmq", "nats"],
                          source_preferences="official", priority="high", budget_usd=0.5,
                          deadline=(now() + timedelta(days=2)).isoformat(), recurring="weekly")
    assert out["duplicate"] is False
    [r] = research.list_reports(goal_id=gid)
    assert r["keywords"] == ["rabbitmq", "nats"] and r["source_preferences"] == "official"
    assert r["priority"] == "high" and r["budget_usd"] == 0.5 and r["recurring"] == "weekly" and r["deadline"]
    for bad in ({"source_preferences": "blogs"}, {"priority": "urgent"}, {"recurring": "yearly"},
                {"deadline": (now() - timedelta(hours=1)).isoformat()}, {"goal_id": 9999}):
        with pytest.raises(ValueError):
            research.submit("Another question entirely?", **bad)


def test_an_overlapping_request_returns_the_existing_one():
    first = research.submit("How do CrewAI teams and roles work?", keywords=["crewai"])
    again = research.submit("how do crewai teams and roles work", keywords=[])
    assert again == {"id": first["id"], "duplicate": True}
    similar = research.submit("CrewAI roles and teams: how do they work?")
    assert similar["id"] == first["id"] and similar["duplicate"]
    other = research.submit("Which vector database scales to a billion rows?")
    assert other["id"] != first["id"] and not other["duplicate"]
    with session() as s:  # a failed request does not hold the question
        s.get(ResearchReport, other["id"]).status = "failed"
    assert not research.submit("Which vector database scales to a billion rows?")["duplicate"]


def test_an_old_finished_report_no_longer_answers_a_new_request():
    first = research.submit("What changed in Python 3.13 typing?")
    with session() as s:
        r = s.get(ResearchReport, first["id"])
        r.status, r.created_at = "done", now() - timedelta(days=research.RECENT_DAYS + 1)
    assert not research.submit("What changed in Python 3.13 typing?")["duplicate"]


def test_preferred_kinds_of_source_come_first_and_the_report_reaches_the_goal(monkeypatch):
    doc("https://blog.example/queues", "Queues compared", "message queue comparison for small teams " * 20)
    doc("https://docs.python.org/queue", "queue docs", "message queue reference " * 3)
    seen = []
    real = executors.OfflineExecutor.run

    def run(self, req):
        if req.purpose == "report":
            seen.append(req)
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "run", run)
    gid = loop.create_goal("Queue")
    rid = research.request_report("Which message queue for small teams?", goal_id=gid, source_preferences="official",
                                  keywords=["queue"])
    out = research.run_report(rid)
    assert out["status"] == "done" and out["runs"] == 1
    assert out["sources"][0]["url"] == "https://docs.python.org/queue"  # official documentation first
    assert "Prefer official sources" in seen[0].prompt and "Keywords: queue" in seen[0].prompt
    entry = [e for e in blackboard.read(gid, ["research"]) if e["key"] == f"report:{rid}"]
    assert entry and entry[0]["sources"][0] == "https://docs.python.org/queue"


def test_a_request_past_its_deadline_expires_instead_of_running():
    rid = research.request_report("Is the old API deprecated?", deadline=now() + timedelta(hours=1))
    with session() as s:
        s.get(ResearchReport, rid).deadline = now() - timedelta(minutes=1)
    out = research.run_report(rid)
    assert out["status"] == "expired" and out["runs"] == 0


def test_due_requests_run_by_priority_and_recurring_ones_come_back():
    low = research.request_report("Low priority question here?", priority="low")
    crit = research.request_report("Critical outage root causes?", priority="critical")
    soon = research.request_report("Which license fits our SDK release?", deadline=now() + timedelta(hours=2))
    late = research.request_report("How are browser extensions reviewed by stores?")
    assert research.due_reports() == [crit, soon, late, low]
    assert research.run_due_reports(limit=1) == [crit]
    weekly = research.request_report("Weekly agent framework news?", recurring="weekly", priority="critical")
    assert research.run_due_reports(limit=1) == [weekly]
    with session() as s:
        r = s.get(ResearchReport, weekly)
        assert r.status == "done" and r.next_run_at is not None
        r.next_run_at = now() - timedelta(seconds=1)  # a week later
    assert research.due_reports()[0] == weekly
    research.run_report(weekly)
    with session() as s:  # one document per request, replaced by the latest report
        assert len(list(s.scalars(select(Document).where(Document.url == f"report://{weekly}")))) == 1
        assert s.get(ResearchReport, weekly).runs == 2


def test_a_used_budget_ends_a_recurring_request():
    rid = research.request_report("Monthly pricing changes of model providers?", recurring="monthly", budget_usd=0.1)
    with session() as s:
        r = s.get(ResearchReport, rid)
        r.status, r.cost_usd, r.next_run_at = "done", 0.1, now() - timedelta(seconds=1)
    out = research.run_report(rid)
    assert out["next_run_at"] is None and out["runs"] == 0
    assert rid not in research.due_reports()


def test_an_emergency_stop_holds_due_requests():
    rid = research.request_report("Anything new on agent protocols?")
    governance.stop("E4", "information_collection", "admin", "test")
    assert research.run_due_reports() == []
    with session() as s:
        assert s.get(ResearchReport, rid).status == "queued"


def test_a_running_request_is_not_run_twice():
    rid = research.request_report("Which logging library is fastest?")
    with session() as s:
        s.get(ResearchReport, rid).status = "running"
    assert research.run_report(rid)["runs"] == 0


def test_the_api_takes_the_new_fields_and_answers_duplicates(monkeypatch):
    monkeypatch.setattr(research, "run_report", lambda rid, **kw: None)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        body = {"question": "Which CI service suits a monorepo?", "keywords": ["ci"], "priority": "high",
                "recurring": "daily", "source_preferences": "open_source"}
        first = app.post("/api/research/requests", json=body).json()
        assert first["duplicate"] is False
        assert app.post("/api/research/requests", json=body).json() == {"id": first["id"], "duplicate": True}
        assert app.post("/api/research/requests", json={**body, "question": "Something else?",
                                                        "priority": "now"}).status_code == 400
        [r] = app.get("/api/research/requests").json()
        assert r["priority"] == "high" and r["recurring"] == "daily" and r["keywords"] == ["ci"]
