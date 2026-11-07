"""A sandbox that cannot start is infrastructure, not failed work (S14-R-03, S07-R-02): the task is skipped, one
incident is opened per goal and the goal pauses instead of failing its tasks iteration after iteration."""

import pytest
from sqlalchemy import select

from agent_company import contracts, loop, prefs, quality, sandbox, workflow
from agent_company.contracts import Condition, TaskContract
from agent_company.db import Incident, Notification, Task, session

LINE = "bwrap: Can't find source path /x: No such file or directory"


@pytest.fixture
def fake(monkeypatch):
    """`fake(out, rc)`: every sandboxed command returns this, without bubblewrap."""
    calls: list[list[str]] = []
    monkeypatch.setattr(sandbox, "available", lambda: (True, ""))
    monkeypatch.setattr(sandbox, "limits_available", lambda: False)

    def set_(out: str, rc: int = 1) -> list[list[str]]:
        def execute(cmd, cwd, env, timeout):
            calls.append(cmd)
            return rc, out, False

        monkeypatch.setattr(sandbox, "_execute", execute)
        return calls

    return set_


@pytest.fixture
def goal_loop(monkeypatch, fake):
    plan = [_contract("true", "spec", "architect"),
            _contract("", "implement", "developer", writes=True, depends_on=["spec"])]
    monkeypatch.setattr(loop, "_plan", lambda goal, it, fb, notes: ([p.model_copy() for p in plan], None))
    monkeypatch.setattr(loop, "enqueue", lambda goal_id: None)
    evaluated: list[int] = []

    def boom(goal_id, iteration):
        evaluated.append(iteration)
        raise AssertionError("the gate must not judge an iteration the sandbox could not run")

    monkeypatch.setattr(quality, "evaluate", boom)
    return evaluated


def _contract(cmd, key="t", role="developer", **kw):
    return TaskContract(key=key, title=key, role=role, description=f"Do {key}.",
                        acceptance=[Condition(type="command", run=cmd)] if cmd else [], **kw)


def _tasks(gid):
    with session() as s:
        return {t.key: (t.status, t.output or "", t.iteration)
                for t in s.scalars(select(Task).where(Task.goal_id == gid))}


def _incidents(gid):
    with session() as s:
        return [(i.category, i.severity, list(i.evidence or []))
                for i in s.scalars(select(Incident).where(Incident.goal_id == gid, Incident.rule == "sandbox_startup"))]


@pytest.mark.parametrize(("rc", "out", "want"), [
    (1, LINE + "\n", LINE),
    (1, "bwrap: No permissions to create new namespace, likely because the kernel does not allow it",
     "bwrap: No permissions to create new namespace, likely because the kernel does not allow it"),
    (1, "bwrap: setting up uid map: Permission denied", "bwrap: setting up uid map: Permission denied"),
    (1, "bwrap: Creating new namespace failed: Operation not permitted",
     "bwrap: Creating new namespace failed: Operation not permitted"),
    (1, "bwrap: execvp /bin/sh: No such file or directory", "bwrap: execvp /bin/sh: No such file or directory"),
    (1, "Failed to start transient scope unit: Access denied", "Failed to start transient scope unit: Access denied"),
    (1, "Failed to create bus connection: No such file or directory",
     "Failed to create bus connection: No such file or directory"),
    (1, "Failed to connect to bus: No medium found", "Failed to connect to bus: No medium found"),
    (0, LINE, ""),  # it ran and passed
    (1, "collected 3 items\n" + LINE, ""),  # the command started and printed: its own failure
    (1, "bwrap: some other message", ""),
    (1, "", ""),
    (1, "FAILED tests/test_x.py::test_y - AssertionError", ""),
])
def test_startup_failure_only_for_start_up_lines(rc, out, want):
    assert sandbox.startup_failure(rc, out, False) == want


def test_a_timeout_is_never_infrastructure():
    assert sandbox.startup_failure(-9, LINE, True) == ""


