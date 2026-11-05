"""SPEC-03 recruitment: capability gaps (S03-R-01), recruitment records with job descriptions (S03-R-03),
probation (S03-R-04) and the approval policy (S03-R-05)."""

import secrets

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agent_company import config, executors, loop, projects, recruitment, registry
from agent_company.db import Agent, AuditEvent, Goal, Notification, Recruitment, Task, session

RUST_BUILD = {"key": "build", "title": "Port the parser to Rust", "role": "rust_expert", "writes": True,
              "description": "Port it. Record what you did in WORKLOG.md.", "skills": ["rust", "embedded"],
              "acceptance": [{"type": "file_exists", "path": "WORKLOG.md"}]}


def with_plan(monkeypatch, tasks):
    monkeypatch.setattr(executors, "standard_plan", lambda goal: {"summary": "test plan", "tasks": tasks})


def rust_plan(review=True):
    plan = executors.standard_plan("x")["tasks"]
    plan[1] = dict(RUST_BUILD, depends_on=["spec"])
    return plan if review else plan[:2]


def policy(value):
    config.set_overrides({"recruit_requires_approval": value})


def records():
    return recruitment.list_recruitments()


def count_agents(role):
    with session() as s:
        return s.scalar(select(func.count()).select_from(Agent).where(Agent.role == role))


# --- S03-R-03 record and job description -------------------------------------------------------

def test_a_recruit_gets_a_record_and_its_description_from_the_job_description(monkeypatch):
    with_plan(monkeypatch, rust_plan())
    gid = loop.create_goal("Port the parser", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    [rec] = records()
    assert rec["decision"] == "recruit" and rec["status"] == "approved" and rec["decided_by"] == "policy"
    assert rec["lifecycle"] == "permanent" and rec["template"] == "developer" and rec["goal_id"] == gid
    jd = rec["job_description"]
    assert "Port the parser to Rust" in jd["summary"] and jd["expertise"] == ["rust", "embedded"]
    assert jd["reporting_manager"]["name"] and jd["lifecycle"] == "permanent" and jd["evaluation_criteria"]
    assert set(rec["required_skills"]) == {"rust", "embedded"}, "nobody holds rust: both are gaps"
    assert rec["gap"]["rust"] == {"required": 1.0, "available": 0.0, "gap": 1.0}
    with session() as s:
        a = s.get(Agent, rec["agent_id"])
        assert a.goal == jd["summary"] and "Port the parser to Rust" in a.backstory
        # S03-R-05: no more than the template, and shell for a role outside the catalog waits for a person
        assert a.permissions == ["read", "write"] and rec["pending_permissions"] == ["shell"]
        build = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == "build")).first()
        assert "recruitment #" in build.contract["staffing"]["why"]
        assert build.contract["staffing"]["gap"]["rust"]["gap"] == 1.0
        assert s.scalars(select(Notification).where(Notification.title.like("%needs shell%"))).first()


def test_reuse_of_a_covering_agent_is_recorded_too():
    a = registry.staff("data_wrangler", ["python", "database"], writes=True, goal_id=None)
    assert a.role == "developer"
    [rec] = records()
    assert rec["decision"] == "reuse" and rec["agent_id"] is None and "covers 100%" in rec["reason"]
    assert rec["preferred_skills"] == ["python", "database"] and rec["required_skills"] == [], "nothing is missing"


def test_one_record_per_goal_and_role(monkeypatch):
    gid = loop.create_goal("Two rust tasks", quality_commands=[])
    first = registry.staff("rust_expert", ["rust"], gid, writes=True)
    registry.release(first.id, gid)
    second = registry.staff("rust_expert", ["rust"], gid, writes=True)
    assert second.id == first.id and len(records()) == 1


def test_a_pending_job_description_is_versioned():
    policy("always")
    gid = loop.create_goal("Needs rust", quality_commands=[])
    try:
        registry.staff("rust_expert", ["rust"], gid, writes=True)
    except recruitment.RecruitmentPending:
        pass
    [rec] = records()
    out = recruitment.revise(rec["id"], {"summary": "Port the parser and its tests"}, "user:admin")
    assert out["jd_version"] == 2 and out["history"][0]["version"] == 1
    assert out["job_description"]["summary"] == "Port the parser and its tests"
    recruitment.approve(rec["id"], "user:admin")
    with session() as s:
        assert s.get(Agent, recruitment.get(rec["id"])["agent_id"]).goal == "Port the parser and its tests"


