"""UI-GM-04 (AT-003, FR-003.4): the GM's clarification questions; the goal waits for the person's answers and is
planned again with them; one round per goal."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, approvals, executors, loop
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import AuditEvent, Goal, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


@pytest.fixture
def asking(monkeypatch):
    prompts = []

    def plan(self, req):
        prompts.append(req.prompt)
        data = executors.standard_plan(req.data.get("goal", "g"))
        data["questions"] = ["Which Python versions must it support?", "  ", "Is a CLI wanted?"]
        return json.dumps(data)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    return prompts


def test_questions_pause_the_goal_and_the_answers_are_planned_with(asking):
    gid = loop.create_goal("Build a parser", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "paused" and out["questions"] == ["Which Python versions must it support?",
                                                               "Is a CLI wanted?"]
    with session() as s:
        cp = s.get(Goal, gid).checkpoint
    assert cp["paused_for"] == "clarification" and len(cp["questions"]) == 2
    assert any(i["kind"] == "questions" and i["id"] == gid for i in approvals.queue(ADMIN))
    with pytest.raises(ValueError, match="at least one"):
        loop.answer_questions(gid, "user:admin", ["", " "])
    loop.answer_questions(gid, "user:admin", ["3.12 and newer"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval", "asked once: the second plan runs"
    assert "A1: 3.12 and newer" in asking[-1] and "A2: (no answer)" in asking[-1]
    with session() as s:
        cp = s.get(Goal, gid).checkpoint
    assert cp["clarified"] is True and "3.12" in cp["answers"]


def test_benchmark_goals_never_wait_for_answers(asking):
    gid = loop.create_goal("Benchmark", origin="eval", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "approved"


def test_the_endpoint_answers_and_audits(asking):
    gid = loop.create_goal("Ask me", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    c = TestClient(app)
    assert c.get(f"/api/goals/{gid}").json()["questions"][1] == "Is a CLI wanted?"
    assert c.post(f"/api/goals/{gid}/questions/answer", json={"answers": "no"}).status_code == 400
    assert c.post(f"/api/goals/{gid}/questions/answer", json={"answers": ["3.12", "no"]}).status_code == 200
    assert c.post(f"/api/goals/{gid}/questions/answer", json={"answers": ["again"]}).status_code == 409
    with session() as s:
        assert s.get(Goal, gid).status == "queued"
        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "goal_answer")).first() is not None
