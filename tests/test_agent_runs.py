"""REQ-006: an agent's run history on its card: paging, run detail and permissions."""
import secrets
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import loop, registry, usage
from agent_company.api import create_app
from agent_company.db import Agent, Goal, Project, Run, Task, session


def _clients():
    app = TestClient(create_app(start_background=False), client=("127.0.0.1", 50000))
    admin = app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
    guest_secret = secrets.token_urlsafe(32)
    guest = TestClient(app.app)
    uid = guest.post("/api/auth/register", json={"name": "guest", "device_secret": guest_secret}).json()["user"]["id"]
    return app, admin.json()["user"]["id"], guest, uid, guest_secret


def _approve(app, guest, uid, guest_secret):
    app.post(f"/api/users/{uid}/approve")
    guest.post("/api/auth/device", json={"device_secret": guest_secret})


def _agents():
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.role == "developer")).first().id
        arch = s.scalars(select(Agent).where(Agent.role == "architect")).first().id
    return dev, arch


def _run(agent_id, goal_id=None, task_id=None, ok=True, error="", tokens=(10, 5), cost=0.01, calls=None):
    usage.record(model_name="sim", kind="sim", agent_id=agent_id, goal_id=goal_id, task_id=task_id, ok=ok,
                 error=error, input_tokens=tokens[0], output_tokens=tokens[1], cost=cost,
                 res=SimpleNamespace(tool_calls=calls if calls is not None else [{"tool": "Read", "input": "x", "ok": True}]))
    with session() as s:
        return s.scalars(select(Run.id).order_by(Run.id.desc())).first()


def test_runs_need_a_signed_in_active_user():
    app, _, guest, uid, guest_secret = _clients()
    with app:
        dev, _ = _agents()
        rid = _run(dev)
        anon = TestClient(app.app)
        assert anon.get(f"/api/agents/{dev}/runs").status_code == 401
        assert anon.get(f"/api/agents/{dev}/runs/{rid}").status_code == 401
        guest.post("/api/auth/device", json={"device_secret": guest_secret})  # not approved yet
        assert guest.get(f"/api/agents/{dev}/runs").status_code == 401
        assert guest.get(f"/api/agents/{dev}/runs/{rid}").status_code == 401
        _approve(app, guest, uid, guest_secret)
        assert guest.get(f"/api/agents/{dev}/runs").status_code == 200
        assert guest.get(f"/api/agents/{dev}/runs/{rid}").status_code == 200


def test_runs_are_paged_newest_first_and_clamped():
    app, *_ = _clients()
    with app:
        dev, _ = _agents()
        ids = [_run(dev) for _ in range(7)]
        pages = [app.get(f"/api/agents/{dev}/runs?limit=3&offset={o}").json() for o in (0, 3, 6)]
        assert all(p["total"] == 7 for p in pages)
        got = [[r["id"] for r in p["runs"]] for p in pages]
        assert [len(g) for g in got] == [3, 3, 1]
        flat = [i for g in got for i in g]
        assert flat == sorted(ids, reverse=True), "newest first, pages neither overlap nor skip"
        past = app.get(f"/api/agents/{dev}/runs?offset=50").json()
        assert past["runs"] == [] and past["total"] == 7
        one = app.get(f"/api/agents/{dev}/runs?limit=0").json()
        assert one["limit"] == 1 and len(one["runs"]) == 1
        assert app.get(f"/api/agents/{dev}/runs?limit=10000").json()["limit"] == registry.RUNS_PAGE_MAX == 200
        neg = app.get(f"/api/agents/{dev}/runs?offset=-5&limit=3").json()
        assert neg["offset"] == 0 and [r["id"] for r in neg["runs"]] == got[0]
        row = pages[0]["runs"][0]
        assert row["model"] == "sim" and row["tokens"] == 15 and row["outcome"] == "ok" and "tool_calls" not in row