# --- S03-R-05 approval policy ------------------------------------------------------------------

def test_permanent_only_pauses_the_goal_until_approved_and_approval_is_idempotent(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    policy("permanent_only")
    with_plan(monkeypatch, rust_plan())
    p = projects.create("Rusty")
    gid = loop.create_goal("Port the parser", quality_commands=["test -f WORKLOG.md"], project_id=p["id"])
    out = loop.run_goal(gid)
    assert out["status"] == "paused" and "waits for an administrator" in out["recruitment"]
    g = loop._goal(gid)
    assert g.checkpoint["paused_for"] == "recruitment" and count_agents("rust_expert") == 0
    [rec] = records()
    assert rec["status"] == "pending"
    with session() as s:
        assert s.scalars(select(Notification).where(Notification.source == "recruitment",
                                                    Notification.admin_only)).first()
    from agent_company import management

    management.sync(p["id"])
    assert any("recruitment" in b["title"] for b in management.list_risks(p["id"]))

    approved = recruitment.approve(rec["id"], "user:admin")
    assert approved["status"] == "approved" and approved["agent_id"] and approved["resumed"]
    again = recruitment.approve(rec["id"], "user:admin")  # AT-020: no second agent
    assert again["agent_id"] == approved["agent_id"] and count_agents("rust_expert") == 1
    assert loop._goal(gid).status == "queued"
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    with session() as s:
        build = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == "build")).first()
        assert build.agent_id == approved["agent_id"] and build.status == "done"
        assert s.get(Agent, approved["agent_id"]).permissions == ["read", "write", "shell"], "approval granted shell"


def test_never_lets_a_covering_clone_through_and_always_holds_it(monkeypatch):
    policy("permanent_only")
    dev = registry.staff("developer", ["python"])  # the only developer is busy: covering it means a clone
    c = registry.staff("data_wrangler", ["python", "database"], writes=True, goal_id=None)
    assert c.parent_id == dev.id, "a clone is temporary: permanent_only does not hold it"
    policy("always")
    gid = loop.create_goal("Wrangle", quality_commands=[])
    try:
        registry.staff("data_wrangler", ["python", "database"], gid, writes=True)
        raise AssertionError("always holds a clone for a role nobody holds")
    except recruitment.RecruitmentPending:
        pass
    assert recruitment.list_recruitments(goal_id=gid)[0]["decision"] == "clone"


def test_a_rejected_recruitment_resumes_the_goal_and_the_task_fails_with_the_refusal(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    policy("always")
    with_plan(monkeypatch, rust_plan())
    gid = loop.create_goal("Port the parser", quality_commands=["test -f WORKLOG.md"], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "paused"
    [rec] = records()
    out = recruitment.reject(rec["id"], "user:admin", "use the developers")
    assert out["status"] == "rejected" and out["resumed"]
    assert loop.run_goal(gid)["status"] == "failed"
    with session() as s:
        build = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == "build")).first()
        assert build.status == "failed" and "rejected by user:admin: use the developers" in build.output
    assert count_agents("rust_expert") == 0
    try:
        recruitment.approve(rec["id"], "user:admin")
        raise AssertionError("a rejected recruitment cannot be approved")
    except ValueError:
        pass


def test_a_benchmark_goal_stops_instead_of_waiting_for_a_recruitment(monkeypatch):
    policy("always")
    with_plan(monkeypatch, rust_plan())
    gid = loop.create_goal("Port the parser", quality_commands=[])
    with session() as s:
        s.get(Goal, gid).origin = "eval"
    assert loop.run_goal(gid)["status"] == "failed"


