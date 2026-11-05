"""Risk-based test strategy per goal (ASEAO09 S09-R-02, AT-001)."""

import pytest
from pydantic import ValidationError

from agent_company import contracts, executors, loop, performance, projects, strategies, teststrategy
from agent_company.db import Goal, Task, session


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def _integration_test(monkeypatch, run="test -f WORKLOG.md"):
    real = strategies.fallback_plan

    def plan(goal, kind=""):
        out = real(goal, kind)
        for t in out["tasks"]:
            if t.get("writes"):
                t["acceptance"] = [*t["acceptance"], {"type": "command", "run": run, "stage": "integration"}]
        return out

    monkeypatch.setattr(strategies, "fallback_plan", plan)


def test_a_plain_goal_is_low_risk_and_needs_no_extra_stage():
    gid = loop.create_goal("Write the worklog", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval"
    ts = loop._goal(gid).test_strategy
    assert ts["risk"] == "low" and ts["mandatory_stages"] == [] and ts["reviewers"] == ["qa"]
    assert not any(c["check"].startswith("stage ") for c in out["report"]["checks"])


def test_a_sensitive_area_makes_integration_mandatory_and_adds_a_security_review():
    gid = loop.create_goal("Harden the login", allowed_paths=["src/auth/*", "WORKLOG.md"], max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] == "failed"
    ts = loop._goal(gid).test_strategy
    assert ts["risk"] == "high" and ts["mandatory_stages"] == ["integration", "security"]
    assert ts["sensitive_paths"] == ["src/auth/*"] and ts["reviewers"] == ["qa", "security"]
    rows = {c["check"]: c for c in out["report"]["checks"]}
    assert not rows["stage integration"]["passed"] and rows["stage security"]["passed"], "the secret scan ran"
    assert "stage integration" in loop._goal(gid).history[-1]["failed_checks"]
    assert "acceptance condition" in out["report"]["feedback"] and '"stage": "integration"' in out["report"]["feedback"]
    with session() as s:
        tasks = {t.key: t for t in s.query(Task).filter(Task.goal_id == gid)}
        assert tasks["build"].contract["risk_level"] == "high" and "build-security" in tasks
    d = next(d for d in performance.list_defects(goal_id=gid) if d["check"] == "stage integration")
    assert d["severity"] == "high" and d["open"]


def test_a_staged_condition_is_run_again_by_the_gate_and_satisfies_the_stage(monkeypatch):
    _integration_test(monkeypatch)
    gid = loop.create_goal("Harden the login", allowed_paths=["src/auth/*", "WORKLOG.md"], max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval", out["report"]["feedback"]
    rows = {c["check"]: c for c in out["report"]["checks"]}
    run = rows["command test -f WORKLOG.md"]
    assert run["stage"] == "integration" and run["from_task"] and run["exit_code"] == 0
    assert run["commit"] == out["report"]["head"], "run on the tested commit, not the agent's earlier state"
    assert rows["stage integration"]["passed"] and rows["stage integration"]["detail"] == "1 check(s) ran"
    assert {r["stage"]: r["status"] for r in out["report"]["stages"]}["integration"] == "pass"


def test_a_failing_staged_condition_fails_its_stage(monkeypatch):
    _integration_test(monkeypatch, run="test -f NOT_THERE")
    gid = loop.create_goal("Plain", quality_commands=["test -f WORKLOG.md"], max_iterations=1)
    out = loop.run_goal(gid)
    st = {r["stage"]: r["status"] for r in out["report"]["stages"]}
    assert out["status"] == "failed" and st["integration"] == "fail"
    assert "Integration check failed" in out["report"]["feedback"]


def test_classification_and_defect_history():
    gid = loop.create_goal("Secret work", classification="restricted")
    ts = teststrategy.derive(gid)
    assert ts["risk"] == "high" and ts["reasons"] == ["classified restricted"]
    assert teststrategy.derive(loop.create_goal("Conf", classification="confidential"))["risk"] == "medium"
    repo = projects.create("Billing")["path"]
    earlier = loop.create_goal("Earlier", repo_path=repo)
    later = loop.create_goal("Later", repo_path=repo, allowed_paths=["src/billing/*"])
    with session() as s:
        s.get(Goal, earlier).history = [
            {"iteration": 1, "failed_checks": ["command pytest"], "changed_files": ["src/billing/tax.py", "README.md"]},
            {"iteration": 2, "failed_checks": [], "changed_files": ["src/billing/ok.py"]}]
    ts = teststrategy.derive(later)
    assert ts["risk"] == "medium" and ts["defect_history"] == {"src/billing/tax.py": [earlier]}
    assert ts["mandatory_stages"] == [] and "src/billing/tax.py" in teststrategy.planner_note(ts)


def test_the_planner_is_told_the_strategy(monkeypatch):
    prompts = []
    real = executors.Executor.run

    def spy(self, req):
        if req.purpose == "plan":
            prompts.append(req.prompt)
        return real(self, req)

    monkeypatch.setattr(executors.Executor, "run", spy)
    gid = loop.create_goal("Rotate the tokens", allowed_paths=["auth/tokens.py", "WORKLOG.md"], max_iterations=1)
    loop.run_goal(gid)
    assert "Test strategy: risk high" in prompts[0] and "Mandatory gate stages: integration, security" in prompts[0]
    assert '"stage": "integration"' in prompts[0]


def test_condition_stage_is_validated():
    c = contracts.Condition(type="command", run="pytest tests/it", stage="integration")
    assert c.stage == "integration"
    with pytest.raises(ValidationError):
        contracts.Condition(type="command", run="pytest", stage="regression")


def test_sensitive_paths():
    hits = [p for p in ["src/auth/login.py", "app/session.ts", "lib/crypto.py", ".env", "keys/id.pem",
                        "src/monkey.py", "docs/readme.md", "src/permissions.py"] if teststrategy.SENSITIVE.search(p)]
    assert hits == ["src/auth/login.py", "app/session.ts", "lib/crypto.py", ".env", "keys/id.pem",
                    "src/permissions.py"]
