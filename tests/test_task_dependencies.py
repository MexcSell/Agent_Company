"""K237: order-only `after`, worktree preconditions `requires`, and reviews of partial work (S07-R-02, G-R-01)."""

import subprocess
import threading

import pytest

from agent_company import contracts, loop, quality, scheduler
from agent_company.config import settings
from agent_company.db import Goal, Task, session

PASS_REVIEW = "## Logic verification\nnormal, edge and error cases traced.\nOUTCOME: APPROVED\nVERDICT: PASS"


def _c(key, role="developer", **extra):
    return contracts.validate({"key": key, "title": key, "role": role, "description": "work"} | extra)


def _run(gid, plan, outcomes, gates=None, worktree=None):
    """Run the plan with a stub worker; returns (call order, final states by key, task ids by key).
    With `worktree`, the worker first applies the loop's real precondition check (`loop._skip_unmet`)."""
    ids = loop._persist(gid, 1, plan)
    key_of = {tid: c.key for tid, c in zip(ids, plan, strict=True)}
    contract_of = dict(zip(ids, plan, strict=True))
    calls, lock = [], threading.Lock()

    def worker(tid):
        key = key_of[tid]
        if worktree is not None and loop._skip_unmet(gid, tid, contract_of[tid], worktree):
            return False
        with lock:
            calls.append(("start", key))
        if gates and key in gates:
            gates[key].wait(5)
        ok = outcomes.get(key, True)
        scheduler.set_status(tid, "done" if ok else "failed",
                             output=PASS_REVIEW if ok else f"ERROR: {key} broke")
        with lock:
            calls.append(("end", key))
        return ok

    state = scheduler.run_dag(ids, worker, f"wt-test-{gid}")
    return calls, {key_of[t]: st for t, st in state.items()}, {c.key: t for t, c in zip(ids, plan, strict=True)}


def _output(tid):
    with session() as s:
        return s.get(Task, tid).output


K237_REQUIRES = [{"type": "file_exists", "path": "src/agent_company/mcp_servers/browser.py"},
                 {"type": "contains", "value": "test_blocks_rebinding", "path": "tests/test_browser.py"}]


def _k237_plan():
    return [_c("build", writes=True), _c("dns_fix", depends_on=["build"], writes=True),
            _c("threat_model", role="architect", after=["dns_fix"], requires=K237_REQUIRES),
            _c("qa_review", role="qa", depends_on=["build", "dns_fix", "threat_model"])]


def _worktree(tmp_path):
    (tmp_path / "src/agent_company/mcp_servers").mkdir(parents=True)
    (tmp_path / "src/agent_company/mcp_servers/browser.py").write_text("# browser\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_browser.py").write_text("def test_blocks_rebinding():\n    pass\n")
    return tmp_path


def test_k237_threat_model_and_review_run_after_dns_fix_fails(tmp_path):
    plan = _k237_plan()
    assert contracts.unmet_requires(plan[2], _worktree(tmp_path)) == []
    gid = loop.create_goal("K237")
    calls, states, _ = _run(gid, plan, {"dns_fix": False})
    assert states == {"build": "done", "dns_fix": "failed", "threat_model": "done", "qa_review": "done"}
    assert calls.index(("start", "threat_model")) > calls.index(("end", "dns_fix"))
    assert calls[-2:] == [("start", "qa_review"), ("end", "qa_review")]


def test_k237_unmet_requires_skips_threat_model_review_still_runs(tmp_path):
    wt = _worktree(tmp_path)
    (wt / "src/agent_company/mcp_servers/browser.py").unlink()
    gid = loop.create_goal("K237 unmet")
    calls, _, ids = _run(gid, _k237_plan(), {"dns_fix": False}, worktree=wt)
    with session() as s:
        stored = {k: s.get(Task, tid).status for k, tid in ids.items()}
    assert stored == {"build": "done", "dns_fix": "failed", "threat_model": "skipped", "qa_review": "done"}
    out = _output(ids["threat_model"])
    assert out.startswith(loop.REQUIRES_SKIP) and "browser.py" in out
    assert ("start", "threat_model") not in calls


def test_k237_partial_review_never_passes_the_iteration():
    repo = settings().worktrees_dir / "k237-gate"
    repo.mkdir(parents=True)
    _worktree(repo)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=repo, check=True)
    gid = loop.create_goal("K237 gate", quality_commands=["true"])
    with session() as s:
        g = s.get(Goal, gid)
        g.worktree, g.base_branch, g.iteration = str(repo), "main", 1
    _run(gid, _k237_plan(), {"dns_fix": False})
    report = quality.evaluate(gid, 1)
    rows = {r["check"]: r["passed"] for r in report["checks"]}
    assert report["passed"] is False
    assert rows["task dns_fix"] is False
    assert rows["review qa_review"] is True


