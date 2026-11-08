"""Verification phase 4: agent communication. Two agents only, the plan's Project Manager (the General Manager, who
turns a goal into task contracts) and the Developer, each on its own local stub provider; the goal runs through the
real loop (`loop.run_goal`: plan, persist, staff, work, gate). The checks read the backend's records (tasks, runs,
blackboard, events), never an agent's claim. Report: docs/verification/phase-4-communication.md."""
import json
import time

import pytest
from sqlalchemy import select
from test_phase1_models import GOOD, Stub

from agent_company import blackboard, contracts, loop, router
from agent_company.db import Agent, Event, Run, Task, session

ASSIGNMENT = ("Ask the Developer Agent to create a Python function that calculates factorial.\n\n"
              "The Developer must return:\n1. Source code.\n2. Unit tests.\n3. Execution result.")
FACTORIAL = '''def factorial(n):
    """n! for a non-negative integer n."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
'''
TESTS = '''import pytest

from factorial import factorial


def test_small_values():
    assert [factorial(n) for n in range(6)] == [1, 1, 2, 6, 24, 120]


def test_large_value():
    assert factorial(20) == 2432902008176640000


def test_invalid_input():
    with pytest.raises(ValueError):
        factorial(-1)
    with pytest.raises(TypeError):
        factorial(2.5)
'''
# the three things the PM asked for, each checked by the loop itself: the files exist, the tests pass when the
# platform runs them, and the answer carries an execution result section
ACCEPTANCE = [{"type": "file_exists", "path": "factorial.py"}, {"type": "file_exists", "path": "test_factorial.py"},
              {"type": "command", "run": "python -m pytest -q test_factorial.py"},
              {"type": "contains", "value": "Execution result"}]
TASK = {"key": "factorial", "title": "factorial function with unit tests", "role": "developer", "writes": True,
        "description": "Create factorial.py with factorial(n) and test_factorial.py with unit tests; report the "
                       "execution result of the tests under a heading 'Execution result'.",
        "acceptance": ACCEPTANCE}


def plan(*tasks, summary="delegate the factorial function to the developer"):
    return json.dumps({"summary": summary, "tasks": list(tasks)})


def blocks(files):
    return "".join(f"=== FILE: {p} ===\n{t}=== END FILE ===\n" for p, t in files.items())


DEV_REPLY = blocks({"factorial.py": FACTORIAL, "test_factorial.py": TESTS}) + "Execution result: 3 passed"


@pytest.fixture
def pm_dev():
    """The PM (General Manager) on Model PM, the Developer on Model DEV: two providers, two agents."""
    pm, dev = Stub("model-pm", plan(TASK)), Stub("model-dev", DEV_REPLY)
    router.add_model({"name": "Model PM", "kind": "llm", "model_id": "openai/model-pm", "base_url": pm.url,
                      "api_key": GOOD})
    router.add_model({"name": "Model DEV", "kind": "llm", "model_id": "openai/model-dev", "base_url": dev.url,
                      "api_key": GOOD})
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.role.in_(["general_manager", "developer"]))):
            a.model_pref = "Model PM" if a.role == "general_manager" else "Model DEV"
    for role in ("general_manager", "developer"):
        router.set_fallback(role, [])
    yield pm, dev
    pm.srv.shutdown()
    dev.srv.shutdown()


def _agent(role):
    with session() as s:
        a = s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None)).order_by(Agent.id)).first()
        s.expunge(a)
        return a


def _tasks(gid):
    with session() as s:
        rows = s.scalars(select(Task).where(Task.goal_id == gid).order_by(Task.id)).all()
        for t in rows:
            s.expunge(t)
        return rows


def _runs(gid):
    with session() as s:
        rows = s.scalars(select(Run).where(Run.goal_id == gid).order_by(Run.id)).all()
        for r in rows:
            s.expunge(r)
        return rows


def _goal(gid):
    return loop._goal(gid)


# --- 4.1 / 4.2: the two-agent chain PM -> Developer -> PM --------------------------------------------------------

