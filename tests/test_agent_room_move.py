"""Moving an agent to another room: an administrator's edit of `room_key`, checked (room exists and is enabled, a
gated role only while disabled), recorded as a configuration version and an organisation version, and a change an
organisation proposal may carry (UI-ORG-01/02)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, organization, registry
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, Event, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MANAGER = {"id": 2, "name": "manager", "is_admin": False,
           "permissions": {**DEFAULT_PERMISSIONS, "manage_agents": True}}


def agent(role):
    with session() as s:
        a = s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first()
        return a.id, a.room_key


def where(aid):
    with session() as s:
        return s.get(Agent, aid).room_key


def test_a_move_is_a_configuration_and_an_organisation_version():
    dev, home = agent("developer")
    organization.list_versions()  # the baseline
    before = len(organization.list_versions())
    out = registry.update_agent(dev, {"room_key": "analysis"}, "user:admin")
    assert out["room"] == "analysis" and where(dev) == "analysis"
    versions = organization.list_versions()
    assert len(versions) == before + 1 and f"({home} → analysis)" in versions[0]["reason"]
    assert organization.get_version(versions[0]["version"])["snapshot"]["agents"][str(dev)]["room"] == "analysis"
    assert registry.config_versions(dev)[0]["snapshot"]["room_key"] == "analysis"
    with session() as s:
        assert s.scalars(select(Event).where(Event.message.like(f"% moved from room {home} to analysis%"))).first()
    same = len(organization.list_versions())
    registry.update_agent(dev, {"room_key": "analysis"}, "user:admin")
    assert len(organization.list_versions()) == same, "staying in the same room is no move"


def test_unknown_disabled_rooms_and_enabled_gated_agents_are_refused():
    dev, _ = agent("developer")
    with pytest.raises(ValueError, match="unknown room"):
        registry.update_agent(dev, {"room_key": "nowhere"}, "user:admin")
    organization.update_room("analysis", {"enabled": False}, "user:admin")
    with pytest.raises(ValueError, match="disabled"):
        registry.update_agent(dev, {"room_key": "analysis"}, "user:admin")
    organization.update_room("analysis", {"enabled": True}, "user:admin")
    fe, home = agent("frontend")  # a gated role
    registry.update_agent(fe, {"enabled": True}, "user:admin")
    with pytest.raises(ValueError, match="gated role"):
        registry.update_agent(fe, {"room_key": "analysis"}, "user:admin")
    registry.update_agent(fe, {"enabled": False}, "user:admin")
    registry.update_agent(fe, {"room_key": "analysis"}, "user:admin")
    assert where(fe) == "analysis"


def test_only_an_administrator_moves_an_agent_and_it_is_audited():
    dev, _ = agent("developer")
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: MANAGER
    c = TestClient(app)
    assert c.patch(f"/api/agents/{dev}", json={"room_key": "analysis"}).status_code == 403
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    r = c.patch(f"/api/agents/{dev}", json={"room_key": "analysis"})
    assert r.status_code == 200 and r.json()["room"] == "analysis"
    assert c.patch(f"/api/agents/{dev}", json={"room_key": "nowhere"}).status_code == 400
    with session() as s:
        from agent_company.db import AuditEvent

        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "agent_update")).first()


def test_an_organisation_proposal_moves_an_agent_when_a_person_approves():
    dev, home = agent("developer")
    p = organization.propose("Developer to analysis", [
        {"op": "update_agent", "agent_id": dev, "changes": {"room_key": "analysis"}}],
        "Analysis lacks hands", actor="agent:general_manager")
    assert where(dev) == home
    out = organization.decide_proposal(p["id"], True, "user:admin")
    assert out["status"] == "approved" and where(dev) == "analysis"
    with pytest.raises(ValueError, match="agent fields"):
        organization.propose("x", [{"op": "update_agent", "agent_id": dev, "changes": {"permissions": []}}], "y")
