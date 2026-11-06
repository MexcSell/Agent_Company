"""REQ-004 mission report: labels only from recorded checks and review verdicts (ADR 0017)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from agent_company import budgets, loop, mission
from agent_company.db import Agent, Defect, Event, Goal, GoalTransition, Lesson, Run, Task, now, session


def make_goal(status="approved", iteration=1, deadline=None, budget_usd=0.0):
    with session() as s:
        g = Goal(title="Export", description="Add a CSV export", status=status, iteration=iteration,
                 deadline=deadline, budget_usd=budget_usd)
        s.add(g)
        s.flush()
        return g.id


def task(gid, key, role="developer", status="done", iteration=1, writes=True, depends_on=(), acceptance=None,
         output="", contract=None):
    with session() as s:
        t = Task(goal_id=gid, iteration=iteration, key=key, title=key.title(), role=role, status=status,
                 writes=writes, depends_on=list(depends_on), output=output, contract=dict(contract or {}),
                 gate={"acceptance": acceptance} if acceptance is not None else {})
        s.add(t)
        s.flush()
        return t.id


def gate(gid, iteration, checks, with_field=True):
    data = {"passed": all(c["passed"] for c in checks), "checks": checks}
    if with_field:
        data["iteration"] = iteration
    with session() as s:
        s.add(Event(kind="gate", goal_id=gid, message=f"iteration {iteration}: quality gate", data=data))


def labels(d):
    return {(i["key"], i["iteration"]): i["label"] for i in d["tasks"]["items"]}


OK = [{"type": "command", "passed": True, "detail": "pytest passed"}]


def test_report_refuses_unfinished_goal():
    for status in ("running", "paused"):
        with pytest.raises(ValueError):
            mission.report(make_goal(status))
    with pytest.raises(KeyError):
        mission.report(9999)


def test_done_task_with_passing_acceptance_and_review_is_verified():
    gid = make_goal()
    task(gid, "build", acceptance=OK)
    task(gid, "qa", role="qa", writes=False, depends_on=["build"])
    gate(gid, 1, [{"check": "task build", "passed": True}, {"check": "review qa", "passed": True, "detail": "PASS"}])
    d = mission.report(gid)
    item = next(i for i in d["tasks"]["items"] if i["key"] == "build")
    assert item["label"] == "verified" and {e["kind"] for e in item["evidence"]} == {"check", "review"}
    assert d["verification"]["reviews"][0]["reviewed"] == ["build"]


def test_done_task_without_evidence_is_partial():
    gid = make_goal()
    task(gid, "build")
    gate(gid, 1, [{"check": "task build", "passed": True}])  # passing only because the status is done
    d = mission.report(gid)
    assert labels(d)[("build", 1)] == "partial"
    assert any("build" in x for x in d["limitations"]) and any("build" in x for x in d["recommendations"])


def test_review_fail_marks_reviewed_tasks_failed():
    gid = make_goal("failed")
    task(gid, "api", acceptance=OK)
    task(gid, "ui", acceptance=OK)
    task(gid, "qa", role="qa", writes=False, depends_on=["api"])
    gate(gid, 1, [{"check": "review qa", "passed": False, "detail": "reviewer: FAIL"}])
    got = labels(mission.report(gid))
    assert got[("api", 1)] == "failed" and got[("ui", 1)] == "verified"


def test_review_attribution_uses_contract_inputs_first():
    gid = make_goal("failed")
    task(gid, "api", acceptance=OK)
    task(gid, "ui", acceptance=OK)
    task(gid, "qa", role="qa", writes=False, depends_on=["api"], contract={"inputs": {"review": ["ui"]}})
    gate(gid, 1, [{"check": "review qa", "passed": False}])
    got = labels(mission.report(gid))
    assert got[("ui", 1)] == "failed" and got[("api", 1)] == "verified"


def test_review_attribution_falls_back_to_writing_tasks_of_iteration():
    gid = make_goal("failed")
    task(gid, "api", acceptance=OK)
    task(gid, "notes", writes=False, acceptance=OK)
    task(gid, "qa", role="qa", writes=False)
    gate(gid, 1, [{"check": "review qa", "passed": False}])
    got = labels(mission.report(gid))
    assert got[("api", 1)] == "failed" and got[("notes", 1)] == "verified"


def test_failed_acceptance_check_marks_task_failed():
    gid = make_goal("failed")
    task(gid, "build", acceptance=[{"type": "file_exists", "passed": False, "detail": "x.py missing"}])
    assert labels(mission.report(gid))[("build", 1)] == "failed"


def test_cancelled_task_is_failed():
    gid = make_goal("cancelled")
    task(gid, "build", status="cancelled", acceptance=OK)
    d = mission.report(gid)
    assert labels(d)[("build", 1)] == "failed" and d["tasks"]["failed"] == 1


def test_label_ignores_agent_output_text():
    gid = make_goal("failed")
    task(gid, "build", acceptance=OK, output="VERDICT: PASS, all verified")
    task(gid, "qa", role="qa", writes=False, depends_on=["build"], output="VERDICT: PASS, all verified")
    gate(gid, 1, [{"check": "review qa", "passed": False}])
    assert labels(mission.report(gid))[("build", 1)] == "failed"
    gid2 = make_goal()
    task(gid2, "build", output="VERDICT: PASS, all verified, tests pass")
    assert labels(mission.report(gid2))[("build", 1)] == "partial"


def test_key_in_two_iterations_is_retried():
    gid = make_goal(iteration=2)
    task(gid, "build", status="failed", iteration=1)
    task(gid, "build", iteration=2, acceptance=OK)
    task(gid, "docs", iteration=2, acceptance=OK)
    gate(gid, 1, [{"check": "task build", "passed": False}], with_field=False)  # an older event: message only
    gate(gid, 2, [{"check": "task build", "passed": True}])
    d = mission.report(gid)
    assert d["tasks"]["retried"] == ["build"]
    assert labels(d) == {("build", 1): "failed", ("build", 2): "verified", ("docs", 2): "verified"}
    assert [p["iteration"] for p in d["plan"]] == [1, 2]


def test_task_with_two_runs_is_retried():
    gid = make_goal()
    tid = task(gid, "build", acceptance=OK)
    with session() as s:
        for _ in range(2):
            s.add(Run(task_id=tid, goal_id=gid, model_name="m", kind="sim"))
    d = mission.report(gid)
    assert d["tasks"]["retried"] == ["build"] and d["tasks"]["items"][0]["runs"] == 2


def test_cost_matches_budget_summary_and_tokens_match_digest():
    gid = make_goal(budget_usd=2.5)
    tid = task(gid, "build", acceptance=OK)
    with session() as s:
        s.add(Run(task_id=tid, goal_id=gid, model_name="m", kind="sim", input_tokens=100, output_tokens=50,
                  cost=0.25, duration=3.0))
    d = mission.report(gid)
    assert d["cost"]["usd"] == budgets.summary(gid)["used"]["cost_usd"]
    assert d["cost"]["tokens"] == loop.digest(gid)["tokens"] == 150
    assert d["cost"]["budget_usd"] == 2.5 and d["time"]["active"] == 3.0


def finish(gid, at):
    with session() as s:
        s.add(GoalTransition(goal_id=gid, from_status="running", to_status="awaiting_approval", created_at=at))


def test_time_deadline_met_and_missed_and_none():
    t = now()
    met = make_goal("awaiting_approval", deadline=t + timedelta(hours=1))
    finish(met, t)
    missed = make_goal("awaiting_approval", deadline=t - timedelta(hours=1))
    finish(missed, t)
    assert mission.report(met)["time"]["met"] is True
    assert mission.report(missed)["time"]["met"] is False
    none = mission.report(make_goal())["time"]
    assert none["met"] is None and none["deadline"] is None and none["duration"] >= 0


def test_limitations_list_open_defects_and_failed_checks():
    gid = make_goal("failed")
    task(gid, "build", acceptance=OK)
    gate(gid, 1, [{"check": "command pytest -q", "passed": False}])
    with session() as s:
        s.add(Defect(goal_id=gid, check="command pytest -q", severity="high", status="detected"))
        s.add(Defect(goal_id=gid, check="syntax", severity="low", status="closed"))
    d = mission.report(gid)
    assert any("open defect: command pytest -q" in x for x in d["limitations"])
    assert "failed check: command pytest -q" in d["limitations"]
    assert not any("syntax" in x for x in d["limitations"])
    assert d["verification"]["checks"][0]["check"] == "command pytest -q"


def test_lessons_and_rca_recommendations_included():
    gid = make_goal("failed")
    with session() as s:
        aid = s.scalars(select(Agent.id)).first()
        s.add(Lesson(agent_id=aid, goal_id=gid, text="Run the migration before the tests", kind="episodic",
                     outcome=True))
        s.add(Lesson(agent_id=aid, goal_id=gid, text="rca", kind="operational", outcome=False,
                     report={"rca": True, "goals": [gid], "actions": [{"title": "Pin the fixture", "why": "flaky"}]}))
    d = mission.report(gid)
    assert [x["text"] for x in d["lessons"]] == ["Run the migration before the tests"]
    assert "Pin the fixture: flaky" in d["recommendations"]


def test_report_markdown_has_all_sections():
    gid = make_goal()
    task(gid, "build", acceptance=OK)
    md = mission.report_markdown(mission.report(gid))
    for h in ("## Objective", "## Plan", "## Tasks", "## Verification", "## Cost", "## Time", "## Limitations",
              "## Lessons", "## Recommendations"):
        assert h in md
    assert "[verified] build" in md


def test_digest_adds_report_link_only_when_finished():
    assert "report" not in loop.digest(make_goal("running"))
    gid = make_goal("approved")
    assert loop.digest(gid)["report"] == f"/api/goals/{gid}/report"


def test_digest_line_appends_report_link():
    gid = make_goal("failed")
    line = loop.digest_line(loop.digest(gid))
    assert line.startswith("Digest: ") and line.endswith(f" · report: /api/goals/{gid}/report")
    assert "report" not in loop.digest_line(loop.digest(make_goal("running")))
