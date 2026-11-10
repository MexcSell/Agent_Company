"""G5 Control Hub UI-G5-SW: Swarm Control's topology Mission -> Team -> Agent -> Task from runtime records."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, projects, swarm
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, Event, Goal, LiveProcess, Run, Task, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "member", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": "own"}}


def client(user):
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: user
    return TestClient(app)


def seed():
    p = projects.create("Swarm")
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.role == "developer")).first()
        qa = s.scalars(select(Agent).where(Agent.role == "qa")).first()
        g = Goal(title="Ship", description="", repo_path="", project_id=p["id"], status="running", iteration=2,
                 budget_usd=1.0, strategy={"execution": "parallel"})
        done = Goal(title="Old", description="", repo_path="", status="approved")
        s.add_all([g, done])
        s.flush()
        s.add_all([
            Task(goal_id=g.id, iteration=1, key="build", title="Build", role="developer", agent_id=dev.id, status="failed"),
            Task(goal_id=g.id, iteration=2, key="build", title="Build", role="developer", agent_id=dev.id, status="done",
                 critic=[{"verdict": "FAIL"}, {"verdict": "PASS"}], writes=True),
            Task(goal_id=g.id, iteration=2, key="review", title="Review", role="qa", agent_id=qa.id, status="running",
                 depends_on=["build"]),
            Task(goal_id=g.id, iteration=2, key="docs", title="Docs", role="developer", status="queued"),
            Run(agent_id=dev.id, goal_id=g.id, model_name="sonnet", kind="claude_code", cost=0.25, isolation="L2",
                input_tokens=100, output_tokens=50,
                tool_calls=[{"tool": "Edit", "input": "src/a.py", "ok": True}, {"tool": "Bash", "input": "x", "ok": False}]),
            Run(agent_id=qa.id, goal_id=g.id, model_name="codex", kind="codex", cost=0.05, isolation="L1", ok=False,
                error="boom"),
            LiveProcess(pid=999999, program="claude", kind="claude_code", agent_id=qa.id, goal_id=g.id),
            Event(goal_id=g.id, kind="security", message="refused rm -rf"),
            Event(goal_id=g.id, kind="task", message="not a policy event"),
        ])
        return p["id"], g.id, dev.id, qa.id, done.id


def test_the_topology_of_a_live_mission():
    pid, gid, dev, qa, _ = seed()
    t = swarm.topology(ADMIN)
    assert [m["id"] for m in t["missions"]] == [gid], "finished missions only on request"
    m = t["missions"][0]
    assert [x["key"] for x in m["tasks"]] == ["build", "review", "docs"], "the current iteration only"
    build = m["tasks"][0]
    assert build["retries"] == 1 and build["critic"] == ["FAIL", "PASS"] and m["recovered"] == 1
    assert m["edges"] == [["build", "review"]]
    by = {a["id"]: a for a in m["agents"]}
    assert set(by) == {dev, qa}, "a task nobody is staffed on adds no agent"
    assert by[dev]["tool_calls"] == 2 and by[dev]["tool_failures"] == 1 and by[dev]["isolation"] == "L2"
    assert by[dev]["recent_tools"][-1] == {"tool": "Bash", "ok": False, "input": "x"}
    assert by[qa]["running"] is True and by[qa]["last_error"] == "boom" and by[dev]["running"] is False
    assert sum(len(tm["agents"]) for tm in m["teams"]) == 2
    assert m["spent_usd"] == 0.3 and m["tokens"] == 150 and m["budget_usd"] == 1.0
    assert [e["kind"] for e in m["events"]] == ["security"]
    assert m["pattern"]["key"] in ("parallel", "collaborative")
    assert t["summary"] == {"missions": 1, "agents": 2, "running_now": 1, "idle_agents": t["summary"]["idle_agents"],
                            "tool_calls": 2, "tool_failures": 1, "approvals": 0, "failed_tasks": 0}


def test_patterns_follow_the_team_and_the_template():
    assert swarm.pattern("parallel", 0, 0)["key"] == "forming"
    assert swarm.pattern("parallel", 1, 1)["key"] == "single"
    assert swarm.pattern("sequential", 3, 2)["key"] == "hierarchical"
    assert swarm.pattern("parallel", 3, 2)["key"] == "parallel"
    assert swarm.pattern("parallel", 3, 1)["key"] == "collaborative"


def test_approvals_attach_to_their_mission_and_recent_adds_finished_ones():
    pid, gid, *_, done = seed()
    with session() as s:
        s.get(Goal, gid).status = "awaiting_approval"
    t = swarm.topology(ADMIN, recent=5)
    ids = [m["id"] for m in t["missions"]]
    assert ids[0] == gid and done in ids
    assert [a["kind"] for a in t["missions"][0]["approvals"]] == ["goal"]
    assert t["summary"]["missions"] == 1, "finished missions are not counted as live"


def test_members_see_only_their_projects_and_one_mission_by_id():
    pid, gid, *_ = seed()
    assert client(MEMBER).get("/api/swarm").json()["missions"] == []
    r = client(ADMIN).get(f"/api/swarm?goal_id={gid}").json()
    assert [m["id"] for m in r["missions"]] == [gid]
