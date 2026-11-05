"""ASEAO08 S08-FR-03: user stories and use cases follow a template; their actors are listed per project."""

import secrets

import pytest
from fastapi.testclient import TestClient

from agent_company import projects, requirements
from agent_company.api import create_app
from agent_company.db import Goal, session

USE_CASE = """Approve a merge
Actors: Administrator, General Manager
Preconditions: the gate passed
Main flow:
1. The GM asks for approval
2. The administrator approves
3) The loop merges the branch
Alternatives:
- The administrator rejects: the goal goes back to work
Postconditions: the branch is merged"""


def test_a_story_is_parsed_from_its_sentence_and_refused_without_one():
    p = projects.create("Shop")
    r = requirements.create(p["id"], "As a returning customer, I want to pay with a saved card, so that checkout "
                                     "takes one click.", "story")
    assert r["details"] == {"actors": ["returning customer"], "want": "pay with a saved card",
                            "benefit": "checkout takes one click"}
    assert requirements.create(p["id"], "As an admin I can export orders", "story")["details"]["want"] == "export orders"
    with pytest.raises(ValueError, match="As a <actor>, I want"):
        requirements.create(p["id"], "Customers can pay by card", "story")
    # explicit details stand in for a sentence that is not in the template
    r = requirements.create(p["id"], "Saved cards", "story", details={"actors": ["customer"], "want": "save a card"})
    assert r["details"] == {"actors": ["customer"], "want": "save a card", "benefit": ""}
    assert requirements.create(p["id"], "Prices include VAT", "functional")["details"] == {}


def test_a_use_case_needs_actors_and_a_main_flow():
    p = projects.create("Shop")
    d = requirements.create(p["id"], USE_CASE, "use_case")["details"]
    assert d["actors"] == ["Administrator", "General Manager"] and d["preconditions"] == ["the gate passed"]
    assert d["main_flow"] == ["The GM asks for approval", "The administrator approves", "The loop merges the branch"]
    assert d["alternatives"] == ["The administrator rejects: the goal goes back to work"]
    assert d["postconditions"] == ["the branch is merged"]
    with pytest.raises(ValueError, match="actors and its main flow"):
        requirements.create(p["id"], "Approve a merge\nActors: Administrator", "use_case")


def test_a_changed_template_is_a_new_version_and_only_a_changed_story_is_checked_again():
    p = projects.create("Shop")
    r = requirements.create(p["id"], "As a customer, I want to pay by card", "story")
    requirements.set_status(r["id"], "approved", "user:admin")
    out = requirements.update(r["id"], "user:admin", details={"benefit": "I need no cash"})
    assert out["version"] == 2 and out["status"] == "draft" and out["details"]["benefit"] == "I need no cash"
    assert out["history"][0]["details"]["benefit"] == ""
    assert requirements.update(r["id"], "user:admin", subject="payment")["version"] == 2  # not a new version
    with pytest.raises(ValueError, match="a story names who wants what"):
        requirements.update(r["id"], "user:admin", text="Card payments")
    # a requirement stored before templates existed (no details) can still be edited without its text
    with session() as s:
        from agent_company.db import Requirement

        s.get(Requirement, r["id"]).details = None
    assert requirements.update(r["id"], "user:admin", note="legacy")["details"] == {}


def test_actors_are_listed_and_the_planner_sees_the_template():
    p = projects.create("Shop")
    requirements.create(p["id"], "As a customer, I want to pay by card", "story", status="approved")
    requirements.create(p["id"], "As a Customer, I want to see my orders", "story", status="approved")
    requirements.create(p["id"], USE_CASE, "use_case", status="approved")
    requirements.create(p["id"], "As a ghost, I want nothing", "story", status="retired")
    acts = requirements.actors(p["id"])
    assert acts[0] == {"actor": "customer", "keys": ["REQ-001", "REQ-002"]}
    assert {a["actor"] for a in acts} == {"customer", "Administrator", "General Manager"}
    tr = requirements.traceability(p["id"])
    assert tr["actors"] == acts and tr["requirements"][2]["actors"] == ["Administrator", "General Manager"]
    note = requirements.planner_note(Goal(project_id=p["id"], title="approve merge flow", description="",
                                          requirement_keys=["REQ-003", "REQ-001"]))
    assert "actor: customer | wants: pay by card" in note
    assert "Main flow: 1. The GM asks for approval 2. The administrator approves" in note


def test_api_takes_details_and_answers_400_on_a_broken_template():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        pid = c.post("/api/projects", json={"name": "Shop"}).json()["id"]
        r = c.post(f"/api/projects/{pid}/requirements", json={"text": "Card payment", "kind": "story",
                                                               "details": {"actors": ["buyer"], "want": "pay"}})
        assert r.status_code == 200 and r.json()["details"]["actors"] == ["buyer"]
        bad = c.post(f"/api/projects/{pid}/requirements", json={"text": "Card payment", "kind": "story"})
        assert bad.status_code == 400 and "As a <actor>" in bad.json()["detail"]
        out = c.patch(f"/api/requirements/{r.json()['id']}", json={"details": {"benefit": "no cash"}}).json()
        assert out["version"] == 2 and out["details"]["benefit"] == "no cash"
        assert c.get(f"/api/projects/{pid}/traceability").json()["actors"] == [{"actor": "buyer", "keys": ["REQ-001"]}]
