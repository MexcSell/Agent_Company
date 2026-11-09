"""AICO UI-ORG-02: organisation change proposals, applied only by a person, recorded as a version."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, approvals, gm, organization
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, AuditEvent, Room, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


def dev_id():
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "developer")).first().id


def room(key):
    with session() as s:
        r = s.scalars(select(Room).where(Room.key == key)).first()
        return (r.parent_key, r.lead_agent_id) if r else None


def test_an_approved_proposal_applies_in_order_and_records_a_version():
    dev = dev_id()
    p = organization.propose("A platform lab", [
        {"op": "create_room", "key": "platform_lab", "name": "Platform lab", "division": "development",
         "parent_key": "development"},
        {"op": "update_room", "key": "platform_lab", "changes": {"lead_agent_id": dev}}],
        "Infrastructure work has no home", "Faster platform goals", ["goals #1, #2 waited for a lead"],
        "No agent moves; the lead is set at once", "agent:general_manager")
    assert p["status"] == "pending" and "rooms" in p["current"] and room("platform_lab") is None
    assert any(i["kind"] == "org" and i["id"] == p["id"] for i in approvals.queue(ADMIN))
    with pytest.raises(ValueError, match="only a person"):
        organization.decide_proposal(p["id"], True, "agent:general_manager")
    out = organization.decide_proposal(p["id"], True, "user:admin")
    assert out["status"] == "approved" and out["version"]
    assert room("platform_lab") == ("development", dev)
    assert organization.get_version(out["version"])["snapshot"]["rooms"]["platform_lab"]["lead"] == dev
    with pytest.raises(ValueError, match="approved"):
        organization.decide_proposal(p["id"], True, "user:admin")
    assert "worked" in organization.review_outcome(p["id"], "It worked", "user:admin")["outcome"]


def test_invalid_changes_are_refused_and_a_rejection_changes_nothing():
    with pytest.raises(ValueError, match="unknown room"):
        organization.propose("Bad", [{"op": "update_room", "key": "nowhere", "changes": {"enabled": False}}],
                             "why", actor="user:admin")
    with pytest.raises(ValueError, match="agent fields"):
        organization.propose("Bad", [{"op": "update_agent", "agent_id": dev_id(), "changes": {"skills": {}}}],
                             "why", actor="user:admin")
    p = organization.propose("Off", [{"op": "update_room", "key": "information_collection", "changes": {"description": "x"}}],
                             "why", actor="user:admin")
    assert organization.decide_proposal(p["id"], False, "user:admin", "not now")["status"] == "rejected"


def test_a_change_that_fails_on_apply_marks_the_proposal_failed():
    p = organization.propose("Loop", [{"op": "update_room", "key": "development", "changes": {"parent_key": "development"}}],
                             "why", actor="user:admin")
    out = organization.decide_proposal(p["id"], True, "user:admin")
    assert out["status"] == "failed" and "Not fully applied" in out["decision_note"]


def test_the_gm_toolbox_proposes_and_the_api_decides(capsys):
    gm.main(["org-propose", "Lead for research", "--changes",
             f'[{{"op": "update_room", "key": "information_collection", "changes": {{"lead_agent_id": {dev_id()}}}}}]',
             "--reason", "Nobody leads it"])
    [p] = organization.list_proposals("pending")
    assert p["proposed_by"] == "agent:general_manager"
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    c = TestClient(app)
    assert c.post("/api/org/proposals", json={"title": "x", "reason": "y", "changes": []}).status_code == 400
    r = c.post(f"/api/org/proposals/{p['id']}/decide", json={"approve": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    with session() as s:
        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "org_proposal_approve")).first() is not None
