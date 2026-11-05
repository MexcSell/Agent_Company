"""Topics: conversation lanes of a project's command window (ASEAO05 S05-R-07) and goals of one topic
running in order (G-R-07)."""

import secrets
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from agent_company import decisions, executors, loop, projects
from agent_company.api import create_app
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, Goal, Message, session
from agent_company.workflow import set_status

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def users():
    from agent_company import auth

    auth.register("admin", secrets.token_urlsafe(32))


@pytest.fixture
def no_run(monkeypatch):
    """Orders become goals but nothing runs them: the tests look at the lanes, not at the loop."""
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def wait(gid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        g = loop._goal(gid)
        if g.status not in {"queued", "planning", "running", "recovering"}:
            return g
        time.sleep(0.05)
    raise AssertionError("goal did not finish")


def test_messages_default_to_general_and_topics_filter_and_search(no_run):
    p = projects.create("Lanes")
    projects.post(p["id"], "an old-style message")
    assert projects.messages(p["id"])[-1]["topic"] == "general"
    t = projects.create_topic(p["id"], "  Front End ", "the UI")
    assert t["name"] == "front end" and t["messages"] == 1  # the "opened" note
    projects.command(p["id"], ADMIN, "/help", topic="front end")
    lane = projects.messages(p["id"], topic="front end")
    assert [m["kind"] for m in lane] == ["chat", "command", "chat"] and "/summary" in lane[-1]["content"]
    assert all(m["topic"] == "general" for m in projects.messages(p["id"], topic="general"))
    assert [m["content"] for m in projects.messages(p["id"], q="OLD-STYLE")] == ["an old-style message"]
    assert projects.messages(p["id"], q="100%") == []  # LIKE wildcards are matched literally
    names = [x["name"] for x in projects.topics(p["id"])]
    assert names == ["general", "front end"]
    with pytest.raises(ValueError):
        projects.create_topic(p["id"], "front end")
    with pytest.raises(ValueError):
        projects.create_topic(p["id"], "general")
    with pytest.raises(ValueError):
        projects.create_topic(p["id"], "bad/name")
    with pytest.raises(ValueError):
        projects.command(p["id"], ADMIN, "/help", topic="nowhere")
    projects.update_topic(p["id"], "front end", {"archived": True})
    with pytest.raises(ValueError):
        projects.command(p["id"], ADMIN, "/help", topic="front end")
    with pytest.raises(ValueError):
        projects.update_topic(p["id"], "general", {"archived": True})


def test_old_databases_get_general_for_existing_messages(no_run):
    """A database made before topics: `_add_missing_columns` adds the column with its default."""
    p = projects.create("Migrated")
    projects.post(p["id"], "before topics")
    from agent_company import db

    with db.engine().begin() as conn:
        conn.execute(text("DROP INDEX ix_messages_topic"))
        conn.execute(text("ALTER TABLE messages DROP COLUMN topic"))
    db.reset_engine()
    assert projects.messages(p["id"], topic="general")[-1]["content"] == "before topics"


def test_an_order_in_a_topic_reports_in_that_topic():
    p = projects.create("Reports", quality_commands=["test -f WORKLOG.md"])
    projects.create_topic(p["id"], "backend")
    out = projects.command(p["id"], ADMIN, "Write the worklog", topic="backend")
    g = wait(out["goal_id"])
    assert g.topic == "backend"
    lane = projects.messages(p["id"], topic="backend")
    assert any(m["kind"] == "plan" for m in lane) and any(m["kind"] == "result" for m in lane)
    assert all(m["goal_id"] is None for m in projects.messages(p["id"], topic="general"))


def test_goals_of_one_topic_run_in_order(no_run):
    p = projects.create("Order")
    projects.create_topic(p["id"], "a")
    first = projects.command(p["id"], ADMIN, "First job", topic="a")["goal_id"]
    second = projects.command(p["id"], ADMIN, "Second job", topic="a")["goal_id"]
    other = projects.command(p["id"], ADMIN, "Elsewhere", topic="general")["goal_id"]
    assert loop.lane_ahead(first) is None and loop.lane_ahead(other) is None
    assert loop.lane_ahead(second) == first
    assert "starts when goal" in projects.messages(p["id"], topic="a")[-1]["content"]
    with session() as s:
        set_status(s, s.get(Goal, first), "running", "test")
    assert loop.lane_ahead(second) == first
    with session() as s:
        set_status(s, s.get(Goal, first), "awaiting_approval", "test")
    assert loop.lane_ahead(second) is None, "a goal waiting for approval does not hold its topic"
    assert loop.lane_ahead(loop.create_goal("no project")) is None


def test_queue_holds_a_goal_behind_its_topic(no_run, monkeypatch):
    p = projects.create("Queue")
    first = projects.command(p["id"], ADMIN, "First")["goal_id"]
    second = projects.command(p["id"], ADMIN, "Second")["goal_id"]
    ran = []
    monkeypatch.setattr(loop, "run_goal", lambda gid: ran.append(gid))
    q = loop.GoalQueue(workers=1)
    q.seen = {second}
    q.q.put(second)

    class Done(Exception):
        pass

    def get():
        if q.q.empty():
            raise Done
        return q.q.queue.popleft()

    monkeypatch.setattr(q.q, "get", get)
    with pytest.raises(Done):
        q._loop()
    assert ran == [] and second not in q.seen, "held, and offered again by the watcher"
    q.q.put(first)
    with pytest.raises(Done):
        q._loop()
    assert ran == [first]


def test_decision_lines_become_decision_records_not_goals(no_run):
    p = projects.create("Decide")
    projects.create_topic(p["id"], "storage")
    out = projects.command(p["id"], ADMIN, "DECISION: database :: SQLite with one writer :: no server to run",
                           topic="storage")
    assert "goal_id" not in out and len(out["decisions"]) == 1
    d = decisions.list_decisions(project_id=p["id"])[0]
    assert (d["topic"], d["recommendation"], d["reasoning"]) == ("database", "SQLite with one writer",
                                                                   "no server to run")
    assert d["project_id"] == p["id"] and "topic 'storage'" in d["evidence"][0]
    again = projects.command(p["id"], ADMIN, "DECISION: Database :: sqlite with one writer", topic="storage")
    assert again["decisions"] == [] and len(decisions.list_decisions(project_id=p["id"])) == 1
    mixed = projects.command(p["id"], ADMIN, "Add the migration\nDECISION: ids :: integers", topic="storage")
    assert mixed["goal_id"] and len(decisions.list_decisions(project_id=p["id"])) == 2


def test_summary_offline_posts_the_draft_and_keeps_it_on_the_topic(no_run):
    p = projects.create("Summed")
    projects.create_topic(p["id"], "api")
    projects.command(p["id"], ADMIN, "Build the endpoint", topic="api")
    projects.command(p["id"], ADMIN, "DECISION: auth :: device sign-in", topic="api")
    projects.command(p["id"], ADMIN, "/summary", topic="api")
    reply = projects.messages(p["id"], topic="api")[-1]
    assert reply["kind"] == "result" and reply["content"].startswith("Summary of topic 'api'")
    assert "Build the endpoint" in reply["content"] and "auth: device sign-in" in reply["content"]
    t = next(x for x in projects.topics(p["id"]) if x["name"] == "api")
    assert t["summary"] == reply["content"] and t["summary_at"]
    projects.command(p["id"], ADMIN, "/summary")  # general has no row until it needs one
    assert projects.topics(p["id"])[0]["summary"].startswith("Summary of topic 'general'")


def test_summary_records_decisions_a_model_finds(no_run, monkeypatch):
    p = projects.create("Found")
    projects.command(p["id"], ADMIN, "We agreed to ship on Fridays only")
    answer = ("They agreed on a release day.\n```json\n{\"decisions\": [{\"topic\": \"release day\", "
              "\"recommendation\": \"Fridays\", \"confidence\": 0.8, \"category\": \"operational\"}, "
              "{\"topic\": \"\"}]}\n```")

    class Fake:
        def run(self, req):
            assert req.purpose == "summary" and "ship on Fridays" in req.prompt
            return executors.Result(text=answer)

    monkeypatch.setattr(executors, "for_choice", lambda choice, timeout=0: Fake())
    out = projects.summarize(p["id"], "general")
    assert "```" not in out and "Recorded as decisions: #" in out
    d = decisions.list_decisions(project_id=p["id"])
    assert [(x["topic"], x["category"], x["status"]) for x in d] == [("release day", "operational", "approved")]
    assert "summary of topic 'general'" in d[0]["evidence"]
    projects.summarize(p["id"], "general")
    assert len(decisions.list_decisions(project_id=p["id"])) == 1, "a decision is recorded once"


def test_standup_in_a_topic_reports_its_agents(no_run):
    p = projects.create("Stand")
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.role == "developer")).first()
        dev_id, dev_name = dev.id, dev.name
    projects.create_topic(p["id"], "ui", agent_ids=[dev_id])
    projects.command(p["id"], ADMIN, "/standup", topic="ui")
    report = projects.messages(p["id"], topic="ui")[-1]["content"]
    assert report.startswith("Stand-up (ui)") and dev_name in report
    projects.command(p["id"], ADMIN, "/standup")
    assert dev_name not in projects.messages(p["id"], topic="general")[-1]["content"], "nobody worked here yet"
    with pytest.raises(ValueError):
        projects.update_topic(p["id"], "ui", {"agent_ids": [99999]})