def test_the_pm_delegates_to_the_developer_and_the_result_comes_back(pm_dev):
    pm, dev = pm_dev
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=2)
    out = loop.run_goal(gid)
    assert out["status"] in {"approved", "awaiting_approval"} and out["iterations"] == 1, out
    gm, developer = _agent("general_manager"), _agent("developer")
    # PM -> Developer: the PM's plan reached provider PM only; the developer's task reached provider DEV only
    assert len(pm.calls) == 1 and "Split the goal into task contracts" in pm.calls[0]["messages"][-1]["content"]
    assert len(dev.calls) == 1
    dev_prompt = dev.calls[0]["messages"][-1]["content"]
    assert "## Your task: factorial function with unit tests" in dev_prompt and ASSIGNMENT.splitlines()[0] in dev_prompt
    assert '"path": "test_factorial.py"' in dev_prompt, "the acceptance conditions travel with the task"
    # the task message: one task, from the planner's contract, run by the developer
    [t] = _tasks(gid)
    assert (t.role, t.status, t.agent_id, t.model_name) == ("developer", "done", developer.id, "Model DEV")
    assert t.contract["acceptance"] == contracts.validate(TASK).model_dump(mode="json")["acceptance"]
    # Developer -> PM: the three deliverables, checked by the platform, not by the developer's word
    files_ok, tests_ok, cmd, report_ok = t.gate["acceptance"]
    assert files_ok["passed"] and tests_ok["passed"] and report_ok["passed"]
    assert cmd["passed"] and "3 passed" in cmd["detail"], "the platform ran the tests itself"
    assert sorted(t.gate["files"]) == ["factorial.py", "test_factorial.py"]
    # the result is on the goal's board for the PM, written by the developer for that task
    eng = {e["key"]: e for e in blackboard.read(gid, ["engineering", "planning"])}
    assert eng["plan"]["author_agent_id"] == gm.id and "factorial (developer)" in eng["plan"]["content"]
    assert eng["factorial"]["author_agent_id"] == developer.id and eng["factorial"]["task_id"] == t.id
    assert "(done)" in eng["factorial"]["content"]
    # the runs: the PM's plan on Model PM, the developer's task on Model DEV, each traced
    runs = [r for r in _runs(gid) if r.purpose in {"plan", "task"}]  # (the lesson writer runs after the goal)
    assert [(r.agent_id, r.purpose, r.model_name, r.ok) for r in runs] == [
        (gm.id, "plan", "Model PM", True), (developer.id, "task", "Model DEV", True)]
    assert runs[1].task_id == t.id and all(router.trace_issues(r.trace) == [] for r in runs)


def test_a_failed_result_goes_back_to_the_pm_who_delegates_the_correction(pm_dev, monkeypatch):
    """The PM evaluates through the gate: a broken result fails it, and the PM's next plan receives the failure."""
    pm, dev = pm_dev
    broken = blocks({"factorial.py": FACTORIAL.replace("range(2, n + 1)", "range(2, n)"),
                     "test_factorial.py": TESTS}) + "Execution result: 3 passed"  # a false claim
    replies, dev_calls = iter([broken, DEV_REPLY]), []
    real = loop._execute

    def execute(agent, req, purpose, **kw):  # the developer's two answers: broken, then fixed
        if agent.role == "developer":
            dev.reply = next(replies)
            dev_calls.append(req.prompt)
        return real(agent, req, purpose, **kw)

    monkeypatch.setattr(loop, "_execute", execute)
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=2)
    out = loop.run_goal(gid)
    assert out["status"] in {"approved", "awaiting_approval"} and out["iterations"] == 2, out
    first, second = _tasks(gid)
    assert (first.iteration, first.status) == (1, "failed") and (second.iteration, second.status) == (2, "done")
    assert "failed" in first.gate["acceptance"][2]["detail"] or not first.gate["acceptance"][2]["passed"]
    # the PM's second plan received the gate's failure
    assert len(pm.calls) == 2
    replan = pm.calls[1]["messages"][-1]["content"]
    assert "The previous iteration failed the quality gate" in replan and "factorial" in replan
    # the developer's correction task carried the failure too, and the failed result is kept (history)
    assert "Findings from the last failed attempt" in dev_calls[1]
    assert [r.ok for r in _runs(gid) if r.purpose == "task"] == [True, True]
    assert [h["passed"] for h in _goal(gid).history] == [False, True]