def test_run_command_reports_infrastructure(fake, tmp_path):
    wt = tmp_path / "data" / "worktrees" / "x"
    wt.mkdir(parents=True)
    fake(LINE + "\n")
    out = sandbox.run(["/bin/sh", "-c", "true"], wt, 60, mode="required")
    assert out.isolation == "L3" and out.infra == LINE and out.returncode == 1
    info: dict = {}
    ok, detail = contracts.run_command("true", wt, 60, mode="required", info=info)
    assert not ok and info["infra"] == LINE and "infrastructure" in detail and LINE in detail
    rows = contracts.check(_contract("true"), "", wt, mode="required")
    assert rows[0]["infra"] == LINE and rows[0]["passed"] is False


def test_a_normal_failure_carries_no_infra(fake, tmp_path):
    wt = tmp_path / "data" / "worktrees" / "x"
    wt.mkdir(parents=True)
    fake("collected 3 items\n" + LINE + "\n")
    info: dict = {}
    ok, detail = contracts.run_command("pytest", wt, 60, mode="required", info=info)
    assert not ok and not info["infra"] and "infrastructure" not in detail
    rows = contracts.check(_contract("pytest"), "", wt, mode="required")
    assert "infra" not in rows[0]


def test_the_host_path_is_never_classified(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "available", lambda: (False, "no bwrap"))
    monkeypatch.setattr(sandbox, "_execute", lambda cmd, cwd, env, timeout: (1, LINE, False))
    assert sandbox.run(["true"], tmp_path, 60, mode="auto").infra == ""


def test_a_failed_prepare_step_does_not_start_the_command(monkeypatch, fake, tmp_path):
    monkeypatch.setattr(sandbox, "PREPARE", {"deps.lock": ("sh", "true", "deps")})
    wt = tmp_path / "data" / "worktrees" / "x"
    wt.mkdir(parents=True)
    (wt / "deps.lock").write_text("v1")
    calls = fake(LINE + "\n")
    out = sandbox.run(["/bin/sh", "-c", "true"], wt, 60, mode="required", prepare=True)
    assert out.infra == LINE and out.returncode == 1 and "sandbox did not start" in out.note
    assert len(calls) == 1  # only the prepare step: the command itself never started


def test_a_goal_whose_sandbox_cannot_start_pauses_with_an_incident(goal_loop, fake):
    fake(LINE + "\n")
    gid = loop.create_goal("Add a feature", quality_commands=[])
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert out["status"] == "paused" and g.status == "paused", out
    assert g.checkpoint["paused_for"] == "infrastructure" and g.checkpoint["paused_why"] == LINE
    paused = [t for t in workflow.history(gid) if t["to"] == "paused"]
    assert paused and paused[-1]["reason"] == f"infrastructure: {LINE}"
    assert not g.history and not goal_loop  # the iteration was not judged
    tasks = _tasks(gid)
    assert tasks["spec"][0] == "skipped" and tasks["spec"][1].startswith(loop.INFRA_SKIP)
    assert tasks["spec"][1] == loop.INFRA_SKIP + LINE
    assert not any(status == "failed" for status, _, _ in tasks.values())
    incidents = _incidents(gid)
    assert len(incidents) == 1
    category, severity, evidence = incidents[0]
    assert category == "infrastructure" and severity == "SEV-3"
    assert any(e.get("type") == "sandbox" and e.get("line") == LINE for e in evidence)
    with session() as s:
        titles = [n.title for n in s.scalars(select(Notification).where(Notification.goal_id == gid,
                                                                         Notification.admin_only.is_(True)))]
    assert f"Goal #{gid} paused: the sandbox could not start" in titles


def test_required_and_unavailable_start_up_failure_is_infra(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "available", lambda: (False, f"bwrap failed: {LINE.replace('/x', '/opt/x')}"))
    wt = tmp_path / "data" / "worktrees" / "x"
    wt.mkdir(parents=True)
    info: dict = {}
    ok, detail = contracts.run_command("true", wt, 60, mode="required", info=info)
    assert not ok and info["infra"] == LINE.replace("/x", "/opt/x") and "required and unavailable" in detail
    rows = contracts.check(_contract("true"), "", wt, mode="required")
    assert rows[0]["infra"] == LINE.replace("/x", "/opt/x") and rows[0]["passed"] is False