def test_guests_see_only_runs_of_projects_they_may_see():
    app, admin_id, guest, uid, guest_secret = _clients()
    with app:
        _approve(app, guest, uid, guest_secret)
        dev, _ = _agents()
        with session() as s:
            p = Project(name="admins-only", created_by=admin_id)
            s.add(p)
            s.flush()
            pid = p.id
        hidden_goal = loop.create_goal("hidden", project_id=pid)
        free_goal = loop.create_goal("no project")
        with session() as s:
            assert s.get(Goal, free_goal).project_id is None
        hidden = _run(dev, hidden_goal)
        no_goal = _run(dev)
        free = _run(dev, free_goal)
        mine = guest.get(f"/api/agents/{dev}/runs").json()
        assert mine["total"] == 2 and {r["id"] for r in mine["runs"]} == {no_goal, free}
        assert guest.get(f"/api/agents/{dev}/runs/{hidden}").status_code == 404
        assert guest.get(f"/api/agents/{dev}/runs/{free}").status_code == 200
        everything = app.get(f"/api/agents/{dev}/runs").json()
        assert everything["total"] == 3 and {r["id"] for r in everything["runs"]} == {hidden, no_goal, free}
        assert app.get(f"/api/agents/{dev}/runs/{hidden}").status_code == 200
        assert next(r for r in everything["runs"] if r["id"] == free)["goal_title"] == "no project"


def test_unknown_agents_and_other_agents_runs_are_not_found():
    app, *_ = _clients()
    with app:
        dev, arch = _agents()
        theirs = _run(arch)
        assert app.get("/api/agents/999999/runs").status_code == 404
        assert app.get(f"/api/agents/999999/runs/{theirs}").status_code == 404
        assert app.get(f"/api/agents/{dev}/runs/{theirs}").status_code == 404
        assert app.get(f"/api/agents/{dev}/runs/999999").status_code == 404
        assert app.get(f"/api/agents/{arch}/runs/{theirs}").status_code == 200


def test_run_detail_has_the_task_output_error_and_tool_calls():
    app, *_ = _clients()
    with app:
        dev, _ = _agents()
        gid = loop.create_goal("detail goal")
        with session() as s:
            t = Task(goal_id=gid, iteration=1, key="build", title="Build it", role="developer", agent_id=dev,
                     output="the task's output", status="failed")
            s.add(t)
            s.flush()
            tid = t.id
        calls = [{"tool": "Read", "input": "a.py", "ok": True}, {"tool": "Bash", "input": "rm x", "ok": False}]
        err = "E" * 500
        rid = _run(dev, gid, tid, ok=False, error=err, calls=calls)
        row = app.get(f"/api/agents/{dev}/runs").json()["runs"][0]
        assert row["id"] == rid and row["outcome"] == "failed" and len(row["error"]) == 200
        assert row["task_key"] == "build" and row["task_title"] == "Build it" and row["tool_call_count"] == 2
        d = app.get(f"/api/agents/{dev}/runs/{rid}").json()
        assert d["tool_calls"] == calls and d["error"] == err
        assert d["task"]["output"] == "the task's output" and d["task"]["status"] == "failed"
        assert d["goal_title"] == "detail goal" and "withheld" in d and "denied_tools" in d
        assert app.get(f"/api/agents/{dev}/runs/{_run(dev)}").json()["task"] is None


def test_agent_detail_usage_is_the_sum_of_its_runs():
    app, *_ = _clients()
    with app:
        dev, arch = _agents()
        before = registry.agent_detail(dev)["usage"]
        _run(dev, tokens=(100, 20), cost=0.25)
        _run(dev, tokens=(7, 3), cost=0.125, ok=False, error="x")
        _run(arch, tokens=(1000, 1000), cost=9.0)
        after = registry.agent_detail(dev)["usage"]
        assert after == {"runs": before["runs"] + 2, "input_tokens": before["input_tokens"] + 107,
                         "output_tokens": before["output_tokens"] + 23,
                         "cost": round(before["cost"] + 0.375, 6)}
