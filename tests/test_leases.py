"""File leases between concurrent goals (guides G-R-08)."""
import heapq
import secrets

import pytest

from agent_company import auth, loop, projects
from agent_company.checks import overlapping
from agent_company.db import Event, Goal, session
from agent_company.workflow import set_status


@pytest.fixture(autouse=True)
def users(monkeypatch):
    auth.register("admin", secrets.token_urlsafe(32))
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)  # nothing runs: the tests look at the queue


def _goal(title, project_id, paths, topic="general"):
    return loop.create_goal(title, project_id=project_id, allowed_paths=paths, topic=topic)


def test_overlap_follows_the_scope_checks_rule():
    assert overlapping(["src/*"], ["src/app/main.py"]) == [("src/*", "src/app/main.py")]
    assert overlapping(["src/app"], ["src/app/*.py"]) and overlapping(["docs/*.md"], ["docs/*.md"])
    assert overlapping(["src/*"], ["tests/*"]) == [] and overlapping(["src/a.py"], ["src/b.py"]) == []


def test_an_earlier_goal_with_overlapping_paths_holds_the_later_one():
    p = projects.create("Leases")
    first = _goal("Change the API", p["id"], ["src/api/*"], topic="api")
    second = _goal("Refactor main", p["id"], ["src/api/main.py", "README.md"], topic="cli")
    apart = _goal("Docs", p["id"], ["docs/*"], topic="docs")
    anything = _goal("Anything", p["id"], [], topic="misc")
    assert loop.paths_ahead(first) is None and loop.paths_ahead(apart) is None
    assert loop.paths_ahead(second) == (first, [("src/api/main.py", "src/api/*")])
    assert loop.paths_ahead(anything) is None, "a goal without declared paths neither waits nor holds"
    later = _goal("After anything", p["id"], ["docs/x.md"], topic="later")
    assert loop.paths_ahead(later) == (apart, [("docs/x.md", "docs/*")])
    other = projects.create("Elsewhere")
    assert loop.paths_ahead(_goal("Same paths, other repo", other["id"], ["src/api/*"])) is None
    with session() as s:
        set_status(s, s.get(Goal, first), "running", "test")
    assert loop.paths_ahead(second)[0] == first, "a running goal holds its lease"
    with session() as s:
        set_status(s, s.get(Goal, first), "awaiting_approval", "test")
    assert loop.paths_ahead(second) is None, "a goal waiting for a person holds no lease"


def test_the_queue_holds_a_goal_behind_a_lease_and_says_why_once(monkeypatch):
    p = projects.create("Queue leases")
    first = _goal("First", p["id"], ["src/*"], topic="one")
    second = _goal("Second", p["id"], ["src/x.py"], topic="two")
    ran = []
    monkeypatch.setattr(loop, "run_goal", lambda gid: ran.append(gid))
    q = loop.GoalQueue(workers=1)

    class Done(Exception):
        pass

    def get():
        if q.q.empty():
            raise Done
        return heapq.heappop(q.q.queue)

    monkeypatch.setattr(q.q, "get", get)
    for _ in range(2):  # the watcher offers it again: one note only
        q.submit(second)
        with pytest.raises(Done):
            q._loop()
    assert ran == [] and second not in q.seen
    with session() as s:
        notes = s.query(Event).filter(Event.goal_id == second, Event.message.like("waits for goal%")).all()
        assert len(notes) == 1 and notes[0].data["lease_holder"] == first
    assert any(f"waits for goal #{first}" in m["content"] for m in projects.messages(p["id"]))
    with session() as s:
        set_status(s, s.get(Goal, first), "cancelled", "test")
    q.submit(second)
    with pytest.raises(Done):
        q._loop()
    assert ran == [second]