def test_topic_api_and_permissions():
    from agent_company import auth
    from agent_company.db import User

    auth.register("member", secrets.token_urlsafe(32))
    auth.admin_action(1, 2, "approve")
    p = projects.create("Api lanes")
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        admin_secret, member_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(admin_secret)
            s.get(User, 2).device_hash = auth._hash(member_secret)
        member = TestClient(app.app)
        assert member.post("/api/auth/device", json={"device_secret": member_secret}).status_code == 200
        assert app.post("/api/auth/device", json={"device_secret": admin_secret}).status_code == 200
        app.put("/api/users/2/permissions", json={"projects": "all", "run": True})
        pid = p["id"]
        assert member.post(f"/api/projects/{pid}/topics", json={"name": "docs"}).status_code == 200
        assert member.post(f"/api/projects/{pid}/topics", json={"name": "x", "agent_ids": [1]}).status_code == 403
        assert member.patch(f"/api/projects/{pid}/topics/docs", json={"archived": True}).status_code == 403
        assert app.patch(f"/api/projects/{pid}/topics/docs", json={"description": "manuals"}).json()["description"] \
            == "manuals"
        assert member.post(f"/api/projects/{pid}/command", json={"content": "/help", "topic": "docs"}).status_code == 200
        lane = member.get(f"/api/projects/{pid}/messages?topic=docs").json()
        assert lane[-1]["topic"] == "docs" and "/summary" in lane[-1]["content"]
        assert [t["name"] for t in member.get(f"/api/projects/{pid}/topics").json()] == ["general", "docs"]
        assert member.get(f"/api/projects/{pid}/messages?q=manuals").json() == []
        actions = {r["action"] for r in app.get("/api/audit").json()["records"]}
        assert {"topic_create", "topic_update"} <= actions
        app.put("/api/users/2/permissions", json={"run": False})
        assert member.post(f"/api/projects/{pid}/topics", json={"name": "nope"}).status_code == 403
    assert projects.delete([pid]) == [pid]
    with session() as s:
        assert s.query(Message).filter(Message.project_id == pid).count() == 0