def test_only_administrators_decide_and_a_refused_attempt_is_audited():
    from agent_company.api import create_app

    policy("always")
    gid = loop.create_goal("Needs rust", quality_commands=[])
    try:
        registry.staff("rust_expert", ["rust"], gid, writes=True)
    except recruitment.RecruitmentPending:
        pass
    rid = records()[0]["id"]
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        other = TestClient(app.app)
        s2 = secrets.token_urlsafe(32)
        other.post("/api/auth/register", json={"name": "member", "device_secret": s2})
        uid = next(u["id"] for u in app.get("/api/users").json() if u["name"] == "member")
        app.post(f"/api/users/{uid}/approve")
        other.post("/api/auth/device", json={"device_secret": s2})
        assert other.get("/api/recruitments").status_code == 200
        assert other.post(f"/api/recruitments/{rid}/approve", json={}).status_code == 403
        with session() as s:
            denied = s.scalars(select(AuditEvent).where(AuditEvent.action == "recruitment_approve_denied")).first()
            assert denied is not None and denied.actor == "user:member"
        assert recruitment.get(rid)["status"] == "pending"
        out = app.post(f"/api/recruitments/{rid}/approve", json={"grant": False}).json()
        assert out["status"] == "approved" and out["pending_permissions"] == ["shell"]
        assert app.post(f"/api/recruitments/{rid}/reject", json={}).status_code == 400
        assert app.get("/api/recruitments/9999").status_code == 404
        assert app.get("/api/recruitment/effectiveness").json()["recruits"][0]["state"] == "on probation"
        assert app.get("/api/capability_gap", params={"skills": "rust,python"}).json()["rust"]["available"] > 0


# --- S03-R-04 probation ------------------------------------------------------------------------

