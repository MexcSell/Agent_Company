"""EL-R-06: with `task_critic` on, a critic of another model family (EL-R-03) reviews a writing task's diff before
its dependants start; a FAIL returns the task to the same agent with the critic's points, at most twice."""

from pathlib import Path

import pytest
from sqlalchemy import select

from agent_company import executors, loop, prefs, quality, router
from agent_company.contracts import TaskContract
from agent_company.db import Event, Task, session

POINT = "feature.py:1 | the value is wrong | set x = 2"  # a critic's point: its `FINDING:` line


@pytest.fixture
def goal_loop(monkeypatch):
    """A developer task then a QA review of it. The author runs on Claude Code; the critic answers `verdicts` in
    order from `critic_choice` (another family unless a test changes it)."""
    plan = [TaskContract(key="implement", title="implement", role="developer", description="Build it.", writes=True),
            TaskContract(key="review", title="review", role="qa", description="Review it.", depends_on=["implement"])]
    monkeypatch.setattr(loop, "_plan", lambda goal, it, fb, notes: ([p.model_copy() for p in plan], None))
    monkeypatch.setattr(loop, "enqueue", lambda goal_id: None)
    monkeypatch.setattr(quality, "evaluate", lambda goal_id, iteration: {
        "passed": False, "checks": [], "summary": "stop here", "feedback": "", "commit": ""})
    state = {"calls": [], "verdicts": [], "critic_choice": router.Choice(
        name="gpt", kind="llm", model_id="gpt-5", reason="test")}
    real = loop._execute

    def execute(agent, req, purpose, task_id=None, **kw):
        if purpose == "critic":
            state["calls"].append(("critic", agent.id, kw.get("avoid_provider"), kw.get("other_cli"), req.prompt))
            verdict = state["verdicts"].pop(0) if state["verdicts"] else "PASS"
            text = (f"FINDING: {POINT}\nVERDICT: FAIL" if verdict == "FAIL" else "Looks right.\nVERDICT: PASS")
            return executors.Result(text=text), state["critic_choice"]
        if purpose != "task":  # lessons and summaries: the offline simulator
            return real(agent, req, purpose, task_id=task_id, **kw)
        key = req.data["title"]
        state["calls"].append((key, agent.id, req.data["feedback"], req.prompt))
        if key == "implement":
            n = sum(1 for c in state["calls"] if c[0] == "implement")
            Path(req.workdir, "feature.py").write_text(f"x = {n}\n")
            return (executors.Result(text="Done: the feature is in feature.py."),
                    router.Choice(name="claude", kind="claude_code", model_id="claude-sonnet-5-5", reason="test"))
        return (executors.Result(text="The change does what the task asks.\nVERDICT: PASS"),
                router.Choice(name="offline", kind="offline", model_id="offline", reason="test"))

    monkeypatch.setattr(loop, "_execute", execute)
    return state


def _run(state, verdicts):
    state["verdicts"] = list(verdicts)
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    loop.run_goal(gid)
    with session() as s:
        tasks = {t.key: t for t in s.scalars(select(Task).where(Task.goal_id == gid, Task.iteration == 1))}
        s.expunge_all()
    return gid, tasks


def _order(state):
    return [c[0] for c in state["calls"]]


def test_a_failing_critic_returns_the_task_to_its_author_and_the_dependant_waits(goal_loop):
    prefs.set_value("task_critic", True)
    gid, tasks = _run(goal_loop, ["FAIL", "PASS"])
    assert _order(goal_loop) == ["implement", "critic", "implement", "critic", "review"]  # B after the PASS
    implement = [c for c in goal_loop["calls"] if c[0] == "implement"]
    assert implement[0][1] == implement[1][1]  # the same agent
    assert POINT in implement[1][2] and POINT in implement[1][3]  # the critic's points: feedback and prompt
    assert not implement[0][2]
    critics = [c for c in goal_loop["calls"] if c[0] == "critic"]
    assert all(c[2] == "anthropic" and c[3] is True for c in critics)  # another family, also another CLI
    assert critics[0][1] != implement[0][1] and "feature.py" in critics[0][4]  # it saw the diff
    t = tasks["implement"]
    assert t.status == "done"
    assert [e["verdict"] for e in t.critic] == ["FAIL", "PASS"]
    assert all(e["provider"] == "openai" and e["model"] == "gpt" for e in t.critic)
    assert t.critic[0]["points"] == [POINT] and [e["attempt"] for e in t.critic] == [1, 2]
    assert sorted(t.gate["files"]) == ["feature.py"]
    with session() as s:
        events = [e.message for e in s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "critic"))]
    assert len(events) == 2 and events[0].endswith("attempt 1: FAIL") and events[1].endswith("attempt 2: PASS")


def test_a_critic_that_always_fails_stops_after_two_retries(goal_loop):
    prefs.set_value("task_critic", True)
    _, tasks = _run(goal_loop, ["FAIL"] * 5)
    assert _order(goal_loop) == ["implement", "critic"] * 3 + ["review"]
    t = tasks["implement"]
    assert [e["verdict"] for e in t.critic] == ["FAIL"] * 3
    assert "Critic (attempt 3, gpt): VERDICT: FAIL" in t.output and POINT in t.output  # QA sees the last points
    assert tasks["review"].status == "done"


def test_with_the_setting_off_no_critic_runs(goal_loop):
    _, tasks = _run(goal_loop, ["FAIL"])
    assert _order(goal_loop) == ["implement", "review"]
    assert tasks["implement"].critic == [] and tasks["implement"].status == "done"


def test_a_critic_of_the_authors_family_is_skipped_and_never_blocks(goal_loop):
    prefs.set_value("task_critic", True)
    goal_loop["critic_choice"] = router.Choice(name="claude", kind="claude_code", model_id="claude-sonnet-5-5",
                                               reason="test")
    _, tasks = _run(goal_loop, ["FAIL"])
    assert _order(goal_loop) == ["implement", "critic", "review"]
    (entry,) = tasks["implement"].critic
    assert entry["verdict"] == "skipped" and entry["reason"] == "no usable model of another family"
    assert entry["points"] == [] and tasks["implement"].status == "done"
