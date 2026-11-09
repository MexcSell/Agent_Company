"""V22: a topic's assigned agents are staffed first on its goals; busy ones are not waited for."""

import pytest

from agent_company import loop, projects, registry
from agent_company.db import Agent, Goal, session


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def lane_goal(pid, topic):
    gid = loop.create_goal("Lane work", project_id=pid)
    with session() as s:
        s.get(Goal, gid).topic = topic
    return gid


def test_a_topic_agent_is_staffed_before_a_better_ranked_one():
    p = projects.create("Lanes")
    extra = registry.create_agent("developer", "Topic Developer")["id"]
    projects.create_topic(p["id"], "frontend", agent_ids=[extra])
    a = registry.staff("developer", ["python"], lane_goal(p["id"], "frontend"))
    assert a.id == extra
    registry.release(a.id)


def test_the_general_topic_and_busy_topic_agents_fall_back_to_ranking():
    p = projects.create("Lanes")
    extra = registry.create_agent("developer", "Topic Developer")["id"]
    projects.create_topic(p["id"], "frontend", agent_ids=[extra])
    other = loop.create_goal("Elsewhere", project_id=p["id"])
    with session() as s:
        s.get(Agent, extra).active_goals = [other]  # its one slot is taken
    a = registry.staff("developer", ["python"], lane_goal(p["id"], "frontend"))
    assert a.id != extra and a.parent_id != extra, "a busy topic agent is not cloned or waited for"
    registry.release(a.id)
    gid = loop.create_goal("General work", project_id=p["id"])
    with session() as s:
        assert registry._topic_agents(s, s.get(Goal, gid)) == set()
