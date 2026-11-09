"""AICO UI-ORG-01/03/04: organisation versions, comparison and restore."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, organization, registry
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, OrgVersion, Room, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


def _count():
    with session() as s:
        return len(s.scalars(select(OrgVersion)).all())


def test_each_structural_change_is_one_version_and_restore_sets_rooms_back():
    base = organization.list_versions()[0]["version"]  # the baseline
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.role == "developer")).first().id
    organization.create_room("ops_lab", "Ops lab", "operations" if "operations" in organization.DIVISIONS
                             else sorted(organization.DIVISIONS)[0])
    assert _count() == 2
    organization.update_room("ops_lab", {"lead_agent_id": dev}, "user:admin", "a lead")
    assert _count() == 3
    organization.update_room("ops_lab", {"description": "text only"}, "user:admin")
    assert _count() == 3, "a description is not a structural change"
    organization.update_room("ops_lab", {"enabled": False}, "user:admin")
    v_off = organization.list_versions()[0]["version"]
    diff = organization.compare_versions(base, v_off)
    assert [r["key"] for r in diff["rooms"]["added"]] == ["ops_lab"] and not diff["rooms"]["removed"]
    diff2 = organization.compare_versions(v_off - 1, v_off)
    assert diff2["rooms"]["changed"][0]["fields"] == {"enabled": [True, False]}
    out = organization.restore_version(v_off - 1, "user:admin")
    assert out["changed"] == ["ops_lab"] and out["version"] == v_off + 1
    with session() as s:
        r = s.scalars(select(Room).where(Room.key == "ops_lab")).first()
        assert r.enabled is True and r.lead_agent_id == dev
    restored = organization.restore_version(base, "user:admin")
    assert any(x.startswith("ops_lab") for x in restored["changed"]) is False, "a later room is left as it is"


def test_agent_moves_are_versions_and_the_api():
    organization.list_versions()
    with session() as s:
        a, boss = s.scalars(select(Agent).where(Agent.role.in_(("developer", "architect"))).limit(2)).all()
        aid, bid = a.id, boss.id
    before = _count()
    registry.update_agent(aid, {"superior_id": bid}, "user:admin")
    assert _count() == before + 1
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    app.dependency_overrides[api.admin_user] = lambda: ADMIN
    c = TestClient(app)
    vs = c.get("/api/org/versions").json()
    top = vs[0]["version"]
    cmp = c.get(f"/api/org/compare?a={top - 1}&b={top}").json()
    assert cmp["agents"]["changed"][0]["fields"]["superior"][1] == bid
    assert c.post(f"/api/org/versions/{top - 1}/restore").status_code == 200
    assert "org_restore" in {r["action"] for r in c.get("/api/audit").json()["records"]}
    assert c.get("/api/org/versions/9999").status_code == 404
