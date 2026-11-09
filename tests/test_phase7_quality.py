"""Verification phase 7: quality and evaluation, offline. The PM (General Manager), developer and QA each run on their
own local stub provider through the real loop; the gate decides. The live run (this server's QA judging a correct, a
buggy and an incomplete delivery) is docs/verification/phase7_live.py. Report: docs/verification/phase-7-quality.md."""
import json

import pytest
from sqlalchemy import select
from test_phase1_models import GOOD, Stub
from test_phase4_communication import DEV_REPLY, FACTORIAL, TASK, TESTS, blocks, plan

from agent_company import loop, performance, router
from agent_company.db import Agent, Run, Task, session

REVIEW = {"key": "review", "title": "Independent review", "role": "qa", "depends_on": ["factorial"],
          "description": "Review the factorial work: verify the logic, list FINDING lines, end with OUTCOME and VERDICT.",
          "acceptance": [{"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}]}
LOGIC = ("Logic verification:\n- normal case: factorial(5) -> 120 traced through the loop\n"
         "- edge case: factorial(0) -> 1, the loop does not run\n- error case: factorial(-1) raises ValueError\n")
QA_PASS = LOGIC + "OUTCOME: APPROVED\nVERDICT: PASS"
QA_FAIL = (LOGIC.replace("factorial(5) -> 120", "factorial(5) -> 24, expected 120")
           + "FINDING: factorial.py:9 - range(2, n) stops one short - factorial(5) returns 24\n"
             "OUTCOME: CHANGES_REQUIRED\nVERDICT: FAIL")


@pytest.fixture
def team():
    """PM, developer and QA, each on its own provider."""
    stubs = {"general_manager": Stub("model-pm", plan(TASK, REVIEW)), "developer": Stub("model-dev", DEV_REPLY),
             "qa": Stub("model-qa", QA_PASS)}
    for role, stub in stubs.items():
        router.add_model({"name": f"Model {role}", "kind": "llm", "model_id": f"openai/{stub.model}",
                          "base_url": stub.url, "api_key": GOOD})
        router.set_fallback(role, [])
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.role.in_(list(stubs)))):
            a.model_pref = f"Model {a.role}"
    yield stubs
    for stub in stubs.values():
        stub.srv.shutdown()


def _run(max_iterations=1, **kw):
    gid = loop.create_goal("Phase 7: factorial", "factorial with tests, reviewed", max_iterations=max_iterations, **kw)
    return gid, loop.run_goal(gid)


def _tasks(gid):
    with session() as s:
        rows = s.scalars(select(Task).where(Task.goal_id == gid).order_by(Task.id)).all()
        for t in rows:
            s.expunge(t)
        return rows


def _checks(out):
    return {c["check"]: c["passed"] for c in out["report"]["checks"]}


# --- 7.1 / 7.2: correct, buggy and incomplete work --------------------------------------------------------------

@pytest.mark.parametrize("variant,dev_reply,qa_reply,passed", [
    ("A correct", DEV_REPLY, QA_PASS, True),
    ("B buggy, its own tests miss it", blocks({"factorial.py": FACTORIAL.replace("range(2, n + 1)", "range(2, n)"),
                                              "test_factorial.py": TESTS.replace("assert factorial(20) == 2432902008176640000", "pass")
                                              .replace("[1, 1, 2, 6, 24, 120]", "[1, 1, 1, 2, 6, 24]")})
     + "Execution result: 3 passed", QA_FAIL, False),
    # incomplete: no input validation (factorial(-1) returns 1), tests only for small values; it claims success
    ("C incomplete", blocks({"factorial.py": FACTORIAL.split("    if isinstance")[0] + "    result = 1\n"
                                             "    for i in range(2, n + 1):\n        result *= i\n    return result\n",
                             "test_factorial.py": "from factorial import factorial\n\n\ndef test_small():\n"
                                                  "    assert factorial(5) == 120\n"})
     + "Execution result: 1 passed", QA_FAIL, False),
])
def test_qa_and_the_gate_pass_a_and_fail_b_and_c(team, variant, dev_reply, qa_reply, passed):
    team["developer"].reply, team["qa"].reply = dev_reply, qa_reply
    gid, out = _run()
    assert out["report"]["passed"] is passed, (variant, out["report"]["feedback"][:500])
    assert _checks(out)["review review"] is passed
    if not passed:
        assert out["status"] == "failed"


def test_a_rubber_stamp_review_does_not_pass_bad_work(team):
    """QA approves the buggy delivery; the goal's own tests (a quality command run by the gate) still fail it."""
    team["developer"].reply = blocks({"factorial.py": FACTORIAL.replace("range(2, n + 1)", "range(2, n)"),
                                      "test_factorial.py": "def test_nothing():\n    assert True\n"}) + "Execution result: 1 passed"
    hidden = "python3 -B -c \"from factorial import factorial; assert factorial(5) == 120\""
    gid, out = _run(quality_commands=[hidden])
    checks = _checks(out)
    assert checks["review review"] is True and checks[f"command {hidden}"] is False and not out["report"]["passed"]


def test_a_bare_verdict_without_the_logic_check_is_not_a_pass(team):
    team["qa"].reply = "Looks good to me.\nVERDICT: PASS"
    gid, out = _run()
    assert _checks(out)["review review"] is False and not out["report"]["passed"]


# --- 7.3: a mandatory failure blocks whatever else passed ---------------------------------------------------------

def test_a_critical_security_failure_blocks_approval_whatever_else_passed(team):
    team["developer"].reply = blocks({"factorial.py": 'API_KEY = "sk-live-9f8e7d6c5b4a3f2e1d0c"\n' + FACTORIAL,
                                      "test_factorial.py": TESTS}) + "Execution result: 3 passed"
    gid, out = _run()
    checks = _checks(out)
    assert checks["task factorial"] and checks["review review"], "every other check passed"
    assert checks["secret scan"] is False and not out["report"]["passed"] and out["status"] == "failed"


def test_a_failed_mandatory_acceptance_test_blocks_approval(team):
    """The developer's own tests fail when the gate runs them: the task fails, QA's review of nothing is skipped
    (every dependency failed), and the iteration fails whatever QA would have said."""
    team["developer"].reply = blocks({"factorial.py": FACTORIAL.replace("range(2, n + 1)", "range(2, n)"),
                                      "test_factorial.py": TESTS}) + "Execution result: 3 passed"
    gid, out = _run()
    checks = _checks(out)
    assert checks["task factorial"] is False and checks["task review"] is False and not out["report"]["passed"]
    review = _tasks(gid)[1]
    assert review.status == "skipped" and "every dependency failed" in review.output and team["qa"].calls == []


# --- 7.4: evaluator independence ---------------------------------------------------------------------------------

def test_the_developer_never_reviews_its_own_work(team):
    gid, out = _run()
    dev_task, review = _tasks(gid)
    assert dev_task.agent_id != review.agent_id
    with session() as s:
        roles = {s.get(Agent, t.agent_id).role for t in (dev_task, review)}
    assert roles == {"developer", "qa"}
    with session() as s:
        runs = {r.task_id: r.model_name for r in s.scalars(select(Run).where(Run.goal_id == gid, Run.purpose == "task"))}
    assert runs == {dev_task.id: "Model developer", review.id: "Model qa"}, "each on its own model"
    assert "Execution result: 3 passed" in dev_task.output and dev_task.gate["acceptance"][2]["passed"], \
        "the developer's claim is beside the gate's own run, never instead of it"


# --- 7.5: the correction cycle -----------------------------------------------------------------------------------

def test_qa_finds_a_bug_the_pm_plans_the_fix_the_developer_fixes_qa_and_the_gate_confirm(team, monkeypatch):
    buggy = blocks({"factorial.py": FACTORIAL.replace("range(2, n + 1)", "range(2, n)"),
                    "test_factorial.py": "def test_nothing():\n    assert True\n"}) + "Execution result: 1 passed"
    dev = iter([buggy, DEV_REPLY])
    qa = iter([QA_FAIL, QA_PASS])
    real = loop._execute

    def execute(agent, req, purpose, **kw):
        if agent.role == "developer":
            team["developer"].reply = next(dev)
        elif agent.role == "qa":
            team["qa"].reply = next(qa)
        return real(agent, req, purpose, **kw)

    monkeypatch.setattr(loop, "_execute", execute)
    gid, out = _run(max_iterations=2)
    assert out["status"] in {"approved", "awaiting_approval"} and out["iterations"] == 2
    # 1. QA reported the bug; 2. the PM's next plan got it; 3. the developer's fix task got it
    replan = team["general_manager"].calls[1]["messages"][-1]["content"]
    assert "range(2, n) stops one short" in replan
    dev_prompt = team["developer"].calls[1]["messages"][-1]["content"]
    assert "range(2, n) stops one short" in dev_prompt
    # 4./5. QA tested again and the gate checked the new result
    t1, r1, t2, r2 = _tasks(gid)
    assert (r1.iteration, r2.iteration) == (1, 2) and "VERDICT: FAIL" in r1.output and "VERDICT: PASS" in r2.output
    # the defect's lifecycle: opened by the gate from QA's failure, fixed, verified by the gate's own rerun
    [d] = [d for d in performance.list_defects(goal_id=gid) if d["check"] == "review review"]
    assert d["status"] in {"retested", "closed"}, d
    assert [h["to"] for h in d["history"]][:4] == ["detected", "assigned", "fix_submitted", "retested"], d["history"]
    # 6. the goal's state, and the original failure kept in its history
    assert [h["passed"] for h in loop._goal(gid).history] == [False, True]
    assert json.dumps(r1.gate) and r1.status == "done"
