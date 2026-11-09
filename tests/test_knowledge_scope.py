"""UI-KN-01: the Knowledge page's scope filter (company, project, room, agent, research, evaluation), and an approved
research report's findings listed as knowledge items while the research room's own notes stay out."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import learning, loop, projects
from agent_company.api import create_app
from agent_company.db import Agent, Lesson, session


def agent_of(role):
    with session() as s:
        a = s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first()
        return a.id, a.room_key


def add(agent_id, text, **kw):
    with session() as s:
        i = Lesson(agent_id=agent_id, text=text, outcome=kw.pop("outcome", "success"), **kw)
        s.add(i)
        s.flush()
        return i.id


def texts(**kw):
    return {i["text"] for i in learning.knowledge(**kw)}


def seeded():
    dev, dev_room = agent_of("developer")
    res, _ = agent_of("research")
    p = projects.create("Scoped")["id"]
    g = loop.create_goal("Benchmark", origin="eval", quality_commands=[])
    add(dev, "company lesson")
    add(dev, "project lesson", project_id=p)
    add(res, "report finding", outcome="research", kind="research")
    add(res, "Source change: docs moved", outcome="research")
    add(dev, "eval lesson", goal_id=g)
    return dev, dev_room, res, p


def test_each_scope_narrows_the_items():
    dev, dev_room, res, p = seeded()
    assert texts(scope="company") == {"company lesson", "report finding", "eval lesson"}
    assert texts(scope="project", project_id=p) == {"project lesson"}
    assert texts(scope="project") == {"project lesson"}, "any project's items"
    assert texts(scope="room", room=dev_room) == {"company lesson", "project lesson", "eval lesson"}
    assert texts(scope="agent", agent_id=res) == {"report finding"}
    assert texts(scope="research") == {"report finding"}
    assert texts(scope="evaluation") == {"eval lesson"}
    with pytest.raises(ValueError):
        learning.knowledge(scope="room")
    with pytest.raises(ValueError):
        learning.knowledge(scope="everything")


def test_research_findings_are_items_and_the_rooms_notes_are_not():
    _, _, res, _ = seeded()
    assert "report finding" in texts() and "Source change: docs moved" not in texts()
    finding = next(i for i in learning.knowledge(agent_id=res) if i["text"] == "report finding")
    assert learning.item(finding["id"])["kind"] == "research"
    assert learning.review(finding["id"], False, "user:admin", "outdated")["status"] == "rejected"
    with session() as s:
        note = s.scalars(select(Lesson.id).where(Lesson.text.like("Source change:%"))).first()
    with pytest.raises(KeyError):
        learning.item(note)


def test_the_api_takes_the_scope_and_refuses_an_unknown_one():
    _, dev_room, _, p = seeded()
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        got = app.get(f"/api/knowledge?scope=project&project_id={p}").json()
        assert [i["text"] for i in got] == ["project lesson"]
        assert {i["text"] for i in app.get(f"/api/knowledge?scope=room&room={dev_room}").json()} >= {"company lesson"}
        assert app.get("/api/knowledge?scope=nowhere").status_code == 400
