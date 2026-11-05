"""Pipeline quality leftovers (docs/requirements, suggested order step 2): task contract fields (G-R-01),
conflict review (S04-R-03), re-planning an invalid contract and escalating a check that keeps failing (S04-R-06)."""

from sqlalchemy import select

from agent_company import decisions, executors, loop
from agent_company.db import Event, Goal, Task, session

REVIEW = ("## Logic verification\n- Normal case: fine.\n- Edge case: fine.\n- Error case: fine.\n\n"
          "OUTCOME: APPROVED\nVERDICT: PASS")


def with_plan(monkeypatch, tasks):
    monkeypatch.setattr(executors, "standard_plan", lambda goal: {"summary": "test plan", "tasks": tasks})


def tasks_of(gid):
    with session() as s:
        return {t.key: t for t in s.scalars(select(Task).where(Task.goal_id == gid))}


def events(gid, kind):
    with session() as s:
        return list(s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == kind)))


# --- G-R-01 -------------------------------------------------------------------------------------------

def test_an_out_of_scope_change_fails_the_task_as_a_scope_violation(monkeypatch):
    plan = executors.standard_plan("x")["tasks"]
    plan[1]["out_of_scope"] = ["WORKLOG.md"]
    with_plan(monkeypatch, plan)
    gid = loop.create_goal("Stay in scope", quality_commands=["test -f WORKLOG.md"], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "failed"
    build = tasks_of(gid)["build"]
    assert build.status == "failed"
    assert any(c["condition"]["type"] == "out_of_scope" and "WORKLOG.md" in c["detail"]
               for c in build.gate["acceptance"])
    assert loop._goal(gid).history[0]["scope_failed"]


def test_a_high_risk_task_gets_a_security_review_by_qa_while_security_is_disabled(monkeypatch):
    plan = executors.standard_plan("x")["tasks"]
    plan[1]["risk_level"] = "high"  # a high-risk goal needs an integration check too (S09-R-02)
    plan[1]["acceptance"].append({"type": "command", "run": "test -f WORKLOG.md", "stage": "integration"})
    with_plan(monkeypatch, plan)
    gid = loop.create_goal("Risky", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    review = tasks_of(gid)["build-security"]
    assert review.depends_on == ["build"] and review.status == "done" and review.role == "qa"
    with_reviews = loop._with_security_reviews(loop.contracts.validate_plan(plan))
    assert len(loop._with_security_reviews(with_reviews)) == len(with_reviews), "added once"


def test_findings_next_actions_and_the_contract_reach_the_trace_and_the_prompt(monkeypatch):
    plan = executors.standard_plan("x")["tasks"]
    plan[0].update(test_plan="run the CLI with no arguments", requirement_keys=["S08-FR-02"],
                   in_scope=["src/cli/*"])
    with_plan(monkeypatch, plan)
    prompts = {}
    real = executors.OfflineExecutor._task

    def task(self, req):
        prompts[req.data["title"]] = req.prompt
        out = real(self, req)
        return out + ("\nFINDING: the spec has no error cases\nNEXT: add error cases" if req.role == "architect" else "")

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = loop.create_goal("Traceable", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    spec = next(t for t in loop.trace(gid)["tasks"] if t["key"] == "spec")
    assert spec["findings"] == ["the spec has no error cases"] and spec["next_actions"] == ["add error cases"]
    p = prompts["Write the specification"]
    assert "Test plan: run the CLI with no arguments" in p and "S08-FR-02" in p and "Work on: src/cli/*" in p


# --- S04-R-03 conflict review -------------------------------------------------------------------------

def conflicting_plan(category):
    def option(key, title):
        return {"key": key, "title": title, "role": "architect", "decision_topic": "database",
                "description": "Recommend a database.", "acceptance": [{"type": "contains", "value": "RECOMMENDATION"}]}

    return [option("a", "Compare relational databases"), option("b", "Compare embedded databases"),
            {"key": "build", "title": "Implement the specification", "role": "developer", "writes": True,
             "description": "Build it. Record it in WORKLOG.md.", "depends_on": ["a", "b"],
             "acceptance": [{"type": "file_exists", "path": "WORKLOG.md"}]}], category


def run_conflict(monkeypatch, category):
    tasks, category = conflicting_plan(category)
    with_plan(monkeypatch, tasks)
    real = executors.OfflineExecutor._task

    def task(self, req):
        title = req.data["title"]
        if title == "Compare relational databases":
            return "Analysis.\nRECOMMENDATION: PostgreSQL"
        if title == "Compare embedded databases":
            return "Analysis.\nRECOMMENDATION: SQLite"
        if title.startswith("Resolve the disagreement"):
            return f"DECISION: SQLite\nCONFIDENCE: 0.8\nCATEGORY: {category}\n\n{REVIEW}"
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = loop.create_goal("Pick a database", quality_commands=["test -f WORKLOG.md"])
    return gid, loop.run_goal(gid)


def test_different_recommendations_on_one_topic_get_one_review_and_one_decision(monkeypatch):
    gid, out = run_conflict(monkeypatch, "technical")
    assert out["status"] == "awaiting_approval"
    t = tasks_of(gid)
    reviews = [k for k in t if k.startswith("conflict-")]
    assert reviews == ["conflict-database"] and t["conflict-database"].status == "done"
    assert t["conflict-database"].depends_on == ["a", "b"]
    [d] = decisions.list_decisions(goal_id=gid)
    assert d["topic"] == "database" and d["recommendation"] == "SQLite" and d["confidence"] == 0.8
    cited = {e["id"] for e in d["evidence"] if e["type"] == "task"}
    assert cited == {t["a"].id, t["b"].id}
    assert sorted(d["alternatives"]) == ["PostgreSQL", "SQLite"] and not d["requires_human_approval"]
    loop.approve(gid)


def test_a_financial_conflict_waits_for_a_person(monkeypatch):
    gid, out = run_conflict(monkeypatch, "financial")
    assert out["status"] == "awaiting_approval"
    [d] = decisions.blocking(gid)
    assert d["category"] == "financial" and d["requires_human_approval"]


def test_a_restart_during_the_conflict_review_still_records_the_decision(monkeypatch):
    tasks, _ = conflicting_plan("technical")
    with_plan(monkeypatch, tasks)
    real = executors.OfflineExecutor._task
    reviews = []

    def task(self, req):
        title = req.data["title"]
        if title.startswith("Compare"):
            return f"RECOMMENDATION: {'PostgreSQL' if 'relational' in title else 'SQLite'}"
        if title.startswith("Resolve the disagreement"):
            reviews.append(title)
            if len(reviews) == 1:
                raise SystemExit("the process dies during the conflict review")
            return f"DECISION: SQLite\nCATEGORY: technical\n\n{REVIEW}"
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = loop.create_goal("Pick a database", quality_commands=["test -f WORKLOG.md"])
    try:
        loop.run_goal(gid)
    except SystemExit:
        pass
    with session() as s:
        s.get(Goal, gid).status = "queued"  # what GoalQueue.start does after a restart
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    assert len(reviews) == 2, "the review reran once, in the resumed iteration"
    [d] = decisions.list_decisions(goal_id=gid)
    assert d["recommendation"] == "SQLite" and len([e for e in d["evidence"] if e["type"] == "task"]) == 2


def test_agreeing_tasks_need_no_review(monkeypatch):
    tasks, _ = conflicting_plan("technical")
    with_plan(monkeypatch, tasks)
    real = executors.OfflineExecutor._task
    monkeypatch.setattr(executors.OfflineExecutor, "_task", lambda self, req: "RECOMMENDATION: sqlite."
                        if req.role == "architect" else real(self, req))
    gid = loop.create_goal("Agree", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    assert not [k for k in tasks_of(gid) if k.startswith("conflict-")]
    assert decisions.list_decisions(goal_id=gid) == []


# --- S04-R-06 re-plan and escalation ------------------------------------------------------------------

def test_an_invalid_plan_goes_back_to_the_planner_once(monkeypatch):
    real = executors.OfflineExecutor._plan
    prompts = []

    def plan(self, req):
        prompts.append(req.prompt)
        return '{"tasks": [{"key": "x"}]}' if len(prompts) == 1 else real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    gid = loop.create_goal("Replan", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    assert len(prompts) == 2 and "was not a valid plan" in prompts[1] and "title" in prompts[1]
    assert not any("using the standard plan" in e.message for e in events(gid, "plan"))


def test_a_check_failing_in_consecutive_iterations_is_escalated_to_the_planner(monkeypatch):
    feedback = []
    real = executors.OfflineExecutor._plan

    def plan(self, req):
        feedback.append(req.data.get("feedback", ""))
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    gid = loop.create_goal("Stubborn", quality_commands=["false"], max_iterations=3)
    assert loop.run_goal(gid)["status"] == "failed"
    assert "Escalation" not in feedback[1] and "Escalation: command false failed" in feedback[2]
    [esc] = events(gid, "escalation")
    assert esc.data["level"] == 2 and esc.data["checks"] == ["command false"]
    with session() as s:
        assert "command false" in s.get(Goal, gid).history[0]["failed_checks"]


def test_the_plan_instructions_name_the_new_fields():
    for name in ("out_of_scope", "risk_level", "test_plan", "budget", "deadline", "requirement_keys",
                 "decision_topic", "estimated_cost_usd"):
        assert name in loop.PLAN_INSTRUCTIONS
