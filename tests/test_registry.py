import pytest
from sqlalchemy import select

from agent_company import org, registry
from agent_company.db import Agent, Relationship, Room, session


def test_seed_creates_every_room_and_role():
    with session() as s:
        assert {r.key for r in s.scalars(select(Room))} == {r[0] for r in org.ROOMS}
        agents = s.scalars(select(Agent)).all()
        assert {a.role for a in agents} == set(org.ROLES)
        gm = next(a for a in agents if a.role == "general_manager")
        dev = next(a for a in agents if a.role == "developer")
        arch = next(a for a in agents if a.role == "architect")
        assert gm.superior_id is None and dev.superior_id == arch.id and arch.superior_id == gm.id
        assert s.scalars(select(Relationship)).first() is not None
    registry.seed()  # idempotent
    with session() as s:
        assert len(s.scalars(select(Agent)).all()) == len(org.ROLES)


def test_staff_reuses_then_clones_then_recruits():
    a = registry.staff("developer", ["python"])
    assert a.role == "developer" and a.parent_id is None
    b = registry.staff("developer", ["python"])
    assert b.parent_id == a.id and b.name.endswith("#2")
    registry.release(a.id)
    c = registry.staff("developer")
    assert c.id == a.id
    d = registry.staff("data_engineer", ["sql", "python"], writes=True)
    assert d.role == "data_engineer" and "write" in d.permissions
    with session() as s:
        assert registry.related(s, d.id), "a recruit is linked to similar agents"


def test_disabled_roles_fall_back():
    assert registry.resolve_role("frontend") == ("developer", "role 'frontend' is disabled; using 'developer'")
    assert registry.resolve_role("security")[0] == "qa"
    assert registry.resolve_role("Developer") == ("developer", None)
    assert registry.resolve_role("brand new role") == ("brand_new_role", None)


def test_names_cannot_be_edited():
    aid = registry.list_agents()[0]["id"]
    with pytest.raises(ValueError, match="not editable"):
        registry.update_agent(aid, {"name": "x"})
    assert registry.update_agent(aid, {"enabled": False})["enabled"] is False
    with pytest.raises(ValueError):
        registry.update_agent(aid, {"superior_id": aid})


# --- SPEC-03 S03-R-02: discovery and duplicate prevention before recruiting ---------------------

def test_a_planner_synonym_staffs_the_existing_role():
    from agent_company import registry as reg

    role, note = reg.resolve_role("backend_dev")
    assert role == "developer" and "developer" in note
    a = reg.staff(role, ["python", "api"])
    assert a.role == "developer" and a.parent_id is None


def test_an_agent_covering_the_skills_is_reused_instead_of_recruiting():
    from sqlalchemy import func, select

    from agent_company import registry as reg
    from agent_company.db import Agent, Event, session

    with session() as s:
        before = s.scalar(select(func.count()).select_from(Agent))
    a = reg.staff("data_wrangler", ["python", "database"], writes=True)
    assert a.role == "developer", "a developer covers python and database: no duplicate specialist"
    with session() as s:
        assert s.scalar(select(func.count()).select_from(Agent)) == before
        assert "covers 100%" in s.scalars(select(Event).where(Event.kind == "staff")).all()[-1].message
    b = reg.staff("rust_expert", ["rust", "embedded"], writes=True)
    assert b.role == "rust_expert", "nobody covers rust: a specialist is recruited"


def test_near_duplicate_agents_are_listed():
    from agent_company import registry as reg

    first = reg.staff("rust_expert", ["rust"], writes=True)
    reg.release(first.id)
    reg.create_agent("rust_expert", "Rust Expert Two", {"rust": 5}, None, None, "", "")
    pairs = reg.duplicates()
    assert any({p["a"]["name"], p["b"]["name"]} >= {first.name} for p in pairs)
