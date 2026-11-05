"""Blackboard (S04-R-01), agent messages (S04-R-10), decision records (S04-R-02) and escalation (S04-R-07)."""

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import blackboard, decisions, executors, loop
from agent_company.api import create_app
from agent_company.db import Event, session


def test_writes_based_on_an_old_version_are_refused_and_versions_are_kept():
    gid = loop.create_goal("Board")
    assert blackboard.write(gid, "engineering", "db", "use SQLite", 0) == 1
    with pytest.raises(blackboard.Conflict):
        blackboard.write(gid, "engineering", "db", "use Postgres", 0)  # based on the same version
    assert blackboard.write(gid, "engineering", "db", "use SQLite with WAL", 1) == 2
    assert [v["content"] for v in blackboard.versions(gid, "engineering", "db")] == ["use SQLite", "use SQLite with WAL"]
    assert blackboard.read(gid)[0]["version"] == 2
    with pytest.raises(ValueError):
        blackboard.write(gid, "gossip", "x", "y", 0)


def capture(monkeypatch, extra=None):
    prompts = []
    real = executors.OfflineExecutor._task

    def task(self, req):
        prompts.append((req.role, req.prompt))
        out = real(self, req)
        return out + (extra(req) if extra else "")

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    return prompts


def test_a_task_sees_its_own_goals_sections_and_never_another_goals(monkeypatch):
    other = loop.create_goal("Other goal")
    blackboard.put(other, "engineering", "secret-plan", "SECRET-OF-THE-OTHER-GOAL")
    gid = loop.create_goal("This goal", quality_commands=["test -f WORKLOG.md"])
    blackboard.put(gid, "engineering", "note", "USE-SQLITE-FOR-STORAGE")
    prompts = capture(monkeypatch)
    loop.run_goal(gid)
    dev = next(p for role, p in prompts if role == "developer")
    assert "USE-SQLITE-FOR-STORAGE" in dev and "<entry section=\"objective\"" in dev
    assert all("SECRET-OF-THE-OTHER-GOAL" not in p for _, p in prompts)
    qa = next(p for role, p in prompts if role == "qa")
    assert "section=\"planning\"" not in qa, "QA gets objective, engineering and qa only"
    sections = {e["section"] for e in blackboard.read(gid)}
    assert {"objective", "planning", "engineering", "qa"} <= sections


def test_a_request_from_the_developer_reaches_the_architects_next_prompt(monkeypatch):
    msg = "MESSAGE request to architect: document the storage format in SPEC.md"
    prompts = capture(monkeypatch, lambda req: f"\n{msg}\n" if req.role == "developer" else "")
    gid = loop.create_goal("Talk", quality_commands=["false"], max_iterations=2)
    loop.run_goal(gid)
    arch = [p for role, p in prompts if role == "architect"]
    assert "document the storage format" not in arch[0] and "request from developer" in arch[1]
    entry = next(e for e in blackboard.read(gid, ["messages"]))
    assert entry["task_id"] and entry["meta"]["to"] == "architect" and entry["meta"]["delivered"]
    assert len([p for p in arch if "request from developer" in p]) == 1, "delivered once"


def test_message_budget_and_duplicates():
    gid = loop.create_goal("Chatty")
    out = "\n".join(f"MESSAGE fact to qa: fact number {i}" for i in range(5)) + "\nMESSAGE fact to qa: fact number 0"
    assert blackboard.extract_messages(gid, 1, "build", 2, "developer", out) == 3
    assert blackboard.extract_messages(gid, 2, "build2", 2, "developer", "MESSAGE fact to qa: fact number 1") == 0


def plan_with(decisions_json):
    def plan(self, req):
        data = executors.standard_plan(req.data.get("goal", "g"))
        data["decisions"] = decisions_json
        return json.dumps(data)
    return plan


def test_a_decision_that_needs_a_person_blocks_the_merge(monkeypatch):
    from agent_company import config

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan_with([
        {"topic": "Database", "alternatives": ["SQLite", "PostgreSQL"], "recommendation": "SQLite",
         "reasoning": "one host", "confidence": 0.7, "category": "technical", "requires_human_approval": True},
        {"topic": "Naming", "recommendation": "snake_case", "category": "operational"},
        {"topic": "", "recommendation": "malformed: skipped"}]))
    config.settings().require_approval = False
    gid = loop.create_goal("Decide", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval", "an open decision keeps it from merging on its own"
    ds = decisions.list_decisions(goal_id=gid)
    assert [d["status"] for d in sorted(ds, key=lambda d: d["id"])] == ["escalated", "approved"]
    blocking = decisions.blocking(gid)
    with pytest.raises(ValueError, match="Database"):
        loop.approve(gid)
    decisions.decide(blocking[0]["id"], True, "user:admin")
    assert loop.approve(gid)["status"] == "approved"
    assert {d["topic"] for d in loop.trace(gid)["decisions"]} == {"Database", "Naming"}


def test_a_critical_decision_escalates_straight_to_a_person():
    gid = loop.create_goal("Critical")
    d = decisions.record(gid, "Delete customer data", "do not", category="critical")
    assert d["requires_human_approval"] and d["escalation_level"] == 5 and d["status"] == "escalated"
    with session() as s:
        ev = s.scalars(select(Event).where(Event.kind == "escalation")).first()
        assert ev.data["level"] == 5
    with pytest.raises(ValueError):
        decisions.decide(decisions.decide(d["id"], False, "admin")["id"], True, "admin")


def test_decisions_api_is_audited():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        gid = loop.create_goal("Api")
        d = decisions.record(gid, "Framework", "FastAPI", requires_human_approval=True)
        assert app.get(f"/api/decisions?goal_id={gid}").json()[0]["id"] == d["id"]
        r = app.post(f"/api/decisions/{d['id']}/decide", json={"approve": True, "note": "fine"}).json()
        assert r["status"] == "approved" and r["approver"] == "user:admin"
        assert app.post(f"/api/decisions/{d['id']}/decide", json={"approve": False}).status_code == 400
        g = app.get(f"/api/goals/{gid}").json()
        assert g["decisions"][0]["status"] == "approved" and "blackboard" in g
        assert "decision_decide" in {r["action"] for r in app.get("/api/audit").json()["records"]}