def test_a_probationers_change_gets_a_review_when_the_plan_has_none(monkeypatch):
    with_plan(monkeypatch, rust_plan(review=False))
    gid = loop.create_goal("Port the parser", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval"
    with session() as s:
        tasks = {t.key: t for t in s.scalars(select(Task).where(Task.goal_id == gid))}
        review = tasks["probation-review"]
        assert review.role == "qa" and review.depends_on == ["build"] and review.status == "done"
        assert not recruitment.on_probation(s, review.agent_id)
        recruit = s.get(Agent, tasks["build"].agent_id)
        assert recruit.probation_tasks_left == recruitment.PROBATION_TASKS - 1, "a verified pass counts down"
    checks = {c["check"]: c for c in out["report"]["checks"]}
    assert checks["probation review"]["passed"]


def test_the_gate_fails_a_probationers_change_without_a_passing_review(monkeypatch):
    with_plan(monkeypatch, rust_plan(review=False))
    monkeypatch.setattr(loop, "_probation_reviews", lambda gid, it: [])  # the gate is the safety net
    gid = loop.create_goal("Unreviewed", quality_commands=["test -f WORKLOG.md"], max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] == "failed"
    check = next(c for c in out["report"]["checks"] if c["check"] == "probation review")
    assert not check["passed"] and "need a passing QA review" in out["report"]["feedback"]


def _task(agent_id, status):
    gid = loop.create_goal("Probation", quality_commands=[])
    with session() as s:
        t = Task(goal_id=gid, iteration=1, key="k", title="k", role="rust_expert", agent_id=agent_id, status=status)
        s.add(t)
        s.flush()
        return t.id


def test_probation_clears_after_three_passes_and_retires_after_two_own_failures():
    a = registry.staff("rust_expert", ["rust"], writes=True)
    registry.release(a.id)
    for _ in range(recruitment.PROBATION_TASKS - 1):
        assert recruitment.after_task(a.id, _task(a.id, "done"), True) == "counted"
    assert recruitment.after_task(a.id, _task(a.id, "done"), True) == "passed"
    with session() as s:
        assert not recruitment.on_probation(s, a.id)

    b = registry.staff("go_expert", ["golang"], writes=True)
    registry.release(b.id)
    clone = registry.staff("go_expert", ["golang"], writes=True)
    clone2 = registry.staff("go_expert", ["golang"], writes=True)
    assert clone.id == b.id and clone2.parent_id == b.id
    with session() as s:
        assert recruitment.on_probation(s, clone2.id), "a clone of a probationer is on probation too"
    assert recruitment.after_task(b.id, _task(b.id, "done"), False) == "", "another task failed the gate"
    assert recruitment.after_task(b.id, _task(b.id, "failed"), False) == "failed"
    assert recruitment.after_task(clone2.id, _task(clone2.id, "failed"), False) == "retired"
    with session() as s:
        retired = s.get(Agent, b.id)
        assert retired.lifecycle == "retired" and not retired.enabled
    eff = recruitment.effectiveness()
    assert eff["finished"] == 2 and eff["passed"] == 1 and eff["pass_rate"] == 0.5


# --- S03-R-01 capability gap -------------------------------------------------------------------

def test_capability_gap_weighs_proficiency_availability_and_verification():
    gap = recruitment.capability_gap({"python": 2.0, "cobol": 1.0})
    dev = next(x for x in gap["python"]["agents"] if x["v"] == 1.0)
    assert dev["c"] == 1.0 and gap["cobol"] == {"required": 1.0, "available": 0.0, "gap": 1.0, "agents": []}
    before = gap["python"]["available"]
    busy = registry.staff("developer", ["python"])
    after = recruitment.capability_gap({"python": 2.0})["python"]
    assert after["available"] < before, "a busy agent is not available"
    registry.release(busy.id)

    a = registry.staff("rust_expert", ["rust"], writes=True)
    registry.release(a.id)
    rust = recruitment.capability_gap({"rust": 1.0})["rust"]
    assert rust["agents"][0]["v"] == recruitment.SELF_ASSIGNED and rust["available"] == 0.5
    assert rust["gap"] == 0.5, "a recruit's self-assigned level counts half until certified"


def test_a_project_gap_counts_the_open_tasks_needing_each_skill():
    p = projects.create("Gaps")
    projects.update(p["id"], {"required_skills": ["postgres"]})
    gid = loop.create_goal("Tune it", quality_commands=[], project_id=p["id"])
    with session() as s:
        for i in range(3):
            s.add(Task(goal_id=gid, iteration=1, key=f"t{i}", title="t", role="developer", status="queued",
                       contract={"skills": ["postgres", "python"]}))
    out = recruitment.project_gap(p["id"])
    rows = {r["skill"]: r for r in out["skills"]}
    assert rows["postgres"]["required"] == 3 and rows["postgres"]["gap"] == 3 and "postgres" in out["missing"]
    assert rows["python"]["required"] == 3


# --- follow-ups: a failed review counts, a retired recruit is not replaced silently, an early decision -----

def test_a_failed_review_of_a_finished_task_counts_as_the_probationers_failure():
    gid = loop.create_goal("Reviewed", quality_commands=[])
    a = registry.staff("rust_expert", ["rust"], gid, writes=True)
    registry.release(a.id, gid)
    with session() as s:
        build = Task(goal_id=gid, iteration=1, key="build", title="b", role="rust_expert", agent_id=a.id,
                     status="done", writes=True)
        s.add(build)
        s.add(Task(goal_id=gid, iteration=1, key="review", title="r", role="qa", status="done", depends_on=["build"]))
        s.flush()
        build_id = build.id
        s.get(Goal, gid).history = [{"iteration": 1, "failed_checks": ["command x"]}]
    assert recruitment.after_task(a.id, build_id, False) == "", "another check failed: not the recruit's fault"
    with session() as s:
        s.get(Goal, gid).history = [{"iteration": 1, "failed_checks": ["review review"]}]
    assert recruitment.after_task(a.id, build_id, False) == "failed", "its reviewer rejected the work"


def test_a_retired_recruit_is_not_replaced_under_the_old_approval():
    gid = loop.create_goal("Needs rust", quality_commands=[])
    first = registry.staff("rust_expert", ["rust"], gid, writes=True)
    registry.release(first.id, gid)
    registry.set_lifecycle(first.id, "retired", "user:admin")
    policy("permanent_only")
    try:
        registry.staff("rust_expert", ["rust"], gid, writes=True)
        raise AssertionError("a replacement goes through the policy again")
    except recruitment.RecruitmentPending:
        pass
    recs = recruitment.list_recruitments(goal_id=gid)
    assert [r["status"] for r in recs] == ["pending", "approved"] and count_agents("rust_expert") == 1


def test_a_decision_before_the_goal_paused_requeues_it(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    policy("always")
    gid = loop.create_goal("Needs rust", quality_commands=[])
    try:
        registry.staff("rust_expert", ["rust"], gid, writes=True)
    except recruitment.RecruitmentPending:
        pass
    rid = records()[0]["id"]
    assert recruitment.approve(rid, "user:admin")["resumed"] is False, "the goal has not paused yet"
    out = loop._out_for_recruitment(gid, "recruitment waits", {})
    assert out["status"] == "queued" and loop._goal(gid).status == "queued"


# --- S03-R-04 qualification run ------------------------------------------------------------------

def qualify_on(monkeypatch, rate=1.0, cost=0.12):
    """`qualify_recruits` on, with evaluation.run_suite answering `rate`; returns the calls it got."""
    from agent_company import evaluation

    config.set_overrides({"qualify_recruits": True})
    calls = []

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        calls.append({"suite": suite, "label": label, "repeats": repeats, "trial": trial})
        return {"label": label, "runs": 3, "passed": round(3 * rate), "pass_rate": rate, "total_cost": cost}

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    return calls


def paused_for_qualification(monkeypatch):
    with_plan(monkeypatch, rust_plan())
    gid = loop.create_goal("Port the parser", quality_commands=["test -f WORKLOG.md"], max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] == "paused" and "qualification run" in out["recruitment"]
    [rec] = records()
    assert rec["status"] == "qualifying" and rec["qualification"] == {"suite": "smoke", "status": "queued"}
    assert count_agents("rust_expert") == 0, "the candidate is registered by the run, outside the staffing lock"
    return gid, rec


def test_a_candidate_that_fails_its_qualification_is_rejected_and_retired(monkeypatch):
    calls = qualify_on(monkeypatch, rate=1 / 3)
    gid, rec = paused_for_qualification(monkeypatch)
    out = recruitment.qualify(rec["id"])  # AT-008
    assert out["status"] == "rejected" and out["decided_by"] == "qualification" and out["resumed"]
    assert out["qualification"]["status"] == "failed" and out["evaluation_cost_usd"] == 0.12
    assert "33% of the smoke suite, needs 67%" in out["note"]
    assert calls[0]["trial"] == {"trainee_id": out["agent_id"], "qualify": rec["id"]} and calls[0]["repeats"] == 1
    with session() as s:
        a = s.get(Agent, out["agent_id"])
        assert a.lifecycle == "retired" and not a.enabled
    assert loop.run_goal(gid)["status"] == "failed"
    with session() as s:
        build = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == "build")).first()
        assert build.status == "failed" and "rejected by qualification" in build.output
    assert len(records()) == 1 and count_agents("rust_expert") == 1, "a failed candidate is not recruited again"
    eff = recruitment.effectiveness()
    assert eff["recruits"][0]["state"] == "not qualified" and eff["qualified"] == 0 and eff["qualification_runs"] == 1


