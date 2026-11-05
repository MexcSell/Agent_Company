"""Escalation levels and their recipients (S04-R-07) and escalating unanswered requests (S04-R-08)."""

from sqlalchemy import select

from agent_company import blackboard, decisions, escalation, executors, loop
from agent_company.db import Agent, Event, Notification, Project, session


def agent(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


def escalations(gid):
    with session() as s:
        return [e.data for e in s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "escalation")
                                          .order_by(Event.id))]


def test_each_level_has_its_recipient():
    gid = loop.create_goal("Who")
    dev, architect, gm = agent("developer"), agent("architect"), agent("general_manager")
    with session() as s:
        assert escalation.recipient(s, 1, gid, dev).id == dev
        assert escalation.recipient(s, 2, gid, dev).id == gm
        assert escalation.recipient(s, 3, gid, dev).id == architect  # the head of the development room
        assert escalation.recipient(s, 3, gid, architect).id == gm  # the head itself goes to its superior
        assert escalation.recipient(s, 4, gid, dev).id == s.scalars(select(Project).where(Project.protected)
                                                                    ).first().manager_id
        assert escalation.recipient(s, 5, gid, dev) is None


def test_the_level_follows_category_and_impact():
    assert escalation.level_for("operational") == 1 and escalation.level_for("technical") == 2
    assert escalation.level_for("organizational") == 3 and escalation.level_for("financial") == 4
    assert escalation.level_for("critical") == 5 and escalation.level_for("technical", human=True) == 5
    assert escalation.level_for("technical", protected=True) == 4  # the platform's own code
    assert escalation.level_for("operational", protected=True) == 1


def test_an_organizational_decision_reaches_the_room_manager_in_its_next_prompt():
    gid = loop.create_goal("Org")
    dev, architect = agent("developer"), agent("architect")
    d = decisions.record(gid, "Team split", "two developers", category="organizational", agent_id=dev)
    assert d["status"] == "approved" and d["escalation_level"] == 3
    [esc] = escalations(gid)
    assert esc["level"] == 3 and esc["recipient"] == "room manager" and esc["recipient_agent_id"] == architect
    note = blackboard.inbox(gid, "architect", architect)
    assert "Escalated to you (room manager)" in note and "Team split" in note
    assert blackboard.inbox(gid, "architect", architect) == ""  # delivered once
    decisions.record(gid, "Naming", "snake_case", category="technical", agent_id=dev)
    assert len(escalations(gid)) == 1  # a technical decision is recorded, not escalated


def test_a_decision_about_the_platform_goes_to_the_executive_gm():
    with session() as s:
        pid = s.scalars(select(Project).where(Project.protected)).first().id
    gid = loop.create_goal("Platform", project_id=pid)
    d = decisions.record(gid, "Cache layer", "add one", category="technical")
    [esc] = escalations(gid)
    assert d["escalation_level"] == 4 and esc["recipient"] == "executive GM"
    assert "Cache layer" in blackboard.inbox(gid, "general_manager", agent("general_manager"))


def test_a_check_that_keeps_failing_climbs_the_levels(monkeypatch):
    gid = loop.create_goal("Stubborn", quality_commands=["false"], max_iterations=5)
    assert loop.run_goal(gid)["status"] == "failed"
    levels = [e["level"] for e in escalations(gid)]
    assert levels == [2, 2, 3]  # iterations 2 and 3 to the planner, iteration 4 to the room manager
    last = escalations(gid)[-1]
    assert last["streak"] == 4 and last["recipient_agent_id"] == agent("architect")
    msgs = [e for e in blackboard.read(gid, ["messages"]) if e["meta"].get("from") == "escalation"]
    assert [m["meta"]["to"] for m in msgs] == ["architect"]  # levels 2 reach the planner as feedback instead


def test_a_long_failure_reaches_a_person(monkeypatch):
    monkeypatch.setenv("ESCALATE_AFTER", "1")
    from agent_company import config

    config.reset_settings()
    gid = loop.create_goal("Hopeless", quality_commands=["false"], max_iterations=5)
    loop.run_goal(gid)
    assert [e["level"] for e in escalations(gid)] == [2, 3, 4, 5]
    with session() as s:
        assert s.scalars(select(Notification).where(Notification.source == "escalation",
                                                    Notification.goal_id == gid)).first().admin_only


def test_a_request_repeated_without_an_answer_escalates_to_the_goal_gm():
    gid = loop.create_goal("Ask")
    dev, gm = agent("developer"), agent("general_manager")
    ask = "MESSAGE request to qa: which test data should I use?"
    assert blackboard.extract_messages(gid, 1, "impl", dev, "developer", ask) == 1
    assert blackboard.extract_messages(gid, 2, "impl", dev, "developer", ask) == 0  # a repeat is not stored again
    [esc] = escalations(gid)
    assert esc["level"] == 2 and esc["recipient_agent_id"] == gm and esc["message_key"] == "impl:1"
    blackboard.extract_messages(gid, 3, "impl", dev, "developer", ask)
    assert len(escalations(gid)) == 1  # escalated once
    assert "which test data" in blackboard.inbox(gid, "general_manager", gm)


def test_an_answered_request_does_not_escalate():
    gid = loop.create_goal("Answered")
    dev, qa = agent("developer"), agent("qa")
    ask = "MESSAGE request to qa: which test data should I use?"
    blackboard.extract_messages(gid, 1, "impl", dev, "developer", ask)
    blackboard.extract_messages(gid, 2, "review", qa, "qa", "MESSAGE fact to developer: use tests/data.json")
    blackboard.extract_messages(gid, 3, "impl", dev, "developer", ask)
    assert escalations(gid) == []


def test_the_planner_reads_messages_for_the_gm(monkeypatch):
    prompts = []
    real = executors.OfflineExecutor._plan

    def plan(self, req):
        prompts.append(req.prompt)
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    gid = loop.create_goal("Inbox", quality_commands=["test -f WORKLOG.md"])
    escalation.escalate(2, "the schema needs an owner", goal_id=gid)
    loop.run_goal(gid)
    assert "the schema needs an owner" in prompts[0]

