"""SPEC-01 organisation editing: rooms with history (S01-R-01), position history (S01-R-02), export (S01-R-04)."""

import csv
import io
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import loop, organization, projects, registry
from agent_company.api import create_app
from agent_company.db import Agent, AuditEvent, Task, session


@pytest.fixture
def admin():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        yield c


def agent_of(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


# --- S01-R-01 rooms ---------------------------------------------------------------------------

def test_a_room_cannot_be_its_own_ancestor():
    organization.create_room("platform", "Platform Group", "development")
    organization.update_room("development", {"parent_key": "platform"})
    with pytest.raises(ValueError, match="own ancestor"):
        organization.update_room("platform", {"parent_key": "development"})
    with pytest.raises(ValueError, match="own ancestor"):
        organization.update_room("platform", {"parent_key": "platform"})
    with pytest.raises(ValueError, match="unknown parent"):
        organization.update_room("platform", {"parent_key": "nowhere"})
    assert organization.get_room("development")["parent"] == "platform"
    with pytest.raises(ValueError, match="exists"):
        organization.create_room("platform", "Again", "development")
    with pytest.raises(ValueError, match="division"):
        organization.create_room("other", "Other", "marketing")


def test_edits_survive_the_seed():
    organization.update_room("analysis", {"name": "Findings Room"})
    registry.seed()
    assert organization.get_room("analysis")["name"] == "Findings Room"


def test_a_room_with_running_goals_cannot_be_disabled_and_a_disabled_room_is_not_staffed():
    gid = loop.create_goal("Something", quality_commands=[])
    qa = registry.staff("qa", [], gid)
    with session() as s:
        s.add(Task(goal_id=gid, iteration=1, key="review", title="Review", role="qa", agent_id=qa.id, status="running"))
    with pytest.raises(ValueError, match="running goals"):
        organization.update_room("development", {"enabled": False})
    registry.release(qa.id, gid)
    loop.cancel(gid)
    room = organization.update_room("development", {"enabled": False})
    assert room["enabled"] is False
    with pytest.raises(RuntimeError, match="disabled room"):
        registry.staff("developer", ["python"], None, writes=True)
    assert registry.resolve_role("frontend")[0] != "frontend"
    organization.update_room("development", {"enabled": True})
    assert registry.staff("developer", ["python"], None, writes=True).role == "developer"


def test_the_general_managers_room_cannot_be_disabled():
    with pytest.raises(ValueError, match="General Manager"):
        organization.update_room("general_management", {"enabled": False})


def test_a_project_cannot_add_a_disabled_room_but_keeps_one_it_listed():
    p = projects.create("Rooms project")
    projects.update(p["id"], {"rooms": ["analysis"]})
    organization.update_room("analysis", {"enabled": False})
    organization.update_room("sales", {"enabled": False})
    assert projects.update(p["id"], {"rooms": ["analysis", "development"]})["rooms"] == ["analysis", "development"]
    with pytest.raises(ValueError, match="disabled room"):
        projects.update(p["id"], {"rooms": ["analysis", "sales"]})


def test_room_api_is_audited_with_before_and_after(admin):
    r = admin.post("/api/rooms", json={"key": "platform", "name": "Platform", "division": "development"})
    assert r.status_code == 200 and r.json()["enabled"]
    r = admin.patch("/api/rooms/analysis", json={"name": "Findings", "parent_key": "platform"})
    assert r.status_code == 200 and r.json()["parent"] == "platform"
    assert admin.patch("/api/rooms/platform", json={"parent_key": "analysis"}).status_code == 400
    assert admin.patch("/api/rooms/analysis", json={"key": "x"}).status_code == 400
    assert admin.patch("/api/rooms/nowhere", json={"name": "x"}).status_code == 404
    with session() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.action == "room_update")).first()
        assert ev.before == {"name": "Analysis Room", "parent_key": None}
        assert ev.after == {"name": "Findings", "parent_key": "platform"}
    other = TestClient(admin.app)
    assert other.patch("/api/rooms/analysis", json={"name": "y"}).status_code == 401


# --- S01-R-02 position history ---------------------------------------------------------------

def test_lead_and_project_gm_changes_are_recorded():
    analyst, research = agent_of("analyst"), agent_of("research")
    organization.update_room("analysis", {"lead_agent_id": analyst}, actor="user:luca")
    organization.update_room("analysis", {"lead_agent_id": research}, actor="user:luca", reason="analyst moved")
    organization.update_room("analysis", {"name": "Analysis"})  # no lead change: no record
    [second, first] = organization.positions("room:analysis:lead")
    assert (first["previous_agent_id"], first["new_agent_id"]) == (None, analyst)
    assert (second["previous_agent_id"], second["new_agent_id"], second["reason"]) == (analyst, research, "analyst moved")
    assert second["actor"] == "user:luca" and second["new_agent"]
    assert organization.holder("room:analysis:lead") == research
    assert organization.holder("room:analysis:lead", at=first["effective_at"]) in {analyst, research}

    p = projects.create("GM project")
    gm = agent_of("general_manager")
    assert organization.holder(f"project:{p['id']}:gm") == gm
    other = registry.create_agent("general_manager", "Project GM")["id"]
    projects.update(p["id"], {"manager_id": other, "manager_reason": "dedicated GM"}, actor="user:luca")
    latest = organization.positions(f"project:{p['id']}:gm")[0]
    assert (latest["previous_agent_id"], latest["new_agent_id"], latest["reason"]) == (gm, other, "dedicated GM")
    with pytest.raises(ValueError, match="active agent"):
        projects.update(p["id"], {"manager_id": 99999})


def test_a_retired_agent_cannot_lead():
    a = registry.create_agent("analyst", "Old analyst")
    registry.set_lifecycle(a["id"], "retired")
    with pytest.raises(ValueError, match="retired"):
        organization.update_room("analysis", {"lead_agent_id": a["id"]})


# --- S01-R-04 export -------------------------------------------------------------------------

def test_export_as_json_and_csv(admin):
    organization.update_room("analysis", {"lead_agent_id": agent_of("analyst")})
    out = admin.get("/api/org/export").json()
    assert {r["key"] for r in out["rooms"]} >= {"development", "analysis"}
    dev = next(a for a in out["agents"] if a["role"] == "developer")
    assert dev["superior"] and dev["lifecycle"] == "active" and dev["certification_level"] == 0
    assert isinstance(out["relationships"], list)
    r = admin.get("/api/org/export?format=csv&table=agents")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert any(x["role"] == "general_manager" for x in rows) and "certification_level" in rows[0]
    rooms = list(csv.DictReader(io.StringIO(admin.get("/api/org/export?format=csv&table=rooms").text)))
    assert next(x for x in rooms if x["key"] == "analysis")["lead_agent_id"]
    assert admin.get("/api/org/export?format=xml").status_code == 400
    assert admin.get("/api/org/export?format=csv&table=goals").status_code == 400