def test_a_candidate_that_passes_becomes_an_active_recruit_on_probation(monkeypatch):
    qualify_on(monkeypatch, rate=1.0)
    gid, rec = paused_for_qualification(monkeypatch)
    out = recruitment.qualify(rec["id"])  # AT-009, AT-010
    assert out["status"] == "approved" and out["decided_by"] == "policy" and out["resumed"]
    assert out["qualification"]["status"] == "passed" and out["evaluation_cost_usd"] == 0.12
    with session() as s:
        a = s.get(Agent, out["agent_id"])
        assert a.lifecycle == "active" and a.probation_tasks_left == recruitment.PROBATION_TASKS
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    with session() as s:
        build = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == "build")).first()
        assert build.agent_id == out["agent_id"] and build.status == "done"
    assert recruitment.effectiveness()["qualified"] == 1


def test_a_qualified_candidate_still_waits_for_the_approval_policy(monkeypatch):
    qualify_on(monkeypatch, rate=1.0)
    config.set_overrides({"qualify_recruits": True, "recruit_requires_approval": "permanent_only"})
    gid, rec = paused_for_qualification(monkeypatch)
    try:
        recruitment.approve(rec["id"], "user:admin")
        raise AssertionError("an administrator decides after the qualification run")
    except ValueError as e:
        assert "qualification run" in str(e)
    out = recruitment.qualify(rec["id"])
    assert out["status"] == "pending" and not out["resumed"]
    with session() as s:
        assert s.get(Agent, out["agent_id"]).lifecycle == "candidate"
        assert s.scalars(select(Notification).where(Notification.title.like(f"Recruitment #{rec['id']} waits%"))).first()
    assert loop._goal(gid).status == "paused"
    approved = recruitment.approve(rec["id"], "user:admin")
    assert approved["resumed"] and approved["agent_id"] == out["agent_id"] and count_agents("rust_expert") == 1
    with session() as s:
        assert s.get(Agent, out["agent_id"]).lifecycle == "active"


