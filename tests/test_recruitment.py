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
