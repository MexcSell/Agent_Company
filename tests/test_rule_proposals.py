"""EL-R-04: a cause (failure layer + failing check) found in 3 failed goals becomes one proposed project rule."""

import pytest
from sqlalchemy import select

from agent_company import catalog, loop, projects, rca
from agent_company.db import Agent, Goal, Lesson, Project, Proposal, session


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def goal(pid, status="failed"):
    gid = loop.create_goal("Change", project_id=pid)
    with session() as s:
        s.get(Goal, gid).status = status
    return gid


def fail(pid, gid, layer="code", check="command false"):
    with session() as s:
        aid = s.scalars(select(Agent.id).order_by(Agent.id)).first()
        s.add(Lesson(agent_id=aid, goal_id=gid, project_id=pid, text="rca", outcome="analysis", kind="operational",
                     status="approved",
                     report={"rca": True, "goals": [gid], "watch": [check],
                             "confirmed": [{"cause": "x", "evidence": [f"check:{check}"], "layer": layer}]}))


def failed(pid, n, **kw):
    ids = []
    for _ in range(n):
        ids.append(goal(pid))
        fail(pid, ids[-1], **kw)
    return ids


def rules(pid):
    with session() as s:
        return [(p.id, p.status, p.title, p.description, dict(p.case), p.spawned_goal_id) for p in s.scalars(
            select(Proposal).where(Proposal.project_id == pid, Proposal.kind == "rule").order_by(Proposal.id))]


def test_three_failed_goals_with_one_cause_give_one_rule_proposal_and_a_fourth_no_second():
    pid = projects.create("P")["id"]
    g1, g2, g3 = failed(pid, 3)
    made = rca.propose_rules(pid)
    [(rid, status, title, desc, case, _)] = rules(pid)
    assert made == [rid] and status == "pending" and title.startswith("Rule: ")
    assert case["cause"] == {"layer": "code", "check": "command false"} and case["goals"] == [g1, g2, g3]
    assert f"#{g1}, #{g2}, #{g3}" in desc and f"#{g1}, #{g2}, #{g3}" in case["rule"]
    with session() as s:
        assert s.get(Proposal, rid).tag == "rule"

    failed(pid, 1)
    assert rca.propose_rules(pid) == []
    [(_, _, _, _, again, _)] = rules(pid)
    assert again["goals"] == [g1, g2, g3], "the pending proposal does not change under its reader"

    projects.decide(rid, False, "tester")
    assert rca.propose_rules(pid) == [] and [r[1] for r in rules(pid)] == ["rejected"], "never proposed again"


def test_fewer_goals_and_causes_that_do_not_count_give_no_proposal():
    pid = projects.create("P")["id"]
    failed(pid, 2)
    assert rca.propose_rules(pid) == []
    failed(pid, 3, check="task spec")  # task keys belong to one plan
    failed(pid, 3, layer="")  # unclassified
    for _ in range(3):  # a streak analysis also covers goals that passed
        fail(pid, goal(pid, status="approved"), check="command other")
    assert rca.propose_rules(pid) == [] and rules(pid) == []

    secret = projects.create("S")["id"]
    with session() as s:
        s.get(Project, secret).classification = "secret"
    failed(secret, 3)
    assert rca.propose_rules(secret) == [] and rca.propose_rules(None) == []


def test_another_layer_or_check_is_a_separate_cause():
    pid = projects.create("P")["id"]
    failed(pid, 3)
    [first] = rca.propose_rules(pid)
    failed(pid, 3, layer="prompt")
    failed(pid, 3, check="command pytest -q")
    assert len(rca.propose_rules(pid)) == 2
    causes = [(r[4]["cause"]["layer"], r[4]["cause"]["check"]) for r in rules(pid)]
    assert sorted(causes) == [("code", "command false"), ("code", "command pytest -q"), ("prompt", "command false")]
    assert [r[0] for r in rules(pid) if r[4]["cause"] == {"layer": "code", "check": "command false"}] == [first]


def test_an_approved_rule_is_an_enabled_catalog_rule_exported_to_claude_rules(tmp_path):
    pid = projects.create("P")["id"]
    failed(pid, 3)
    [rid] = rca.propose_rules(pid)
    with pytest.raises(PermissionError):
        projects.decide(rid, True, "GM", by_human=False)
    with pytest.raises(ValueError):
        projects.set_case(rid, kind="product")
    out = projects.decide(rid, True, "tester")
    [(_, status, _, _, case, spawned)] = rules(pid)
    assert out["status"] == status == "approved" and spawned is None and out["spawned_goal_id"] is None
    [item] = [i for i in catalog.list_items() if i["kind"] == "rule" and i["name"] == case["name"]]
    assert item["enabled"] and case["catalog_id"] == item["id"] and item["source"] == f"proposal:{rid}"
    catalog.export_claude(tmp_path, [])
    text = (tmp_path / ".claude" / "rules" / f"{case['name']}.md").read_text()
    assert case["rule"] in text

    other = projects.create("Q")["id"]  # the same cause in another project: its own proposal, its own name
    failed(other, 3)
    [rid2] = rca.propose_rules(other)
    assert projects.decide(rid2, True, "tester")["status"] == "approved"
    assert rules(other)[0][4]["name"] == f"{case['name'][:56]}-{rid2}"


def test_the_analysis_of_the_third_failed_goal_proposes_the_rule(monkeypatch):
    pid = projects.create("Flaky")["id"]
    ids = []
    for i in range(3):
        ids.append(loop.create_goal(f"Change {i}", project_id=pid, quality_commands=["false"], max_iterations=1))
        loop.run_goal(ids[-1])
    [(_, status, _, _, case, _)] = rules(pid)
    assert status == "pending" and case["cause"] == {"layer": "code", "check": "command false"}
    assert case["goals"] == ids

    def boom(project_id):
        raise RuntimeError("no rules today")

    monkeypatch.setattr(rca, "propose_rules", boom)
    gid = loop.create_goal("Change 4", project_id=pid, quality_commands=["false"], max_iterations=1)
    loop.run_goal(gid)
    report = rca.analyse(gid, "failed after 1 iteration(s)")
    assert report is not None and report["id"] and "rules" not in report
