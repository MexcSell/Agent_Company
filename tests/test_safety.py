"""Safety and correctness of the pipeline (docs/requirements, suggested order step 1): emergency stop levels,
durable recovery, retry policy, behaviour rules and incidents, collaboration from verified outcomes, stricter
playbook promotion, review with logic verification, change boundaries."""

import secrets
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import (checks, db, evolution, executors, governance, learning, loop, quality, registry, research,
                           router, worktrees)
from agent_company.api import create_app
from agent_company.db import Agent, Event, Goal, Incident, Notification, Relationship, Run, Task, session


def agent_of(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


def plan_request():
    return executors.Request(purpose="plan", role="general_manager", system="", prompt="p")


# --- S14-R-01 emergency stop levels ---------------------------------------------------------------

def test_e5_accepts_goals_but_runs_nothing_and_survives_a_restart():
    governance.set_emergency(True, "test", "drill")
    gid = loop.create_goal("Queued during the stop")
    assert loop.run_goal(gid) == {"status": "queued", "held": "emergency stop"}
    db.reset_engine()  # a restart: the stop is in the database
    assert governance.emergency()["reason"] == "drill"
    with session() as s:
        assert s.get(Goal, gid).status == "queued", "accepted, not run, not cancelled"
    governance.set_emergency(False, "test")
    assert loop.run_goal(gid)["status"] == "awaiting_approval"


def test_e5_terminates_a_running_executor_within_five_seconds(env):
    from agent_company.config import settings

    wt = settings().worktrees_dir / "kill"
    wt.mkdir(parents=True)
    ex = executors.Executor(router.Choice("x", "claude_code", "", "t"), timeout=60)
    req = executors.Request(purpose="task", role="developer", system="", prompt="", agent_id=1, goal_id=1)
    out = {}

    def run():
        out["proc"] = ex._spawn(["sleep", "30"], req, wt)

    t = threading.Thread(target=run)
    t.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with session() as s:
            if s.scalars(select(db.LiveProcess)).first():
                break
        time.sleep(0.05)
    started = time.monotonic()
    res = governance.set_emergency(True, "test", "kill drill")
    t.join(5)
    assert not t.is_alive() and time.monotonic() - started < 5
    assert res["terminated"] and out["proc"].returncode < 0 and "terminated by signal" in out["proc"].stderr
    with session() as s:
        assert s.scalars(select(db.LiveProcess)).first() is None, "the PID is untracked once it ended"


def test_e1_pauses_an_agent_without_recruiting_around_it_and_lift_restores_it():
    dev = agent_of("developer")
    with session() as s:
        ids = [a.id for a in s.scalars(select(Agent).where(Agent.role == "developer"))]
    for aid in ids:
        governance.stop("E1", aid, "test", "misbehaving")
    with session() as s:
        assert s.get(Agent, dev).lifecycle == "paused"
    a = registry.staff("general_manager")
    with session() as s:
        devagent = s.get(Agent, dev)
        s.expunge(devagent)
    res, _ = loop._execute(devagent, plan_request(), "plan")
    assert not res.ok and "E1 stop" in res.error
    with pytest.raises(RuntimeError, match="paused"):
        registry.staff("developer")  # no replacement is recruited around the stop
    registry.release(a.id)
    for aid in ids:
        assert governance.lift("E1", aid, "test")["restored_agents"] == [aid]
    with session() as s:
        assert s.get(Agent, dev).lifecycle == "active"
    assert not any(x["level"] == "E1" for x in governance.stops())


def test_e3_refuses_new_goals_of_a_project_and_cancels_its_running_ones():
    from agent_company import projects

    pid = projects.create("Stoppable")["id"]
    gid = loop.create_goal("Before", project_id=pid)
    out = governance.stop("E3", pid, "test", "freeze")
    assert gid in out["cancelled_goals"]
    with pytest.raises(ValueError, match="stopped"):
        loop.create_goal("After", project_id=pid)
    governance.lift("E3", pid, "test")
    assert loop.create_goal("After the lift", project_id=pid)


def test_e0_research_stops_the_rotation_and_e4_pauses_a_room():
    research.add_source("https://example.org", depth=0)
    governance.stop("E0", "research", "test", "crawler misbehaves")
    assert research.rotation_tick() is None
    with pytest.raises(ValueError):
        governance.stop("E0", "teleporter", "test", "no such executor")
    out = governance.stop("E4", "development", "test", "room freeze")
    assert out["paused_agents"]
    with session() as s:
        assert all(s.get(Agent, a).lifecycle == "paused" for a in out["paused_agents"])
    governance.lift("E4", "development", "test")
    with session() as s:
        assert all(s.get(Agent, a).lifecycle == "active" for a in out["paused_agents"])


def test_stop_cli_writes_the_database_directly(capsys):
    from agent_company.__main__ import main

    main(["stop", "--reason", "from the shell"])
    assert governance.emergency()["actor"].startswith("cli:")
    main(["stop", "--lift"])
    assert governance.emergency() is None


def test_stops_and_incidents_api_are_admin_only():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        dev = agent_of("developer")
        assert app.post("/api/stops", json={"level": "E1", "scope": str(dev), "reason": "x"}).status_code == 400
        r = app.post("/api/stops", json={"level": "E1", "scope": str(dev), "reason": "drill"}).json()
        assert r["paused_agents"] == [dev]
        assert app.get("/api/stops").json()["stops"][0]["scope"] == str(dev)
        assert app.post("/api/stops/lift", json={"level": "E1", "scope": str(dev)}).json()["restored_agents"] == [dev]
        inc = app.post("/api/incidents", json={"category": "prompt_injection", "severity": "SEV-3",
                                               "title": "Odd page", "agent_id": dev, "note": "seen in research"}).json()
        assert inc["new"] and inc["status"] == "open"
        assert app.post(f"/api/incidents/{inc['id']}", json={"status": "closed"}).status_code == 400
        assert app.get("/api/incidents").json()["items"][0]["id"] == inc["id"]
        member = TestClient(app.app)
        member.post("/api/auth/register", json={"name": "member", "device_secret": secrets.token_urlsafe(32)})
        assert member.get("/api/incidents").status_code in {401, 403}
        actions = {r["action"] for r in app.get("/api/audit").json()["records"]}
        assert {"emergency_stop", "emergency_lift", "incident_open"} <= actions


# --- S04-R-05 durable recovery -----------------------------------------------------------------------

def test_restart_after_a_finished_task_does_not_run_it_again(monkeypatch):
    calls = []
    real = executors.OfflineExecutor._task

    def task(self, req):
        calls.append(req.data.get("title"))
        if req.role == "qa" and len([c for c in calls if c == req.data.get("title")]) == 1:
            raise SystemExit("the process dies during the review")  # not an Exception: nothing catches it
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = loop.create_goal("Survive a restart", quality_commands=["test -f WORKLOG.md"])
    with pytest.raises(SystemExit):
        loop.run_goal(gid)
    with session() as s:
        assert s.get(Goal, gid).status == "running"
        done = {t.key for t in s.scalars(select(Task).where(Task.goal_id == gid)) if t.status == "done"}
        s.get(Goal, gid).status = "queued"  # what GoalQueue.start does after a restart
    assert done == {"spec", "build"}
    before = list(calls)
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval" and out["iterations"] == 1
    assert calls[len(before):] == ["Review the implementation"], "only the unfinished task ran again"
    with session() as s:
        assert len(s.scalars(select(Task).where(Task.goal_id == gid)).all()) == 3


def test_a_restart_drops_the_partial_edits_of_a_task_cut_off_mid_write(monkeypatch):
    """The worktree is reconciled with the last checkpoint commit: a finished writing task keeps its work, the
    edits of the one cut off are dropped before it runs again."""
    plan = executors.standard_plan("x")
    plan["tasks"].insert(2, {"key": "extra", "title": "Extend the worklog", "role": "developer", "writes": True,
                             "description": "Add a section to WORKLOG.md", "depends_on": ["build"],
                             "acceptance": [{"type": "file_exists", "path": "WORKLOG.md"}]})
    plan["tasks"][-1]["depends_on"] = ["extra"]
    monkeypatch.setattr(executors, "standard_plan", lambda goal: plan)
    calls, seen = [], {}
    real = executors.OfflineExecutor._task

    def task(self, req):
        calls.append(req.data.get("title"))
        if req.data.get("title") == "Extend the worklog":
            seen["partial_before_rerun"] = (req.workdir / "PARTIAL.txt").exists()
            if calls.count("Extend the worklog") == 1:
                (req.workdir / "PARTIAL.txt").write_text("half an edit\n")
                (req.workdir / "WORKLOG.md").write_text("clobbered mid-write\n")
                raise SystemExit("the process dies mid-write")
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = loop.create_goal("Reconcile", quality_commands=["test -f WORKLOG.md"])
    with pytest.raises(SystemExit):
        loop.run_goal(gid)
    with session() as s:
        g = s.get(Goal, gid)
        build = s.scalars(select(Task).where(Task.goal_id == gid, Task.key == "build")).one()
        assert build.status == "done" and build.gate["commit"] == g.checkpoint["commit"]
        assert build.gate["files"] == ["WORKLOG.md"], "the files the task changed, from its checkpoint commit"
        g.status = "queued"
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval"
    assert seen["partial_before_rerun"] is False, "the rerun starts from the checkpoint, not the partial edits"
    assert calls.count("Implement the specification") == 1, "the finished writing task is not run again"
    wt = loop._goal(gid).worktree
    assert "clobbered" not in (__import__("pathlib").Path(wt) / "WORKLOG.md").read_text()
    with session() as s:
        assert any("dropped unfinished edits: PARTIAL.txt, WORKLOG.md" in e.message
                   for e in s.scalars(select(Event).where(Event.goal_id == gid)))


def test_a_merge_is_not_repeated():
    gid = loop.create_goal("Merge once", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    first = loop.approve(gid)["merge_commit"]
    with session() as s:
        s.get(Goal, gid).status = "awaiting_approval"  # e.g. a crash after the merge, before the status was saved
    assert loop.approve(gid)["merge_commit"] == first
    with session() as s:
        assert sum(":merge already done" in e.message for e in s.scalars(select(Event))) == 1


# --- S04-R-06 retry policy ---------------------------------------------------------------------------

def test_transient_errors_are_retried_with_growing_delays(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls, delays = [], []

    class Flaky(executors.Executor):
        kind = "llm"

        def _run(self, req):
            calls.append(self.choice.name)
            if len(calls) <= 2:
                raise RuntimeError("429 rate limit exceeded")
            return executors.Result(text="ok")

    monkeypatch.setitem(executors.EXECUTORS, "llm", Flaky)
    monkeypatch.setattr(loop, "_sleep", delays.append)
    monkeypatch.setattr(loop.random, "uniform", lambda a, b: 0)
    router.set_route("general_manager", "Claude Sonnet 5.5")
    gm = registry.staff("general_manager")
    res, choice = loop._execute(gm, plan_request(), "plan")
    assert res.ok and calls == ["Claude Sonnet 5.5"] * 3 and delays == [2.0, 4.0]
    assert loop.backoff(10) == 60.0, "capped at retry_max_seconds"


def test_a_retry_that_would_wait_past_the_goal_deadline_is_not_made(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls, delays = [], []

    class Flaky(executors.Executor):
        kind = "llm"

        def _run(self, req):
            calls.append(self.choice.name)
            raise RuntimeError("503 service unavailable")

    monkeypatch.setitem(executors.EXECUTORS, "llm", Flaky)
    monkeypatch.setattr(loop, "_sleep", delays.append)
    monkeypatch.setattr(loop.random, "uniform", lambda a, b: 0)
    router.set_route("general_manager", "Claude Sonnet 5.5")
    gm = registry.staff("general_manager")
    gm.model_pref = "Claude Sonnet 5.5"
    res, _ = loop._execute(gm, plan_request(), "plan", deadline=time.monotonic() + 3)
    assert not res.ok and calls == ["Claude Sonnet 5.5"] * 2 and delays == [2.0], \
        "the 2 s retry fits before the deadline, the 4 s one does not"


def test_the_task_deadline_caps_the_run_timeout(monkeypatch):
    seen = []
    real = executors.for_choice
    monkeypatch.setattr(executors, "for_choice", lambda choice, timeout: (seen.append(timeout), real(choice, timeout))[1])
    gm = registry.staff("general_manager")
    loop._execute(gm, plan_request(), "plan", timeout=30)
    loop._execute(gm, plan_request(), "plan", timeout=10 ** 6)
    assert seen == [30, 600], "a task deadline shortens a run; it never extends the command timeout"


def test_a_command_timeout_of_0_is_no_limit(monkeypatch, tmp_path):
    from agent_company import prefs, sandbox

    prefs.set_value("command_timeout", 0, actor="user:admin")
    seen = []
    real = executors.for_choice
    monkeypatch.setattr(executors, "for_choice", lambda choice, timeout: (seen.append(timeout), real(choice, timeout))[1])
    gm = registry.staff("general_manager")
    loop._execute(gm, plan_request(), "plan", timeout=30)
    loop._execute(gm, plan_request(), "plan")
    assert seen == [30, 0], "a task deadline still applies; without one the run has no configured limit"
    off = executors.OfflineExecutor(router.Choice("Offline", "offline", "", "t"), 0)
    assert off.run(executors.Request(purpose="report", role="research", system="", prompt="p")).ok
    assert off.timeout == executors.TOOL_CONTRACTS["offline"].max_seconds, "only the executor's own cap is left"
    res = sandbox.run(["/bin/sh", "-c", "sleep 0.2; echo ok"], tmp_path, 0, mode="off")
    assert res.returncode == 0 and not res.timed_out and "ok" in res.output


def test_policy_refusals_are_not_retried(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls = []

    class Refused(executors.Executor):
        kind = "llm"

        def _run(self, req):
            calls.append(self.choice.name)
            return executors.Result(text="", ok=False, error="`sudo x` refused by the command policy: privilege escalation")

    monkeypatch.setitem(executors.EXECUTORS, "llm", Refused)
    router.set_route("general_manager", "Claude Opus 5.5")
    gm = registry.staff("general_manager")
    gm.model_pref = "Claude Sonnet 5.5"
    res, _ = loop._execute(gm, plan_request(), "plan")
    assert not res.ok and calls == ["Claude Sonnet 5.5"], "no retry and no other model"
    assert not loop.transient("503 but refused by the command policy") and loop.transient("HTTP 503")


# --- S14-R-03 behaviour rules, S14-R-04 incidents ----------------------------------------------------

def test_three_refused_commands_pause_the_agent_and_raise_one_alert():
    gid = loop.create_goal("Refusals")
    gm = agent_of("general_manager")
    for _ in range(4):
        governance.on_command_refused(gid, gm, "sudo rm -rf /", "privilege escalation")
    with session() as s:
        assert s.get(Agent, gm).lifecycle == "paused"
        alerts = s.scalars(select(Notification).where(Notification.source == "security")).all()
        assert len(alerts) == 1 and alerts[0].admin_only
    assert any(x["level"] == "E1" and x["scope"] == str(gm) for x in governance.stops())


def test_refused_acceptance_commands_reach_the_rule():
    from agent_company import contracts

    heard = []
    c = contracts.TaskContract.model_validate({"key": "k", "title": "t", "role": "developer", "description": "d",
                                              "acceptance": [{"type": "command", "run": "sudo true"}]})
    from agent_company.config import settings

    wt = settings().worktrees_dir / "r"
    wt.mkdir(parents=True)
    out = contracts.check(c, "", wt, on_refused=lambda cmd, why: heard.append((cmd, why)))
    assert not out[0]["passed"] and heard == [("sudo true", "privilege escalation")]


def test_a_secret_in_the_change_stops_the_goal_and_opens_an_incident(monkeypatch):
    real = executors.OfflineExecutor._task

    def leak(self, req):
        if req.writes:
            return ("Done.\n=== FILE: settings.py ===\nKEY = 'AKIAZ3MXQ7RTV2KD9WPL'\n=== END FILE ===\n"
                    "=== FILE: WORKLOG.md ===\nx\n=== END FILE ===\n")
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", leak)
    gid = loop.create_goal("Leaky", max_iterations=3)
    out = loop.run_goal(gid)
    assert out["status"] == "failed" and out["iterations"] == 1 and "credentials" in out["stopped"]
    inc = governance.list_incidents()[0]
    assert inc["category"] == "credential_compromise" and inc["severity"] == "SEV-2" and inc["goal_id"] == gid
    assert inc["evidence"], "evidence is frozen when the incident opens"


def test_incident_moves_forward_and_closes_with_a_root_cause():
    dev = agent_of("developer")
    inc = governance.open_incident("agent_compromise", "SEV-2", "Odd behaviour", "test", agent_id=dev)
    out = governance.update_incident(inc["id"], "test", contain="pause_agent", note="paused while we look")
    assert out["status"] == "contained"
    with session() as s:
        assert s.get(Agent, dev).lifecycle == "paused"
    with pytest.raises(ValueError, match="forward"):
        governance.update_incident(inc["id"], "test", status="open")
    with pytest.raises(ValueError, match="root cause"):
        governance.update_incident(inc["id"], "test", status="closed", note="done")
    out = governance.update_incident(inc["id"], "test", status="closed", root_cause="prompt injection in a page",
                                     note="page blocked")
    assert out["status"] == "closed" and [a["action"] for a in out["actions"]] == ["pause_agent", "status closed"]
    with session() as s:
        assert any("Security incident" in x.text for x in s.scalars(select(db.Lesson).where(db.Lesson.agent_id == dev)))


def test_an_agent_failing_most_of_its_last_ten_tasks_is_flagged_once():
    dev = agent_of("developer")
    for i in range(10):
        learning.learn(dev, None, f"failure {i}", False, [])
    flags = [e for e in evolution.overview()["events"] if e["stage"] == "flag"]
    assert len(flags) == 1 and flags[0]["agent_id"] == dev


# --- S12-D-01 collaboration from verified outcomes ---------------------------------------------------

def weight(a, b):
    lo, hi = sorted((a, b))
    with session() as s:
        r = s.scalars(select(Relationship).where(Relationship.a_id == lo, Relationship.b_id == hi,
                                                 Relationship.kind == "collaborated")).first()
        return None if r is None else round(r.weight, 3)


def test_collaboration_counts_verified_outcomes_once_per_goal():
    a, b, c = agent_of("architect"), agent_of("developer"), agent_of("qa")
    learning.link_team(1, [(a, "done"), (b, "failed")], passed=False)
    assert weight(a, b) is None, "a failure never creates a link"
    learning.link_team(1, [(a, "done"), (b, "done")], passed=True)
    assert weight(a, b) == 0.4
    learning.link_team(1, [(a, "done"), (b, "done")], passed=True)
    assert weight(a, b) == 0.4, "the same goal does not count twice"
    learning.link_team(2, [(a, "done"), (b, "failed"), (c, "done")], passed=False)
    assert weight(a, b) == 0.35 and weight(a, c) is None, "only pairs with a failed task of their own lose"


# --- S13-D-01 stricter promotion ---------------------------------------------------------------------

@pytest.mark.parametrize("baseline,outcomes,promoted", [
    (2 / 3, [True, True, False], False),  # a tie keeps the current version
    (0.6, [True, True, False], False),    # 67 % vs 60 % is less than min_improvement (10 points)
    (0.2, [True, True, False], True),
])
def test_a_trial_playbook_needs_a_clear_improvement(baseline, outcomes, promoted):
    dev = agent_of("developer")
    with session() as s:
        a = s.get(Agent, dev)
        a.playbook, a.experiment = "OLD", {"playbook": "NEW", "baseline": baseline, "outcomes": []}
    for ok in outcomes:
        evolution.after_task(dev, ok)
    with session() as s:
        assert s.get(Agent, dev).playbook == ("NEW" if promoted else "OLD")


# --- S08-R-03 review with logic verification ---------------------------------------------------------

def test_review_needs_logic_verification_and_an_approving_outcome():
    assert quality.review_verdict("Looks clean.\nVERDICT: PASS")[0] is False
    good = "## Logic verification\n- normal: ok\n- edge: ok\n- error: ok\nOUTCOME: APPROVED_WITH_COMMENTS\nVERDICT: PASS"
    assert quality.review_verdict(good) == (True, "APPROVED_WITH_COMMENTS")
    assert quality.review_verdict(good.replace("APPROVED_WITH_COMMENTS", "REVISION_REQUIRED"))[0] is False
    assert quality.review_verdict(good + "\nVERDICT: FAIL")[0] is False


def test_duplicate_findings_are_merged():
    out = ("FINDING: a.py:3 | crashes on empty input | guard with `if not items: return []`\n"
           "- FINDING: b.py:9 | Crashes on  empty input | guard with `if not items: return []`\n"
           "FINDING: c.py:1 | leaks the file handle | use `with open(...)`\n")
    assert quality.merge_findings(out) == [
        "a.py:3, b.py:9 | crashes on empty input | guard with `if not items: return []`",
        "c.py:1 | leaks the file handle | use `with open(...)`"]


def test_a_bare_pass_review_fails_the_gate(monkeypatch):
    real = executors.OfflineExecutor._task
    monkeypatch.setattr(executors.OfflineExecutor, "_task",
                        lambda self, req: "Looks fine.\nVERDICT: PASS" if req.role == "qa" else real(self, req))
    gid = loop.create_goal("Rubber stamp", max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] == "failed"
    review = next(c for c in out["report"]["checks"] if c["check"] == "review review")
    assert "logic-verification" in review["detail"]


# --- S08-R-04 change boundaries ----------------------------------------------------------------------

def repo_with_test(env):
    repo = env / "bound"
    (repo / "tests").mkdir(parents=True)
    worktrees.git(repo, "init", "-q", "-b", "main")
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    assert 1 == 1\n    assert 2 == 2\n")
    (repo / "package.json").write_text("{}\n")
    worktrees.git(repo, "add", ".")
    worktrees.git(repo, "commit", "-q", "-m", "init")
    wt, _, base = worktrees.create(repo, "bound")
    return wt, base


def test_skipping_a_test_or_removing_assertions_fails_the_boundaries(env):
    wt, base = repo_with_test(env)
    (wt / "tests" / "test_x.py").write_text("import pytest\n\n@pytest.mark.skip\ndef test_x():\n    assert 1 == 1\n")
    worktrees.commit_all(wt, "skip it")
    problems = checks.boundary_violations(wt, base)
    assert any("skips a test" in p for p in problems) and any("1 assertion(s) removed" in p for p in problems)
    assert checks.boundary_violations(wt, base, ["skip_tests"]) == []
    row = next(c for c in checks.run_static(wt, base, [], False) if c["check"] == "boundaries")
    assert not row["passed"]


def test_new_dependencies_and_ci_edits_need_a_permit(env):
    wt, base = repo_with_test(env)
    (wt / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}\n')
    (wt / ".github" / "workflows").mkdir(parents=True)
    (wt / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    worktrees.commit_all(wt, "deps and ci")
    problems = checks.boundary_violations(wt, base)
    assert len(problems) == 2 and "dependencies" in problems[0] and "config" in problems[1]
    assert checks.boundary_violations(wt, base, ["dependencies", "config"]) == []
    with pytest.raises(ValueError, match="unknown permit"):
        loop.create_goal("x", permits=["everything"])
    with session() as s:
        assert s.get(Goal, loop.create_goal("y", permits=["config"])).permits == ["config"]


def test_runs_are_tracked_with_agent_and_goal():
    gid = loop.create_goal("Trace me", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    with session() as s:
        runs = s.scalars(select(Run).where(Run.goal_id == gid)).all()
        assert runs and all(r.agent_id for r in runs)
        assert not s.scalars(select(Incident)).first()
