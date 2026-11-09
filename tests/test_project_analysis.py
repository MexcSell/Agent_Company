"""UI-GM-02 (FR-003.5, S10-R-01): a message to "New project…" asks the GM to analyse the request and propose a
project; it starts no code, the proposal carries its documented case, and only a person turns it into a project."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import auth, executors, loop, projects
from agent_company.api import create_app
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Goal, Project, Proposal, Task, User, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
CASE = ("Analysis done.\nPROPOSAL[new_project]: Recipe Planner :: families plan meals by hand\n"
        "- Concept: a web app that plans a week of meals\n- Target user: families cooking at home\n"
        "- Expected value: an hour saved a week\n- Feasibility: small, a CRUD app\n"
        "- Requirements: recipes, a weekly plan, a shopping list\n- Estimated cost: two weeks of agent time\n")


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def users():
    admin, other = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    auth.register("admin", admin)
    auth.register("bob", other)
    with session() as s:
        s.get(User, 2).status = "active"
    return admin, other


@pytest.fixture
def analyst(monkeypatch):
    """Every non-review task ends with the case block, as parallel tasks of a real analysis might."""
    task = executors.OfflineExecutor._task

    def answer(self, req):
        out = task(self, req)
        return out if req.role == "qa" or req.purpose == "review" else out + "\n" + CASE

    monkeypatch.setattr(executors.OfflineExecutor, "_task", answer)


def test_an_analysis_writes_nothing_and_ends_in_one_documented_proposal(analyst):
    users()
    gid = projects.analyse_new("A meal planner for families\nwith a shopping list", ADMIN)
    with session() as s:
        g = s.get(Goal, gid)
        assert g.project_id is None and g.kind == "research" and g.topic == projects.ANALYSIS_TOPIC
        assert "PROPOSAL[new_project]" in g.description and g.title == "New project analysis: A meal planner for families"
    assert loop.run_goal(gid)["status"] in ("awaiting_approval", "approved"), "the research template's gate passes"
    with session() as s:
        props = s.scalars(select(Proposal).where(Proposal.goal_id == gid)).all()
        assert not s.scalars(select(Project).where(Project.name == "Recipe Planner")).first()
    assert [p.title for p in props] == ["Recipe Planner"], "one proposal however many tasks repeat it"
    with session() as s:
        tasks = s.scalars(select(Task).where(Task.goal_id == gid)).all()
    assert tasks and not any((t.contract or {}).get("writes") for t in tasks), "the research template writes nothing"
    [a] = projects.analyses(ADMIN)
    assert a["goal_id"] == gid and a["request"].startswith("A meal planner") and "PROPOSAL" not in a["request"]
    [p] = a["proposals"]
    assert p["kind"] == "new_project" and p["case_missing"] == [] and p["status"] == "pending"
    with pytest.raises(PermissionError):
        projects.decide(p["id"], True, "policy", by_human=False)
    out = projects.decide(p["id"], True, "admin")
    with session() as s:
        made = s.get(Project, out["spawned_project_id"])
        assert made.name == "Recipe Planner"


def test_an_empty_request_is_refused_and_people_see_only_their_own():
    users()
    with pytest.raises(ValueError):
        projects.analyse_new("   ", ADMIN)
    mine = projects.analyse_new("A chess clock", ADMIN)
    other = {"id": 2, "name": "bob", "is_admin": False, "permissions": dict(DEFAULT_PERMISSIONS)}
    assert [a["goal_id"] for a in projects.analyses(ADMIN)] == [mine]
    assert projects.analyses(other) == []


def test_the_api_needs_both_permissions_and_is_audited(monkeypatch):
    admin, other = users()
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        assert app.post("/api/auth/device", json={"device_secret": admin}).status_code == 200
        monkeypatch.setattr(app.app.state, "queue", type("Q", (), {"submit": lambda self, gid: None})())
        r = app.post("/api/project-analyses", json={"text": "A bird-song recogniser"})
        assert r.status_code == 200
        assert [a["goal_id"] for a in app.get("/api/project-analyses").json()] == [r.json()["goal_id"]]
        assert app.post("/api/project-analyses", json={"text": " "}).status_code == 400
        assert "project_analysis" in {a["action"] for a in app.get("/api/audit").json()["records"]}
        bob = TestClient(app.app)
        assert bob.post("/api/auth/device", json={"device_secret": other}).status_code == 200
        assert bob.post("/api/project-analyses", json={"text": "x"}).status_code == 403, "run alone is not enough"
        auth.set_permissions(2, {"create_projects": True})
        r = bob.post("/api/project-analyses", json={"text": "A kite designer"})
        assert r.status_code == 200
        assert [a["goal_id"] for a in bob.get("/api/project-analyses").json()] == [r.json()["goal_id"]]


def test_the_person_who_asked_answers_the_questions_and_the_queue_links_the_gm_page(monkeypatch):
    from agent_company import approvals

    admin, other = users()
    auth.set_permissions(2, {"create_projects": True})
    bob = {"id": 2, "name": "bob", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "create_projects": True}}
    mine, theirs = projects.analyse_new("A tide-table app", bob), projects.analyse_new("A chess clock", ADMIN)
    with session() as s:
        for gid in (mine, theirs):
            g = s.get(Goal, gid)
            g.status, g.checkpoint = "paused", {"paused_for": "clarification", "questions": ["Which coast?"]}
    projects.extract_proposals(theirs, 1, "PROPOSAL[new_project]: Chess Clock :: players\n- Concept: a clock")
    links = {(i["kind"], i["id"]): i["link"] for i in approvals.queue(ADMIN)}
    assert links[("questions", mine)] == "#/general-manager/new"
    assert next(v for (k, _), v in links.items() if k == "proposal") == "#/general-manager/new"
    monkeypatch.setattr(loop, "resume", lambda gid, actor="": None, raising=False)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        assert app.post("/api/auth/device", json={"device_secret": other}).status_code == 200
        r = app.post(f"/api/goals/{mine}/questions/answer", json={"answers": ["the North Sea"]})
        assert r.status_code == 200, r.text
        assert app.post(f"/api/goals/{theirs}/questions/answer", json={"answers": ["x"]}).status_code == 403
