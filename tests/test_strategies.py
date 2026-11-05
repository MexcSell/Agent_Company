"""SPEC-04 S04-R-11: workflow templates per goal kind, strategy selection and versions, team resizing."""

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import contracts, executors, loop, registry, strategies, teams
from agent_company.api import create_app
from agent_company.db import AuditEvent, Event, GoalMember, Task, session


@pytest.fixture
def admin():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        yield c


def planner_says(monkeypatch, *answers):
    """The offline planner answers these plans in turn (the last one repeats)."""
    seen = []

    def plan(self, req):
        seen.append(req)
        return json.dumps(answers[min(len(seen), len(answers)) - 1])

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    return seen


def tasks_of(gid):
    with session() as s:
        return s.scalars(select(Task).where(Task.goal_id == gid).order_by(Task.id)).all()


def test_a_research_goal_runs_without_developer_tasks_and_without_the_quality_commands():
    gid = loop.create_goal("Compare SQLite and PostgreSQL for the job queue", kind="research",
                           quality_commands=["false"])
    g = loop._goal(gid)
    assert g.kind == "research" and g.strategy["source"] == "order" and g.strategy["version"] == 1
    assert g.max_iterations == 2  # the template's limit, as the order named none
    out = loop.run_goal(gid)
    assert out["status"] in {"approved", "awaiting_approval"}, out
    tasks = tasks_of(gid)
    roles = {t.role for t in tasks}
    assert "developer" not in roles and not any(t.writes for t in tasks)
    assert {"research", "qa"} <= roles
    assert not [c for c in out["report"]["checks"] if c["check"].startswith("command")]
    assert loop._goal(gid).baseline == {}  # the quality commands never ran


def test_a_plan_that_does_not_fit_the_template_goes_back_once_then_the_template_plan_is_used(monkeypatch):
    bad = executors.standard_plan("x")  # architect -> developer (writes) -> qa
    seen = planner_says(monkeypatch, bad)
    gid = loop.create_goal("Survey the logging libraries", kind="research")
    out = loop.run_goal(gid)
    assert out["status"] in {"approved", "awaiting_approval"}
    assert len(seen) == 2 and "does not fit the research workflow template" in seen[1].prompt
    assert "No task may change files" in seen[0].prompt and seen[0].data["kind"] == "research"
    assert "developer" not in {t.role for t in tasks_of(gid)}


def test_the_planner_picks_a_template_and_it_can_only_tighten_the_goal(monkeypatch):
    plan = {"kind": "docs", "summary": "docs", "tasks": strategies.fallback_plan("x", "docs")["tasks"]}
    seen = planner_says(monkeypatch, plan)
    gid = loop.create_goal("Explain the configuration", quality_commands=["false"])
    assert loop._goal(gid).kind == "" and loop._goal(gid).max_iterations == 3
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert g.kind == "docs" and g.strategy["source"] == "planner"
    assert g.max_iterations == 2 and g.protect_tests and "docs/*" in g.allowed_paths
    assert out["status"] == "failed" and out["iterations"] == 2  # the lowered limit ended the loop
    assert "Workflow templates (name the one" in seen[0].prompt
    assert "Workflow template: docs v1" in seen[1].prompt  # the second iteration plans under the chosen one


def test_a_plan_without_a_kind_gets_an_inferred_label_and_nothing_else_changes():
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    g = loop._goal(gid)
    assert g.kind == "feature" and g.strategy["source"] == "inferred"
    assert not strategies.enforced(g) and g.max_iterations == 3 and not g.protect_tests
    with session() as s:
        assert s.scalars(select(Event).where(Event.goal_id == gid, Event.message.like("workflow template feature%"))).first()


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="unknown goal kind"):
        loop.create_goal("x y z", kind="party")


def test_an_independent_review_is_added_when_the_plan_has_none():
    plan = contracts.validate_plan([t for t in executors.standard_plan("x")["tasks"] if t["role"] != "qa"])
    out = strategies.with_review(strategies.effective("feature"), plan)
    assert out[-1].role == "qa" and out[-1].depends_on == ["build"]
    assert strategies.with_review({**strategies.effective("feature"), "review": "none"}, plan) == plan


def test_the_refactor_template_runs_one_task_at_a_time_and_protects_tests():
    gid = loop.create_goal("Split the module", kind="refactor")
    g = loop._goal(gid)
    assert g.protect_tests and strategies.max_agents(g, 4) == 1
    other = loop._goal(loop.create_goal("Add a flag", kind="feature"))
    assert strategies.max_agents(other, 4) == 4


