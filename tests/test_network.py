"""SPEC-12: relationship kinds with evidence, links of a new agent, administrator corrections."""

import secrets

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import learning, loop, network, registry
from agent_company.api import create_app
from agent_company.db import Agent, AuditEvent, Goal, LearningPlan, Relationship, Task, log_event, session


def agent_of(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


def link(a, kind, b):
    with session() as s:
        r = s.scalars(select(Relationship).where(Relationship.a_id == a, Relationship.b_id == b,
                                                 Relationship.kind == kind)).first()
        return None if r is None else (round(r.weight, 3), list(r.evidence or []), r.status)


def certify(agent_id, capability="python", level=3):
    with session() as s:
        s.add(LearningPlan(agent_id=agent_id, capability=capability, current_level=level - 1, target_level=level,
                           final_suite="python-final", status="passed"))


def test_finished_work_writes_directed_review_and_dependency_links_once():
    arch, dev, qa = agent_of("architect"), agent_of("developer"), agent_of("qa")
    gid = loop.create_goal("Add a hello function")
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    reviewed = link(dev, "reviewed_by", qa)
    assert reviewed is not None and reviewed[1][0].startswith("task:"), "the writer was reviewed by QA"
    assert link(qa, "reviewed_by", dev) is None, "the direction is kept"
    assert link(dev, "depends_on", arch) is not None
    with session() as s:
        iteration = s.get(Goal, gid).iteration
    network.observe(gid, iteration, True)  # the same tasks again: duplicate evidence is ignored (AR-015)
    assert link(dev, "reviewed_by", qa) == reviewed
    kinds = {r["kind"]: r["origin"] for r in network.of_agent(dev)}
    assert kinds["reviewed_by"] == kinds["depends_on"] == kinds["collaborated"] == "observed"


def test_handover_and_knowledge_contributor_links():
    dev, qa = agent_of("developer"), agent_of("qa")
    with session() as s:
        network.handover(s, dev, qa, 1, "build", 2)
        network.handover(s, dev, qa, 1, "build", 2)
    assert link(dev, "handover_to", qa)[0] == 0.4, "counted once"
    gid = loop.create_goal("Reuse what QA learned")
    learning.learn(qa, None, "Pin dependency versions in requirements files", True, ["python"])
    with session() as s:
        item = s.scalars(select(learning.Lesson).where(learning.Lesson.agent_id == qa)).first().id
    log_event("retrieval", "1 item", goal_id=gid, agent_id=dev, ids=[item])  # before the run: the lesson step
    assert loop.run_goal(gid)["status"] == "awaiting_approval"                # observes it when the goal passes
    assert f"goal:{gid}" in link(qa, "knowledge_contributor", dev)[1]
    with session() as s:
        for r in s.scalars(select(Relationship).where(Relationship.kind == "knowledge_contributor")):
            s.delete(r)
        iteration = s.get(Goal, gid).iteration
    network.observe(gid, iteration, passed=False)
    assert link(qa, "knowledge_contributor", dev) is None, "only a goal that passed counts"
    network.observe(gid, iteration, passed=True)
    assert link(qa, "knowledge_contributor", dev) is not None


def test_a_new_agent_gets_complementary_specialists_and_a_mentor_candidate():
    dev, qa, arch = agent_of("developer"), agent_of("qa"), agent_of("architect")
    learning.link_team(7, [(dev, "done"), (qa, "done"), (arch, "done")], passed=True)
    certify(dev, "python", 3)
    new = registry.create_agent("developer", "Developer B", {"python": 5})
    mentor = link(new["id"], "mentored_by", dev)
    assert mentor is not None and mentor[1] == ["candidate:python:L3"]
    rels = {(r["kind"], r["b_id"] if r["a_id"] == new["id"] else r["a_id"]): r for r in network.of_agent(new["id"])}
    assert ("complementary_skill", qa) in rels and ("complementary_skill", arch) in rels
    assert rels[("mentored_by", dev)]["origin"] == "inferred"
    assert any(k == "similar" for k, _ in rels)


def test_related_uses_only_experience_sharing_links_that_are_not_expired():
    dev, qa = agent_of("developer"), agent_of("qa")
    with session() as s:
        for r in s.scalars(select(Relationship)):
            s.delete(r)
        registry.connect(s, dev, qa, "depends_on", 0.6, evidence="task:1")
    with session() as s:
        assert registry.related(s, dev) == [], "a dependency is not shared experience"
        registry.connect(s, dev, qa, "collaborated", 0.6, evidence="goal:1:passed")
    with session() as s:
        assert [a.id for a, _ in registry.related(s, dev)] == [qa]
    network.correct(dev, qa, "collaborated", "admin", "they never worked together", status="expired")
    with session() as s:
        assert registry.related(s, dev) == []


def test_an_administrator_corrects_a_relationship_and_it_is_audited():
    dev, qa = agent_of("developer"), agent_of("qa")
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        r = app.post(f"/api/agents/{dev}/relationships", json={"other_id": qa, "kind": "testing_partner",
                                                               "reason": "x"})
        assert r.status_code == 400 and "weight" in r.json()["detail"]
        r = app.post(f"/api/agents/{dev}/relationships", json={"other_id": qa, "kind": "testing_partner",
                                                               "weight": 0.7, "reason": "paired on the QA rota"})
        assert r.status_code == 200 and r.json()["origin"] == "corrected" and r.json()["weight"] == 0.7
        r = app.post(f"/api/agents/{dev}/relationships", json={"other_id": qa, "kind": "friend", "weight": 1,
                                                               "reason": "x"})
        assert r.status_code == 400
        listed = app.get(f"/api/agents/{dev}/relationships").json()
        assert any(x["kind"] == "testing_partner" and x["evidence"][-1].startswith("admin:admin:") for x in listed)
    with session() as s:
        audit = s.scalars(select(AuditEvent).where(AuditEvent.action == "relationship_correct")).one()
        assert audit.after["reason"] == "paired on the QA rota"
        assert not audit.before, "the link did not exist before"


# --- S12-R-03 fair team recommendation ----------------------------------------------------------

def outcomes(agent_id, results, start=1000):
    for i, ok in enumerate(results):
        learning.learn(agent_id, None, f"outcome {start + i}", ok, ["python"], task_id=start + i)


def test_a_new_agent_has_insufficient_evidence_not_a_penalty_and_old_failures_decay():
    dev = agent_of("developer")
    new = registry.create_agent("developer", "Developer B")["id"]
    outcomes(dev, [True, True, False, True])
    outcomes(new, [False], start=2000)  # one failure is not incompetence
    with session() as s:
        ranks = registry._rank(s, [s.get(Agent, dev), s.get(Agent, new)], ["python"])
    assert ranks[dev]["quality"] == 0.75 and ranks[dev]["evidence"] == 4
    assert ranks[new]["evidence"] == 1 and ranks[new]["quality"] == 0.75, "the pool's mean, neither 0 nor 0.5"
    outcomes(dev, [False] * 5, start=3000)
    outcomes(dev, [True] * 20, start=4000)  # the failures are older than the last 20 outcomes now
    with session() as s:
        assert registry._rank(s, [s.get(Agent, dev)], ["python"])[dev]["quality"] == 1.0


def test_exploration_gives_a_new_agent_a_non_critical_task_only(monkeypatch):
    dev = agent_of("developer")
    new = registry.create_agent("developer", "Developer B")["id"]
    outcomes(dev, [True] * 5)
    with session() as s:
        s.get(Agent, dev).skills = {**s.get(Agent, dev).skills, "python": 9}
    first = registry.staff("developer", ["python"], loop.create_goal("normal"))
    assert first.id == dev, "without exploration the evidence wins"
    registry.release(dev)
    monkeypatch.setattr(registry, "_random", lambda: 0.0)
    assert registry.staff("developer", ["python"], loop.create_goal("high risk"), critical=True).id == dev
    registry.release(dev)
    eval_goal = loop.create_goal("an exercise", origin="eval")
    assert registry.staff("developer", ["python"], eval_goal).id == dev, "evaluations never explore"
    registry.release(dev)
    picked = registry.staff("developer", ["python"], loop.create_goal("low risk"))
    assert picked.id == new


def test_each_staffing_decision_is_recorded_in_its_task():
    gid = loop.create_goal("Add a hello function")
    loop.run_goal(gid)
    with session() as s:
        tasks = list(s.scalars(select(Task).where(Task.goal_id == gid)))
    staffing = {t.key: t.contract.get("staffing") for t in tasks}
    build = staffing["build"]
    assert build["action"] == "assigned" and build["agent_id"] == next(t.agent_id for t in tasks if t.key == "build")
    assert build["candidates"] and {"coverage", "quality", "evidence", "workload", "score"} <= set(build["candidates"][0])
    assert build["explored"] is False
