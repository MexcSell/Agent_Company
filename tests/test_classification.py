"""SPEC-14 S14-R-05: classified orders and uploads, and models that only receive what they are cleared for."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import loop, projects, router
from agent_company.api import create_app
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Goal, Lesson, ModelEntry, Route, Upload, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def users():
    from agent_company import auth

    auth.register("admin", secrets.token_urlsafe(32))


@pytest.fixture
def models():
    """A hosted and a local OpenAI-compatible model, both usable without keys; nothing is ever sent to them."""
    hosted = router.add_model({"name": "Hosted", "kind": "llm", "model_id": "openai/gpt-x",
                               "base_url": "https://api.example.com/v1"})
    local = router.add_model({"name": "Local", "kind": "llm", "model_id": "openai/qwen",
                              "base_url": "http://127.0.0.1:8080/v1"})
    return hosted, local


def _route(role, name):
    with session() as s:
        r = s.get(Route, role)
        if r is None:
            s.add(Route(role=role, model_name=name))
        else:
            r.model_name = name


def test_clearance_defaults_by_locality_and_can_be_set(models):
    hosted, local = models
    assert (hosted["clearance"], local["clearance"]) == ("internal", "restricted")
    with session() as s:
        offline = s.scalars(select(ModelEntry).where(ModelEntry.kind == "offline")).first()
        assert router.clearance(offline) == "secret", "the simulator sends nothing anywhere"
    assert router.update_model(hosted["id"], {"clearance": "confidential"})["clearance"] == "confidential"
    with pytest.raises(ValueError):
        router.update_model(hosted["id"], {"clearance": "top"})
    assert router.update_model(hosted["id"], {"clearance": ""})["clearance"] == "internal"


def test_resolution_skips_models_not_cleared_and_never_falls_back_to_the_simulator(models):
    _route("developer", "Hosted")
    assert router.resolve("developer").name == "Hosted"
    with pytest.raises(router.NotCleared, match="no model is cleared for confidential"):
        router.resolve("developer", classification="confidential")
    assert router.resolve("developer", "Local", classification="restricted").name == "Local"
    with pytest.raises(router.NotCleared):
        router.resolve("developer", "Local", classification="secret")
    _route("developer", "Offline simulator")
    assert router.resolve("developer", classification="secret").kind == "offline", \
        "with no real model at all, the simulator stands in as before"


def test_a_confidential_project_fails_when_no_model_is_cleared(models):
    for role in ("general_manager", "developer", "qa", "architect"):
        _route(role, "Hosted")
    p = projects.create("Vault")
    projects.update(p["id"], {"classification": "confidential"})
    gid = loop.create_goal("Change something", project_id=p["id"])
    assert loop._goal(gid).classification == "confidential"
    with pytest.raises(RuntimeError):  # recorded on the goal, then re-raised (the queue swallows it)
        loop.run_goal(gid)
    g = loop._goal(gid)
    assert g.status == "failed" and "no model is cleared for confidential" in (g.result or "")


def test_order_and_upload_classification_raise_the_goal(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    p = projects.create("Levels")
    up = projects.save_upload("plan.txt", b"numbers", "text/plain", 1, "confidential")
    gid = projects.command(p["id"], ADMIN, "Use the attached plan", [up["id"]])["goal_id"]
    assert loop._goal(gid).classification == "confidential", "an attachment raises the goal"
    gid = projects.command(p["id"], ADMIN, "Secret work", classification="restricted")["goal_id"]
    assert loop._goal(gid).classification == "restricted"
    with session() as s:
        assert s.get(Upload, up["id"]).classification == "confidential"
    up2 = projects.save_upload("b.txt", b"x", "text/plain", 1)
    gid = projects.command(p["id"], ADMIN, "Other", [up2["id"]], classification="secret")["goal_id"]
    with session() as s:
        assert s.get(Upload, up2["id"]).classification == "secret", "an order marks its attachments"
        assert s.get(Goal, gid).classification == "secret"
    projects.update(p["id"], {"classification": "restricted"})
    gid = projects.command(p["id"], ADMIN, "Below the project", classification="public")["goal_id"]
    assert loop._goal(gid).classification == "restricted", "an order cannot lower its project's level"
    with pytest.raises(ValueError):
        projects.command(p["id"], ADMIN, "Bad", classification="top")
    with pytest.raises(ValueError):
        projects.save_upload("c.txt", b"x", "text/plain", 1, "nope")


def test_a_secret_order_in_an_internal_project_stores_no_lesson_text():
    p = projects.create("Mixed", quality_commands=["test -f WORKLOG.md"])
    gid = loop.create_goal("Write the worklog", project_id=p["id"], repo_path=p["path"],
                           quality_commands=["test -f WORKLOG.md"], classification="secret")
    loop.run_goal(gid)
    with session() as s:
        lessons = s.scalars(select(Lesson).where(Lesson.goal_id == gid)).all()
        assert lessons and all(x.classification == "secret" and x.text.startswith("(withheld") for x in lessons)


def test_a_project_raised_later_raises_its_goals(models):
    from agent_company.learning import goal_level

    p = projects.create("Later")
    gid = loop.create_goal("Plan it", project_id=p["id"])
    projects.update(p["id"], {"classification": "confidential"})
    with session() as s:
        assert goal_level(s, s.get(Goal, gid)) == "confidential"


def test_classification_api():
    from agent_company import auth
    from agent_company.db import User

    p = projects.create("Api levels")
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        secret = secrets.token_urlsafe(32)
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(secret)
        assert app.post("/api/auth/device", json={"device_secret": secret}).status_code == 200
        up = app.post("/api/uploads", files={"file": ("a.txt", b"hi", "text/plain")},
                      data={"classification": "restricted"}).json()
        assert up["classification"] == "restricted"
        assert app.post("/api/uploads", files={"file": ("a.txt", b"hi", "text/plain")},
                        data={"classification": "nope"}).status_code == 400
        m = app.get("/api/models").json()["models"]
        assert all("clearance" in x for x in m)
        local = next(x for x in m if x["kind"] == "offline")
        assert local["clearance"] == "secret"
        r = app.post(f"/api/projects/{p['id']}/command", json={"content": "DECISION: x :: y",
                                                                 "classification": "confidential"})
        assert r.status_code == 200
