"""AICO UI-HIST-01: one change timeline from the existing version tables."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, changes, organization, policies, projects, registry
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Agent, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


def test_timeline_kinds_filters_and_paging():
    with session() as s:
        a = s.scalars(select(Agent).where(Agent.role == "developer")).first().id
    registry.update_agent(a, {"max_concurrent": 2}, "user:admin")
    organization.list_versions()
    p = projects.create("Hist")
    policies.set_project_rules(p["id"], {"require_approval": True}, "user:admin")
    t = changes.timeline()
    kinds = {x["kind"] for x in t["items"]}
    assert {"agent_config", "organization", "governance"} <= kinds
    assert t["items"] == sorted(t["items"], key=lambda x: x["at"], reverse=True)
    mine = changes.timeline(agent_id=a)["items"]
    assert mine and all(x["agent_id"] == a for x in mine) and mine[0]["link"] == f"#/agents/{a}"
    gov = changes.timeline(kind="governance", project_id=p["id"])["items"]
    assert gov and all(x["project_id"] == p["id"] for x in gov)
    assert changes.timeline(limit=1)["items"] == t["items"][:1] and changes.timeline(offset=1)["offset"] == 1


def test_api_is_for_administrators():
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: {**ADMIN, "is_admin": False, "id": 5}
    c = TestClient(app)
    assert c.get("/api/changes").status_code in (401, 403)
    app.dependency_overrides[api.admin_user] = lambda: ADMIN
    assert "items" in c.get("/api/changes?days=7&kind=organization").json()
