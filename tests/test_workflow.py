"""Goal workflow states (S04-R-04), pause/resume, restart recovery, execution trace (S04-R-12), goal-chain
depth (S04-R-08) and human-only proposals (S14-R-02)."""

import subprocess

import pytest
from sqlalchemy import select

from agent_company import executors, loop, projects, workflow
from agent_company.db import Goal, Proposal, Task, session


def test_every_status_change_is_a_recorded_legal_transition():
    gid = loop.create_goal("Walk the states", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    loop.approve(gid, actor="user:admin")
    steps = [(t["from"], t["to"]) for t in workflow.history(gid)]
    assert steps == [("queued", "planning"), ("planning", "running"), ("running", "awaiting_approval"),
                     ("awaiting_approval", "approved")]
    assert workflow.history(gid)[-1]["actor"] == "user:admin"
    with pytest.raises(workflow.IllegalTransition):
        workflow.transition(gid, "running")
    assert len(workflow.history(gid)) == 4, "a refused transition writes nothing"


def test_a_revision_goes_back_to_planning():
    gid = loop.create_goal("Two rounds", quality_commands=["false"], max_iterations=2)
    loop.run_goal(gid)
    steps = [t["to"] for t in workflow.history(gid)]
    assert steps == ["planning", "running", "planning", "running", "failed"]
    assert "revision required" in workflow.history(gid)[2]["reason"]


def test_pause_mid_iteration_then_resume_from_the_checkpoint(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    calls = []
    real = executors.OfflineExecutor._task
    gid_box = {}

    def task(self, req):
        calls.append(req.data.get("title"))
        if req.writes and len(calls) == 2:
            loop.pause(gid_box["id"], actor="user:admin", reason="lunch")
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = gid_box["id"] = loop.create_goal("Pausable", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "paused"
    with session() as s:
        assert s.get(Goal, gid).status == "paused"
    assert loop.run_goal(gid) == {"status": "paused"}, "a paused goal does not run"
    with pytest.raises(ValueError):
        loop.pause(gid)
    loop.resume(gid, actor="user:admin")
    before = len(calls)
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval"
    assert len(calls) == before, "the tasks finished before the pause are not run again"
    assert [t["to"] for t in workflow.history(gid)][-5:] == ["paused", "queued", "planning", "running",
                                                              "awaiting_approval"]


def test_restart_marks_working_goals_recovering_and_they_resume():
    gid = loop.create_goal("Interrupted")
    workflow.transition(gid, "planning")
    q = loop.GoalQueue(workers=0)
    q.start()
    loop.set_queue(None)
    with session() as s:
        assert s.get(Goal, gid).status == "recovering"
    assert workflow.history(gid)[-1]["reason"] == "server restarted"
    assert loop.run_goal(gid)["status"] == "awaiting_approval"


def _process():
    p = subprocess.Popen(["sleep", "60"])
    return p, f"{p.pid}:{loop._proc_start(p.pid)}"


def _owned(title, status, owner):
    gid = loop.create_goal(title)
    workflow.transition(gid, status)
    with session() as s:
        s.get(Goal, gid).owner = owner
    return gid


def test_a_goal_records_the_process_that_took_it():
    gid = loop.create_goal("Owned")
    loop.run_goal(gid)
    with session() as s:
        assert s.get(Goal, gid).owner == loop.process_token()
    assert loop.owner_alive(loop.process_token())


def test_a_goal_whose_process_ended_is_resumed_without_a_restart():
    """V25: a CLI run or live script that dies leaves its goal planning; the server's watcher takes it back."""
    p, token = _process()
    assert loop.owner_alive(token)
    p.kill()
    p.wait()
    assert not loop.owner_alive(token)
    orphan = _owned("Orphaned", "planning", token)
    live_p, live_token = _process()
    try:
        elsewhere = _owned("Runs elsewhere", "planning", live_token)
        mine = _owned("Runs here", "planning", loop.process_token())
        legacy = _owned("No owner", "planning", "")
        reused = _owned("Reused pid", "planning", f"{live_p.pid}:1")  # the pid of another process now
        assert sorted(loop.reclaim_orphans()) == sorted([orphan, reused])
        assert loop.reclaim_orphans() == [], "a reclaimed goal is not taken twice"
        with session() as s:
            assert {g.id: g.status for g in s.scalars(select(Goal).where(
                Goal.id.in_([orphan, elsewhere, mine, legacy, reused])))} == {
                orphan: "recovering", elsewhere: "planning", mine: "planning", legacy: "planning",
                reused: "recovering"}
            assert s.get(Goal, orphan).owner == ""
        assert workflow.history(orphan)[-1]["reason"] == "the process running it ended"
        assert loop.run_goal(orphan)["status"] == "awaiting_approval"
    finally:
        live_p.kill()
        live_p.wait()


def test_a_restart_leaves_a_goal_another_live_process_runs():
    live_p, live_token = _process()
    try:
        elsewhere = _owned("CLI run", "running", live_token)
        dead = _owned("Old server", "planning", f"{2**22 + 1}:1")  # above pid_max: never a process
        q = loop.GoalQueue(workers=0)
        q.start()
        loop.set_queue(None)
        with session() as s:
            assert s.get(Goal, elsewhere).status == "running"
            assert s.get(Goal, dead).status == "recovering" and s.get(Goal, dead).owner == ""
        assert elsewhere not in q.seen and dead in q.seen
    finally:
        live_p.kill()
        live_p.wait()


def test_trace_names_the_agent_and_model_of_every_run():
    gid = loop.create_goal("Traceable", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    tr = loop.trace(gid)
    assert tr["runs"] and all(r["agent"] and r["model"] for r in tr["runs"])
    assert {t["key"] for t in tr["tasks"]} == {"spec", "build", "review"}
    assert tr["gates"][0]["passed"] is True and tr["transitions"][0]["to"] == "planning"


def _chain(depth):
    gid = loop.create_goal("root")
    for _ in range(depth - 1):
        gid = loop.create_goal("child", origin="proposal", parent_goal_id=gid)
    return gid


def test_a_proposal_from_a_goal_at_max_depth_is_recorded_but_spawns_nothing(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    deep = _chain(3)
    with session() as s:
        s.add(Proposal(goal_id=deep, title="Yet more work", description="x"))
        s.flush()
        pid = s.scalars(select(Proposal)).first().id
    out = projects.decide(pid, True, "admin")
    assert out["status"] == "rejected" and "depth" in out["decided_by"] and not out.get("spawned_goal_id")


def test_agents_cannot_approve_proposals_that_raise_authority(monkeypatch):
    from agent_company import config

    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    gid = loop.create_goal("origin")
    with session() as s:
        s.add(Proposal(goal_id=gid, title="Give the developer shell permission", description="faster"))
        s.add(Proposal(goal_id=gid, title="Add a README section", description="docs"))
    with session() as s:
        restricted_id, plain_id = [p.id for p in s.scalars(select(Proposal).order_by(Proposal.id))]
    with pytest.raises(PermissionError):
        projects.decide(restricted_id, True, "GM (GM)", by_human=False)
    config.settings().proposal_policy = "superiors"
    projects.settle_proposals(gid)
    with session() as s:
        assert s.get(Proposal, restricted_id).status == "pending", "left for a person"
        assert s.get(Proposal, plain_id).status == "approved", "the GM may decide the harmless one"
    assert projects.decide(restricted_id, True, "admin")["status"] == "approved"


def test_cancelled_tasks_and_queue_use_workflow_states():
    gid = loop.create_goal("Cancel me")
    loop.cancel(gid, actor="user:x")
    assert workflow.history(gid)[-1] == {**workflow.history(gid)[-1], "from": "queued", "to": "cancelled",
                                         "actor": "user:x"}
    with session() as s:
        assert not s.scalars(select(Task).where(Task.goal_id == gid)).first()