def test_a_candidate_is_never_staffed_for_users_work(monkeypatch):
    qualify_on(monkeypatch)
    gid = loop.create_goal("Needs rust", quality_commands=[])
    try:
        registry.staff("rust_expert", ["rust"], gid, writes=True)
        raise AssertionError("the candidate qualifies first")
    except recruitment.RecruitmentPending:
        pass
    rid = records()[0]["id"]
    monkeypatch.setattr(recruitment, "_finish", lambda rid, summary, error="": {})  # stop inside the run
    recruitment.qualify(rid)
    cand = recruitment.get(rid)["agent_id"]
    assert recruitment.capability_gap({"rust": 1.0})["rust"]["available"] == 0, "a candidate is not available"
    other = loop.create_goal("Also rust", quality_commands=[])
    try:  # another goal opens its own request; it never gets the candidate (AT-007)
        registry.staff("rust_expert", ["rust"], other, writes=True)
        raise AssertionError("the other goal waits for its own candidate")
    except recruitment.RecruitmentPending:
        pass
    try:
        registry.set_lifecycle(cand, "active", "user:admin")
        raise AssertionError("only the qualification run activates a candidate")
    except ValueError:
        pass
    out = recruitment.reject(rid, "user:admin", "not needed", status="cancelled")
    assert out["status"] == "cancelled"
    with session() as s:
        assert s.get(Agent, cand).lifecycle == "retired"


def test_the_qualification_run_gives_the_candidate_the_writing_tasks_of_the_suite(monkeypatch, tmp_path):
    import json

    config.set_overrides({"qualify_recruits": True})
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    suite = tmp_path / "mini.json"
    suite.write_text(json.dumps({"name": "mini", "tasks": [
        {"name": "worklog", "title": "Write a worklog", "checks": ["test -f WORKLOG.md"], "max_iterations": 2}]}))
    monkeypatch.setattr(recruitment, "qualification_suite", lambda skills: str(suite))
    gid = loop.create_goal("Needs rust", quality_commands=[])
    try:
        registry.staff("rust_expert", ["rust"], gid, writes=True)
    except recruitment.RecruitmentPending:
        pass
    out = recruitment.qualify(records()[0]["id"])  # the real eval harness, offline
    cand = out["agent_id"]
    assert out["qualification"]["status"] == "passed" and out["status"] == "approved"
    with session() as s:
        evals = list(s.scalars(select(Goal.id).where(Goal.origin == "eval")))
        tasks = list(s.scalars(select(Task).where(Task.goal_id.in_(evals))))
        writers = [t for t in tasks if t.writes and t.agent_id]
        assert writers and all(t.agent_id == cand for t in writers), "the candidate does the developer's work"
        reviews = [t for t in tasks if t.role == "qa" and t.agent_id]
        assert reviews and all(t.agent_id != cand for t in reviews), "never its own reviewer"
        assert s.get(Agent, cand).probation_tasks_left == recruitment.PROBATION_TASKS, "an exercise is not probation"


def test_qualification_suites_name_a_skill_or_fall_back_to_smoke():
    assert recruitment.qualification_suite(["python"]) == "python-practice"
    assert recruitment.qualification_suite(["rust"]) == "smoke"


def test_no_qualification_for_a_reading_role_or_when_off(monkeypatch):
    gid = loop.create_goal("Needs rust", quality_commands=[])
    registry.staff("rust_expert", ["rust"], gid, writes=True)
    assert records()[0]["qualification"] == {}, "off by default"
    qualify_on(monkeypatch)
    other = loop.create_goal("Needs a historian", quality_commands=[])
    a = registry.staff("historian", ["history"], other, writes=False)
    rec = recruitment.list_recruitments(goal_id=other)[0]
    assert rec["status"] == "approved" and rec["qualification"]["status"] == "skipped" and a.lifecycle == "active"