def test_templates_are_versioned_and_goals_record_the_version_they_ran_under():
    v1 = loop.create_goal("first", kind="docs")
    body = {k: v for k, v in strategies.effective("docs").items() if k not in {"kind", "version"}}
    out = strategies.save("docs", {**body, "max_iterations": 4}, "user:luca", "longer")
    assert out["version"] == 2
    v2 = loop.create_goal("second", kind="docs")
    assert loop._goal(v1).strategy["version"] == 1 and loop._goal(v2).strategy["version"] == 2
    assert loop._goal(v2).max_iterations == 4
    back = strategies.restore("docs", 1, "user:luca")
    assert back["version"] == 3 and back["max_iterations"] == 2
    docs = next(x for x in strategies.list_strategies() if x["kind"] == "docs")
    assert docs["version"] == 3 and [v["goals"] for v in docs["versions"]] == [0, 1, 1]
    with pytest.raises(ValueError, match="missing"):
        strategies.save("docs", {"roles": ["qa"]}, "user:luca")
    with pytest.raises(ValueError, match="keeps the checks"):
        strategies.save("docs", {**body, "checks": "none"}, "user:luca")
    with pytest.raises(ValueError, match="names its roles"):
        strategies.save("research", {**body, "writes": False, "roles": ["*"]}, "user:luca")
    trace = loop.trace(v2)
    assert trace["goal"]["kind"] == "docs" and trace["goal"]["strategy"]["version"] == 2


# --- team resizing (FR-25) ----------------------------------------------------------------------

def test_the_team_history_records_joins_releases_and_additions():
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    joined = {m["role"] for m in teams.history(gid) if m["action"] == "joined"}
    assert {"architect", "developer", "qa"} <= joined
    dev = next(m["agent_id"] for m in teams.history(gid) if m["role"] == "developer")
    teams.release(gid, dev, "too slow", "user:luca")
    with pytest.raises(RuntimeError, match="released from goal"):
        registry.staff("developer", ["python"], gid)
    with pytest.raises(ValueError, match="already released"):
        teams.release(gid, dev)
    gm = registry.staff("general_manager", ["planning"], gid)
    registry.release(gm.id, gid)
    with pytest.raises(ValueError, match="cannot be released"):
        teams.release(gid, gm.id)
    teams.add(gid, agent_id=dev, reason="back", actor="user:luca")
    a = registry.staff("developer", ["python"], gid)
    registry.release(a.id, gid)
    added = teams.add(gid, "qa", ["review"], "second reviewer", "user:luca")
    assert added["action"] == "added" and added["agent_id"]
    actions = [m["action"] for m in teams.history(gid)]
    assert actions.count("released") == 1 and actions.count("added") == 2
    assert loop.trace(gid)["team"][-1]["reason"] == "second reviewer"
    assert any(m["action"] == "released" for m in teams.history(gid))  # history is never rewritten


def test_the_planner_resizes_the_team_in_its_plan(monkeypatch):
    plan = executors.standard_plan("x")
    plan["team"] = {"add": [{"role": "security", "skills": ["security_review"], "reason": "auth code"}],
                    "release": [{"agent_id": 99999, "reason": "nobody"}]}
    seen = planner_says(monkeypatch, plan)
    gid = loop.create_goal("Add a login form", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    assert "To resize the team" in seen[0].prompt
    with session() as s:
        rows = s.scalars(select(GoalMember).where(GoalMember.goal_id == gid, GoalMember.action == "added")).all()
        assert rows and rows[0].reason == "auth code"
        assert s.scalars(select(Event).where(Event.goal_id == gid, Event.message.like("release refused%"))).first()


# --- API --------------------------------------------------------------------------------------------

def test_api_lists_and_versions_templates_and_resizes_a_team(admin):
    assert {x["kind"] for x in admin.get("/api/strategies").json()} == set(strategies.KINDS)
    body = {k: v for k, v in strategies.effective("bugfix").items() if k not in {"kind", "version"}}
    r = admin.post("/api/strategies/bugfix", json={"body": {**body, "max_iterations": 5}, "note": "harder bugs"})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert admin.post("/api/strategies/bugfix", json={"body": {"roles": []}}).status_code == 400
    assert admin.post("/api/strategies/bugfix/restore/1").json()["version"] == 3
    assert admin.post("/api/goals", json={"title": "Do things", "kind": "party"}).status_code == 400
    gid = loop.create_goal("Fix the crash", kind="bugfix")
    r = admin.post(f"/api/goals/{gid}/team", json={"action": "add", "role": "qa", "reason": "review"})
    assert r.status_code == 200 and r.json()["action"] == "added"
    assert admin.post(f"/api/goals/{gid}/team", json={"action": "release"}).status_code == 400
    r = admin.post(f"/api/goals/{gid}/team", json={"action": "release", "agent_id": r.json()["agent_id"]})
    assert r.status_code == 200
    g = admin.get(f"/api/goals/{gid}").json()
    assert g["kind"] == "bugfix" and [m["action"] for m in g["team"]] == ["added", "released"]
    with session() as s:
        acts = set(s.scalars(select(AuditEvent.action)))
    assert {"strategy_save", "strategy_restore", "goal_team_add", "goal_team_release"} <= acts