# --- 4.3: the communication data ---------------------------------------------------------------------------------

def test_every_message_names_sender_receiver_task_content_time_and_status(pm_dev, monkeypatch):
    """The task contract is the PM's message to the developer; MESSAGE lines are the agents' own messages."""
    pm, dev = pm_dev
    dev.reply = DEV_REPLY + "\nMESSAGE handoff to general_manager: factorial.py and test_factorial.py are ready"
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=2)
    loop.run_goal(gid)
    gm, developer = _agent("general_manager"), _agent("developer")
    [t] = _tasks(gid)
    # the task message
    assert t.planned_by == gm.id, "sender"
    assert t.agent_id == developer.id, "receiver"
    assert t.id and t.key == "factorial" and t.goal_id == gid, "task and message ids"
    assert contracts.TaskContract.model_validate(t.contract).title == TASK["title"], "content in the contract format"
    assert t.created_at and t.started_at and t.finished_at and t.created_at <= t.started_at <= t.finished_at
    stages = [e["to"] for e in t.stage_log]
    assert stages[:4] == ["planned", "assigned", "in_progress", "review"], "delivery: planned -> assigned -> running"
    assert t.stage_log[1]["reason"] == developer.name, "delivered to this agent"
    assert (t.status, t.stage) == ("done", stages[-1]) and t.stage in {"approved", "completed"}, "response status"
    [row] = loop.trace(gid)["tasks"]  # the goal trace (Goals → trace) shows the sender beside the receiver
    assert (row["planned_by"], row["agent"], row["created_at"]) == (gm.name, developer.name, t.created_at.isoformat())
    # the developer's message to the PM
    [m] = blackboard.read(gid, ["messages"])
    assert m["author_agent_id"] == developer.id and m["meta"]["from"] == "developer", "sender"
    assert m["meta"]["to"] == "general_manager" and m["meta"]["kind"] == "handoff", "receiver and format"
    assert m["task_id"] == t.id and m["id"] and m["at"] and m["content"].startswith("factorial.py and"), \
        "task, message id, timestamp, content"
    assert m["meta"]["delivered"] == [], "not yet delivered: the PM has not planned since"
    assert "factorial.py and test_factorial.py are ready" in blackboard.inbox(gid, "general_manager", gm.id)
    [m] = blackboard.read(gid, ["messages"])
    assert m["meta"]["delivered"] == [gm.id], "delivery status names the receiving agent"
    assert blackboard.inbox(gid, "general_manager", gm.id) == "", "delivered once"


# --- 4.4: communication failures ---------------------------------------------------------------------------------

def test_1_receiver_offline_its_model_unreachable_the_task_fails_never_completes(pm_dev):
    pm, dev = pm_dev
    dev.srv.shutdown()
    dev.srv.server_close()
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    out = loop.run_goal(gid)
    [t] = _tasks(gid)
    assert out["status"] == "failed" and (t.status, t.stage) == ("failed", "failed")
    assert "ERROR:" in t.output
    runs = [r for r in _runs(gid) if r.purpose == "task"]
    assert runs and not any(r.ok for r in runs) and all(r.task_id == t.id for r in runs)


def test_1_receiver_offline_its_model_at_its_limit_the_task_waits_and_runs_once_on_resume(pm_dev, monkeypatch):
    """The developer's model is at its usage limit, with no fallback: the task waits, the goal pauses; once the
    limit is gone the same task runs once, the plan is not made again."""
    pm, dev = pm_dev
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)  # the test runs the resumed goal itself
    router.mark_limited("Model DEV", "You've hit your session limit · resets 9:50pm")
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=2)
    out = loop.run_goal(gid)
    [t] = _tasks(gid)
    assert out["status"] == "paused" and "usage limit" in out["quota"]
    assert (t.status, t.stage) == ("skipped", "blocked") and t.output.startswith(loop.QUOTA_SKIP)
    assert dev.calls == [] and (_goal(gid).checkpoint or {}).get("resume_after"), "it resumes at the reset"
    router.clear_limit("Model DEV")
    loop.resume(gid, "test")
    out = loop.run_goal(gid)
    assert out["status"] in {"approved", "awaiting_approval"}, out
    assert [x.id for x in _tasks(gid)] == [t.id], "the same task, not a copy"
    assert len(pm.calls) == 1 and len(dev.calls) == 1, "planned once, delivered once"
    assert [r.task_id for r in _runs(gid) if r.purpose == "task"] == [t.id]


