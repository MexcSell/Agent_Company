"""ASEAO05 S05-R-09: a new project GM gets a handover; one agent needed by two projects' goals."""

from sqlalchemy import select

from agent_company import loop, management, projects, registry
from agent_company.db import ActionItem, Agent, Event, Goal, Message, ProjectHandover, Relationship, Risk, session
from agent_company.workflow import set_status


def _agent(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


def test_replacing_the_gm_writes_a_handover_and_moves_its_open_work():
    p = projects.create("Handover")
    old = p["manager_id"]
    gid = loop.create_goal("Ship the importer", project_id=p["id"])
    done = loop.create_goal("Old work", project_id=p["id"])
    with session() as s:
        set_status(s, s.get(Goal, done), "cancelled", "test")
    blocker = management.create_risk(p["id"], "Vendor API down", kind="blocker", owner_agent_id=old)
    item = management.create_item(p["id"], "Write the release notes", responsible_agent_id=old)
    new = registry.create_agent("general_manager", "Project GM")["id"]

    projects.update(p["id"], {"manager_id": new, "manager_reason": "dedicated GM"}, actor="user:luca")

    with session() as s:
        h = s.scalars(select(ProjectHandover).where(ProjectHandover.project_id == p["id"])).one()
        assert (h.from_agent_id, h.to_agent_id, h.actor, h.reason) == (old, new, "user:luca", "dedicated GM")
        assert [g["id"] for g in h.package["goals"]] == [gid]  # final goals are not handed over
        assert [b["title"] for b in h.package["blockers"]] == ["Vendor API down"]
        titles = [a["title"] for a in h.package["action_items"]]
        assert "Write the release notes" in titles and "Resolve blocker: Vendor API down" in titles  # after a sync
        assert s.get(Risk, blocker["id"]).owner_agent_id == new
        assert s.get(ActionItem, item["id"]).responsible_agent_id == new
        assert s.scalars(select(Relationship).where(Relationship.a_id == old, Relationship.b_id == new,
                                                    Relationship.kind == "handover_to")).first() is not None
        assert any("Handover: 1 open goal(s), 1 open blocker(s)" in m.content
                   for m in s.scalars(select(Message).where(Message.project_id == p["id"])))
    assert management.overview(p["id"])["handovers"][0]["to_agent_id"] == new
    assert any(x.get("project_id") == p["id"] for x in registry.agent_detail(new)["handovers"])

    # the same GM again is no replacement
    projects.update(p["id"], {"manager_id": new})
    assert len(management.list_handovers(p["id"])) == 1


def _busy_on(agent_id, goal_id):
    with session() as s:
        a = s.get(Agent, agent_id)
        a.max_concurrent = 2
        a.active_goals = [goal_id]
        a.status = "busy"


def test_an_agent_working_for_another_project_is_not_shared_while_a_free_one_exists():
    a, b = projects.create("Alpha"), projects.create("Beta")
    ga = loop.create_goal("Alpha work", project_id=a["id"])
    gb = loop.create_goal("Beta work", project_id=b["id"])
    dev = _agent("developer")
    _busy_on(dev, ga)
    with session() as s:
        s.get(Agent, dev).skills = {"python": 5}  # the best-ranked developer
    other = registry.create_agent("developer", "Second developer", skills={"python": 1})["id"]

    got = registry.staff("developer", ["python"], gb)
    assert got.id == other
    with session() as s:
        ev = s.scalars(select(Event).where(Event.kind == "staffing_conflict", Event.goal_id == gb)).one()
        assert ev.agent_id == ev.data["shared_agent_id"] == dev and ev.data["projects"] == [a["id"]]
        assert ev.data["resolution"] == "free agent Second developer"


def test_an_agent_working_for_another_project_is_cloned_when_nobody_else_is_free():
    a, b = projects.create("Alpha"), projects.create("Beta")
    ga = loop.create_goal("Alpha work", project_id=a["id"])
    gb = loop.create_goal("Beta work", project_id=b["id"])
    dev = _agent("developer")
    with session() as s:
        for x in s.scalars(select(Agent).where(Agent.role == "developer", Agent.id != dev)):
            x.enabled = False
    _busy_on(dev, ga)

    got = registry.staff("developer", [], gb)
    assert got.id != dev and got.parent_id == dev
    with session() as s:
        assert s.scalars(select(Event).where(Event.kind == "staffing_conflict", Event.goal_id == gb)).one()

    # a second goal of the same project shares the agent without a conflict
    ga2 = loop.create_goal("More Alpha work", project_id=a["id"])
    with session() as s:
        s.get(Agent, got.id).enabled = False
    assert registry.staff("developer", [], ga2).id == dev
    with session() as s:
        assert s.scalars(select(Event).where(Event.kind == "staffing_conflict", Event.goal_id == ga2)).first() is None
