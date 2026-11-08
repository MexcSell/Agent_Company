"""Integration: gate commands (contracts.run_command at L2 and L3) and agent CLI runs (Executor._spawn) run a real
pytest in a fresh directory and leave no __pycache__/ or .pytest_cache/ behind; a control run without the fix does."""
import os
import shutil
import subprocess
import sys
import types

import pytest

from agent_company import contracts, executors, sandbox, worktrees

real = pytest.mark.skipif(not shutil.which("bwrap"), reason="bubblewrap is not installed")
CACHES = ("__pycache__", ".pytest_cache")
FIX = ("PYTHONDONTWRITEBYTECODE", "PYTEST_ADDOPTS")
PYTEST = [sys.executable, "-m", "pytest", "-q", "test_x.py"]


@pytest.fixture(autouse=True)
def _usable(request, monkeypatch):
    if request.node.get_closest_marker("skipif") and not sandbox.available()[0]:
        pytest.skip(f"bubblewrap cannot start a sandbox here: {sandbox.available()[1]}")
    for k in FIX:  # the fix, not the outer environment (a gate run sets both), must keep the caches away
        monkeypatch.delenv(k, raising=False)


def _worktree(name="sbx"):
    repo = worktrees.new_workspace(name)
    wt, _, _ = worktrees.create(repo, name)
    return wt


def caches(root):
    return [p for p in root.rglob("*") if p.name in CACHES]


def project(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "helper.py").write_text("X = 1\n")
    (root / "test_x.py").write_text("import helper\n\n\ndef test_ok():\n    assert helper.X\n    assert True\n")
    return root


def stripped():
    return {k: v for k, v in os.environ.items() if k not in FIX}


def control(root):
    """The same pytest run without the fix: both cache folders appear, so the assertions below can fail."""
    p = subprocess.run(PYTEST, cwd=project(root), env=stripped(), capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert {c.name for c in caches(root)} == set(CACHES)


def gate(mode):
    wt = project(_worktree())
    assert caches(wt) == []
    info = {}
    ok, detail = contracts.run_command(" ".join(PYTEST), wt, 120, mode=mode, info=info)
    assert ok, detail
    assert "1 passed" in detail
    return wt, info


def test_a_gate_command_on_the_host_leaves_no_caches():
    control(_worktree("control"))
    wt, info = gate("off")
    assert info["isolation"] == "L2"
    assert caches(wt) == []


@real
def test_a_sandboxed_gate_command_leaves_no_caches():
    wt, info = gate("required")
    assert info["isolation"] == "L3"
    assert caches(wt) == []


@pytest.mark.parametrize("given", ["none", "addopts"])
def test_an_agent_cli_run_leaves_no_caches(tmp_path, given):
    control(tmp_path / "control")
    work = project(tmp_path / "work")
    env = None if given == "none" else {**stripped(), "PYTEST_ADDOPTS": "-x"}  # None: claude_env's default case
    me = types.SimpleNamespace(kind="test", timeout=120)
    p = executors.Executor._spawn(me, PYTEST, None, work, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "1 passed" in p.stdout
    assert caches(work) == []
