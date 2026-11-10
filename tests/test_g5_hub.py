"""G5 Control Hub overviews: Evolution Lab (UI-G5-EV), Governance (UI-G5-GV), Observability (UI-G5-OB) (hub.py)."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, hub, prefs, projects
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import (Agent, CatalogItem, DefinitionCandidate, Experiment, Goal, LearningPlan, Lesson,
                              LiveProcess, McpServer, Room, Run, now, session)

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "member", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": "own"}}


def client(user):
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: user
    return TestClient(app)


def dev_id():
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "developer")).first().id


def test_the_evolution_lab_answers_its_questions_from_records():
    aid = dev_id()
    t = now()
    with session() as s:
        s.add_all([
            LearningPlan(agent_id=aid, capability="python", final_suite="python-final", status="passed",
                         current_level=1, target_level=2),
            Experiment(target="playbook", agent_id=aid, baseline_version="v1", status="rolled_out",
                       results={"comparison": {"better": True, "pass_rate_change": 0.2, "regressions": []}}),
            Experiment(target="route", role="developer", status="rejected",
                       results={"comparison": {"better": False, "pass_rate_change": -0.1, "regressions": ["t1"]}}),
            Experiment(target="playbook", agent_id=aid, status="review"),
            DefinitionCandidate(agent_id=aid, base_version=2, status="applied", decided_at=t),
            DefinitionCandidate(agent_id=aid, base_version=3, status="pending"),
            CatalogItem(kind="skill", name="pandas", source="registry", enabled=False),
            Lesson(agent_id=aid, text="use uv", outcome="success", status="approved"),
            Lesson(agent_id=aid, text="draft", outcome="imported", status="draft"),
        ])
    lab = hub.evolution_lab("30d")
    me = next(a for a in lab["improving"] if a["id"] == aid)
    assert any("certified in python" in x for x in me["steps"]) and any("rolled out" in x for x in me["steps"])
    assert any("definition rewrite applied" in x for x in me["steps"])
    assert lab["capabilities"][0]["capability"] == "python"
    assert [x["name"] for x in lab["skills"]] == ["pandas"]
    assert lab["knowledge"] == {"approved": 1, "by_outcome": {"success": 1}, "drafts_waiting": 1}
    assert lab["experiments"]["passed"] == 1 and lab["experiments"]["failed"] == 1
    assert {x["kind"] for x in lab["awaiting"]} >= {"definition", "experiment"}
    assert {x["kind"] for x in lab["deployed"]} >= {"playbook", "definition"}
    assert [x["step"] for x in lab["lifecycle"]] == list(hub.STEPS)
    assert lab["better"]["overall"] == "insufficient", "no finished missions: never a guessed verdict"


def test_the_capability_table_reuses_improves_develops_or_acquires():
    aid = dev_id()
    p = projects.create("Caps")
    with session() as s:
        from agent_company.db import Project
        s.get(Project, p["id"]).required_skills = ["python", "rust", "go"]
        a = s.get(Agent, aid)
        a.skills = {**(a.skills or {}), "python": 10, "rust": 2}
        s.add(LearningPlan(agent_id=aid, capability="go", final_suite="x", status="running"))
    with session() as s:
        rows = {r["skill"]: r for r in hub.capability_table(s)}
    assert rows["python"]["action"] in ("reuse", "improve")
    assert rows["rust"]["status"] == "weak" and rows["rust"]["action"] == "improve"
    assert rows["go"]["status"] == "missing" and rows["go"]["action"] == "develop"
    assert rows["python"]["projects"] == ["Caps"]


def test_the_autonomy_matrix_follows_the_live_settings():
    def level(name):
        return next(r["level"] for r in hub.autonomy_matrix() if r["activity"] == name)

    assert level("Mission planning") == hub.AUTO
    assert level("New tool permissions") == hub.HUMAN
    prefs.set_value("require_plan_approval", True)
    prefs.set_value("auto_merge", True)
    assert level("Mission planning") == hub.HUMAN
    assert level("Merging a mission's result") == hub.POLICY
    assert all(r["level"] in (hub.AUTO, hub.POLICY, hub.HUMAN) for r in hub.autonomy_matrix())


def test_governance_lists_budgets_safety_and_autonomy_levels():
    aid = dev_id()
    with session() as s:
        s.scalars(select(Room)).first().daily_budget_usd = 5.0
        a = s.get(Agent, aid)
        a.daily_cost_quota_usd, a.autonomy = 1.0, "G4"
        g = Goal(title="Costly", description="", repo_path="", status="awaiting_approval", budget_usd=1.0,
                 estimated_cost_usd=0.5)
        s.add(g)
        s.flush()
        s.add(Run(agent_id=aid, goal_id=g.id, model_name="m", kind="llm", cost=0.9))
    d = hub.governance_overview(ADMIN)
    assert d["autonomy"]["G4"] >= 1 and len(d["safety"]) == 10
    assert d["budgets"]["rooms"] and d["budgets"]["agents"][0]["today_usd"] == 0.9
    assert [g["title"] for g in d["budgets"]["near_limit"]] == ["Costly"]
    goal = next(x for x in d["approvals"] if x["kind"] == "goal")
    assert goal["spent_usd"] == 0.9 and goal["budget_usd"] == 1.0 and goal["estimated_usd"] == 0.5
    assert client(MEMBER).get("/api/governance").json()["approvals"] == []


def test_observability_counts_tools_isolation_and_models_for_administrators():
    aid = dev_id()
    with session() as s:
        s.add_all([
            Run(agent_id=aid, model_name="sonnet", kind="claude_code", isolation="L2", input_tokens=10, output_tokens=5,
                tool_calls=[{"tool": "Edit", "ok": True}, {"tool": "Bash", "ok": False}, {"tool": "Edit", "ok": True}]),
            Run(agent_id=aid, model_name="sonnet", kind="claude_code", isolation="L2", ok=False),
            LiveProcess(pid=4242, program="claude", kind="claude_code", agent_id=aid, goal_id=None),
            McpServer(name="files", slug="files", status="ok", tools=[{"name": "read", "allowed": True}]),
        ])
    o = hub.observability("24h")
    assert o["tools"]["calls"] == 3 and o["tools"]["failed"] == 1 and o["tools"]["top"][0] == {
        "tool": "Edit", "calls": 2, "failed": 0}
    assert o["sandbox"]["isolation"] == {"L2": 2} and [p["pid"] for p in o["sandbox"]["processes"]] == [4242]
    assert o["models"][0]["name"] == "sonnet" and o["models"][0]["runs"] == 2 and o["models"][0]["failed"] == 1
    assert o["tools"]["mcp_servers"][0]["tools"] == 1 and "host" in o
    assert client(MEMBER).get("/api/observability").status_code == 403
    assert client(ADMIN).get("/api/observability?period=2y").status_code == 400
    assert client(ADMIN).get("/api/evolution-lab?period=7d").json()["period"] == "7d"