def test_1_receiver_offline_every_developer_disabled_the_task_is_never_completed(pm_dev):
    """Every developer agent disabled: staffing recruits a replacement (`recruit_requires_approval` is "never"); it has
    no model of its own and the developer route none, so the offline simulator stands in (phase 2's V7). The task
    fails honestly and the trace flags the stand-in."""
    pm, dev = pm_dev
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.role == "developer")):
            a.enabled = False
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    out = loop.run_goal(gid)
    [t] = _tasks(gid)
    assert out["status"] == "failed" and t.status == "failed" and dev.calls == []
    [run] = [r for r in _runs(gid) if r.purpose == "task"]
    assert run.model_name == "Offline simulator" and router.trace_issues(run.trace)


def test_2_receiver_rejects_the_task_it_is_not_completed(pm_dev):
    pm, dev = pm_dev
    dev.reply = "I decline this task: writing a factorial function is outside my responsibilities."
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    out = loop.run_goal(gid)
    [t] = _tasks(gid)
    assert out["status"] == "failed" and t.status == "failed"
    assert [c["passed"] for c in t.gate["acceptance"]][:3] == [False, False, False]


def test_2_receiver_names_a_blocker_the_goal_waits_for_a_person(pm_dev):
    pm, dev = pm_dev
    dev.reply = "BLOCKER: the task does not say which Python version factorial must support"
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=2)
    out = loop.run_goal(gid)
    [t] = _tasks(gid)
    assert out["status"] == "paused" and (t.status, t.stage) == ("skipped", "blocked")
    assert t.output.startswith(loop.BLOCKER_SKIP) and len(pm.calls) == 1


def _pm_replies(monkeypatch, pm, replies):
    replies = iter(replies)
    real = loop._execute

    def execute(agent, req, purpose, **kw):
        if purpose == "plan":
            pm.reply = next(replies)
        return real(agent, req, purpose, **kw)

    monkeypatch.setattr(loop, "_execute", execute)


@pytest.mark.parametrize("bad", ["Sure! I will ask the developer to write it.",  # no JSON
                                 plan({**TASK, "depends_on": ["design"]}),  # 6: a task id that does not exist
                                 plan({**TASK, "role": ""})])  # a contract without a receiver
def test_3_and_6_an_invalid_task_message_goes_back_to_the_pm_once(pm_dev, monkeypatch, bad):
    pm, dev = pm_dev
    _pm_replies(monkeypatch, pm, [bad, plan(TASK)])
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    out = loop.run_goal(gid)
    assert out["status"] in {"approved", "awaiting_approval"}, out
    assert len(pm.calls) == 2 and "was not a valid plan" in pm.calls[1]["messages"][-1]["content"]
    assert [t.key for t in _tasks(gid)] == ["factorial"] and len(dev.calls) == 1
    with session() as s:
        assert s.scalars(select(Event).where(Event.goal_id == gid, Event.message.like("%was invalid%"))).first()


def test_3_twice_invalid_the_standard_plan_runs_and_says_so(pm_dev, monkeypatch):
    pm, dev = pm_dev
    _pm_replies(monkeypatch, pm, ["not a plan", "still not a plan"])
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    loop.run_goal(gid)
    assert len([c for c in pm.calls if "Split the goal" in c["messages"][-1]["content"]]) == 2  # (then the RCA)
    with session() as s:
        assert s.scalars(select(Event).where(Event.goal_id == gid, Event.message.like("%using the standard plan%"))).first()
    assert "standard plan" in blackboard.read(gid, ["planning"])[0]["content"]