@pytest.mark.parametrize("why", ["bubblewrap (bwrap) is not installed", "bwrap not installed",
                                 "bwrap failed: [Errno 13] Permission denied", "sandbox off"])
def test_required_and_unavailable_for_another_reason_is_not_infra(monkeypatch, tmp_path, why):
    monkeypatch.setattr(sandbox, "available", lambda: (False, why))
    wt = tmp_path / "data" / "worktrees" / "x"
    wt.mkdir(parents=True)
    info: dict = {}
    ok, _ = contracts.run_command("true", wt, 60, mode="required", info=info)
    assert not ok and not info["infra"]
    assert "infra" not in contracts.check(_contract("true"), "", wt, mode="required")[0]


def test_a_required_goal_whose_sandbox_probe_fails_pauses_with_an_incident(goal_loop, monkeypatch):
    prefs.set_value("sandbox", "required")
    monkeypatch.setattr(sandbox, "available", lambda: (False, f"bwrap failed: {LINE}"))
    gid = loop.create_goal("Add a feature", quality_commands=[])
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert out["status"] == "paused" and g.checkpoint["paused_for"] == "infrastructure", out
    tasks = _tasks(gid)
    assert tasks["spec"][0] == "skipped" and tasks["spec"][1] == loop.INFRA_SKIP + LINE
    assert not any(status == "failed" for status, _, _ in tasks.values())
    assert [i[0] for i in _incidents(gid)] == ["infrastructure"] and not goal_loop


def test_command_output_that_mentions_bwrap_is_a_normal_failure(monkeypatch, fake):
    plan = [_contract("true", "spec", "architect")]
    monkeypatch.setattr(loop, "_plan", lambda goal, it, fb, notes: ([p.model_copy() for p in plan], None))
    monkeypatch.setattr(loop, "enqueue", lambda goal_id: None)
    fake("collected 3 items\n" + LINE + "\n")
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    loop.run_goal(gid)
    g = loop._goal(gid)
    status, output, _ = _tasks(gid)["spec"]
    assert status == "failed" and not output.startswith(loop.INFRA_SKIP)
    assert (g.checkpoint or {}).get("paused_for") != "infrastructure"
    assert _incidents(gid) == []


def test_a_benchmark_goal_fails_instead_of_pausing(goal_loop, fake):
    fake(LINE + "\n")
    gid = loop.create_goal("Add a feature", quality_commands=[], origin="eval")
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert out["status"] == "failed" and g.status == "failed" and "infrastructure" in g.result
    assert len(_incidents(gid)) == 1 and not goal_loop


def test_a_second_occurrence_adds_evidence_to_the_same_incident(goal_loop, fake):
    fake(LINE + "\n")
    gid = loop.create_goal("Add a feature", quality_commands=[])
    assert loop.run_goal(gid)["status"] == "paused"
    first = _incidents(gid)[0][2]
    loop.resume(gid, "user:admin")
    assert loop.run_goal(gid)["status"] == "paused"
    incidents = _incidents(gid)
    assert len(incidents) == 1 and len(incidents[0][2]) > len(first)
    assert loop._goal(gid).checkpoint["paused_for"] == "infrastructure" and not goal_loop


def test_after_the_host_is_fixed_the_same_iteration_continues(goal_loop, fake, monkeypatch):
    fake(LINE + "\n")
    gid = loop.create_goal("Add a feature", quality_commands=[], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "paused"
    fake("", rc=0)
    judged: list[int] = []
    monkeypatch.setattr(quality, "evaluate", lambda goal_id, iteration: judged.append(iteration) or {
        "passed": False, "checks": [], "summary": "stop here", "feedback": "", "commit": ""})
    loop.resume(gid, "user:admin")
    loop.run_goal(gid)
    status, _, iteration = _tasks(gid)["spec"]
    assert status == "done" and iteration == 1 and judged == [1]  # the requeued task, in the same iteration
    assert loop._goal(gid).iteration == 1
    assert loop._goal(gid).checkpoint.get("paused_for") != "infrastructure"