def test_unmet_precondition_skips_with_requires_skip(tmp_path):
    c = _c("threat_model", role="architect", requires=K237_REQUIRES)
    unmet = contracts.unmet_requires(c, tmp_path)
    assert len(unmet) == 2 and "browser.py" in unmet[0] and "file missing" in unmet[1]
    _worktree(tmp_path)
    (tmp_path / "tests/test_browser.py").write_text("def test_other():\n    pass\n")
    unmet = contracts.unmet_requires(c, tmp_path)
    assert len(unmet) == 1 and "test_blocks_rebinding" in unmet[0] and "not found" in unmet[0]
    # case-sensitive on the file: test names are identifiers
    (tmp_path / "tests/test_browser.py").write_text("def TEST_BLOCKS_REBINDING(): pass\n")
    assert len(contracts.unmet_requires(c, tmp_path)) == 1
    assert loop.REQUIRES_SKIP == "skipped (precondition): "
    assert loop.REQUIRES_SKIP.startswith("skipped")


def test_requires_path_stays_inside_worktree(tmp_path):
    root, outside = tmp_path / "wt", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("x")
    (root / "link").symlink_to(outside)
    c = _c("t", requires=[{"type": "file_exists", "path": "link/secret.py"}])
    assert contracts.unmet_requires(c, root) == ["link/secret.py is outside the worktree"]
    for bad in ("/etc/passwd", "../x", "a/../../b", "C:/x", "\\x"):
        with pytest.raises(contracts.ContractError):
            _c("t", requires=[{"type": "file_exists", "path": bad}])


def test_review_whose_dependencies_all_failed_is_skipped():
    gid = loop.create_goal("all failed")
    calls, states, ids = _run(gid, [_c("a"), _c("b"), _c("qa", role="qa", depends_on=["a", "b"])],
                              {"a": False, "b": False})
    assert states["qa"] == "skipped"
    assert _output(ids["qa"]) == "skipped: every dependency failed or was skipped"
    assert ("start", "qa") not in calls


def test_review_waits_for_every_dependency():
    gid = loop.create_goal("waits")
    b_go = threading.Event()
    plan = [_c("a", priority=0), _c("b", priority=1), _c("qa", role="qa", depends_on=["a", "b"])]
    ids = loop._persist(gid, 1, plan)
    key_of = dict(zip(ids, ["a", "b", "qa"], strict=True))
    calls = []

    def worker(tid):
        key = key_of[tid]
        calls.append(("start", key))
        if key == "a":
            scheduler.set_status(tid, "failed", output="ERROR")
            b_go.set()
            return False
        if key == "b":
            b_go.wait(5)
        scheduler.set_status(tid, "done", output=PASS_REVIEW)
        calls.append(("end", key))
        return True

    state = scheduler.run_dag(ids, worker, f"wt-test-{gid}")
    assert {key_of[t]: s for t, s in state.items()} == {"a": "failed", "b": "done", "qa": "done"}
    assert calls.index(("start", "qa")) > calls.index(("end", "b"))


def test_non_review_task_keeps_old_skip():
    gid = loop.create_goal("old skip")
    calls, states, ids = _run(gid, [_c("a"), _c("dev", depends_on=["a"])], {"a": False})
    assert states["dev"] == "skipped"
    assert _output(ids["dev"]) == "skipped: a dependency failed"
    assert ("start", "dev") not in calls


def test_after_is_order_only():
    gid = loop.create_goal("after")
    calls, states, _ = _run(gid, [_c("x", after=["a"], priority=0), _c("a", priority=9)], {"a": False})
    assert states == {"a": "failed", "x": "done"}
    assert calls.index(("start", "x")) > calls.index(("end", "a"))


def test_validate_plan_rejects_bad_after_and_requires():
    base = {"title": "t", "role": "developer", "description": "work"}
    bad_plans = [
        [base | {"key": "a", "after": ["nope"]}],
        [base | {"key": "a", "depends_on": ["b"]}, base | {"key": "b", "after": ["a"]}],
        [base | {"key": "a"}, base | {"key": "b", "depends_on": ["a"], "after": ["a"]}],
        [base | {"key": "a", "requires": [{"type": "command", "run": "true"}]}],
        [base | {"key": "a", "requires": [{"type": "regex", "pattern": "x", "path": "f"}]}],
        [base | {"key": "a", "requires": [{"type": "contains", "value": "x"}]}],
        [base | {"key": "a", "requires": [{"type": "contains", "path": "f"}]}],
    ]
    for plan in bad_plans:
        with pytest.raises(contracts.ContractError):
            contracts.validate_plan(plan)
    with pytest.raises(contracts.ContractError, match="cycle"):
        contracts.validate_plan(bad_plans[1])


def test_old_plans_stay_valid():
    c = contracts.validate_plan([{"key": "a", "title": "t", "role": "developer", "description": "work"},
                                 {"key": "b", "title": "t", "role": "qa", "description": "r", "depends_on": ["a"]}])
    assert all(x.after == [] and x.requires == [] for x in c)
    assert "after" in loop.PLAN_INSTRUCTIONS and "requires" in loop.PLAN_INSTRUCTIONS