def test_3_an_agent_message_to_no_role_or_in_no_known_kind_is_reported_not_lost(pm_dev):
    gid = loop.create_goal("Phase 4: messages")
    developer = _agent("developer")
    out = ("MESSAGE request to devloper: add the edge case n=0\n"  # a misspelled receiver
           "MESSAGE question to qa: does 0! count as covered?\n"  # a kind that does not exist
           "MESSAGE handoff to qa: factorial.py is ready")
    assert blackboard.extract_messages(gid, 1, "factorial", developer.id, "developer", out) == 1
    [m] = blackboard.read(gid, ["messages"])
    assert m["meta"]["to"] == "qa" and m["meta"]["kind"] == "handoff"
    with session() as s:
        notes = [e.message for e in s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "message"))]
    assert any("devloper" in n and "no such role" in n for n in notes), notes
    assert any("question to qa" in n and "not a message" in n for n in notes), notes


def test_4_a_finished_task_is_not_run_again_when_its_iteration_resumes(pm_dev):
    pm, dev = pm_dev
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    loop.run_goal(gid)
    [t] = _tasks(gid)
    assert t.status == "done"
    from agent_company import scheduler

    calls = []
    assert loop._requeue_unfinished(gid, 1) == [t.id]
    scheduler.run_dag([t.id], lambda tid: calls.append(tid) or True, _goal(gid).worktree)
    assert calls == [] and len(dev.calls) == 1


def test_4_a_goal_handed_out_twice_runs_once(pm_dev, monkeypatch):
    """Two workers given the same goal at the same moment (a submit and the watcher, two processes): one runs it."""
    import threading

    pm, dev = pm_dev
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    both = threading.Barrier(2, timeout=5)
    real = loop._goal

    def goal(goal_id):  # both workers read the goal as queued before either moves it on
        g = real(goal_id)
        if threading.current_thread().name.startswith("dup-") and g.status == "queued":
            try:
                both.wait()
            except threading.BrokenBarrierError:
                pass
        return g

    monkeypatch.setattr(loop, "_goal", goal)
    outs = []
    threads = [threading.Thread(target=lambda: outs.append(loop.run_goal(gid)), name=f"dup-{i}") for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert len(pm.calls) == 1 and len(dev.calls) == 1, (outs, len(pm.calls), len(dev.calls))
    assert len(_tasks(gid)) == 1


def test_5_a_task_past_its_deadline_fails_and_nothing_keeps_running(pm_dev):
    pm, dev = pm_dev
    pm.reply = plan({**TASK, "deadline": 1})
    dev.mode = "slow"  # answers after 5 s
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    started = time.monotonic()
    out = loop.run_goal(gid)
    [t] = _tasks(gid)
    assert out["status"] == "failed" and t.status == "failed" and "ERROR:" in t.output
    runs = [r for r in _runs(gid) if r.purpose == "task"]
    assert runs and not any(r.ok for r in runs) and all("imeout" in r.error or "timed out" in r.error for r in runs)
    assert time.monotonic() - started < 60
    from agent_company.db import LiveProcess

    with session() as s:
        assert s.scalars(select(LiveProcess)).all() == []


def test_6_a_worker_given_a_task_id_that_does_not_exist_starts_nothing(pm_dev):
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT)
    loop._update_goal(gid, worktree="")
    with pytest.raises(Exception):  # noqa: B017
        loop._make_worker(_goal(gid), "", "", time.monotonic())(9999)
    assert _runs(gid) == [] and _tasks(gid) == []
    with session() as s:
        assert all(a.status == "idle" for a in s.scalars(select(Agent).where(Agent.role == "developer")))


@pytest.mark.parametrize("reply,failing", [
    (blocks({"factorial.py": FACTORIAL}) + "Execution result: 3 passed", ["test_factorial.py", "command"]),  # no tests
    (blocks({"factorial.py": FACTORIAL, "test_factorial.py": TESTS}), ["Execution result"]),  # no execution result
])
def test_7_an_incomplete_result_is_not_completed(pm_dev, reply, failing):
    pm, dev = pm_dev
    dev.reply = reply
    gid = loop.create_goal("Phase 4: factorial", ASSIGNMENT, max_iterations=1)
    out = loop.run_goal(gid)
    [t] = _tasks(gid)
    assert out["status"] == "failed" and t.status == "failed"
    failed = [c for c in t.gate["acceptance"] if not c["passed"]]
    assert all(any(f in json.dumps(c) for c in failed) for f in failing), failed
