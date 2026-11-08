"""Agent CLI runs and gate commands leave no __pycache__/ or .pytest_cache/ in the worktree."""
import subprocess
import sys

import pytest

from agent_company import executors, sandbox
from agent_company.sandbox import no_cache_env

CACHES = ("__pycache__", ".pytest_cache")


def caches(root):
    return [p for p in root.rglob("*") if p.name in CACHES]


def project(root):
    (root / "helper.py").write_text("X = 1\n")
    (root / "test_x.py").write_text("import helper\n\n\ndef test_ok():\n    assert helper.X\n    assert True\n")


def run_pytest(root, env):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "test_x.py"], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)


def test_empty_dict():
    env = no_cache_env({})
    assert env == {"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": "-p no:cacheprovider"}


def test_none_falls_back_to_os_environ(monkeypatch):
    monkeypatch.setenv("AC_ENV_PROBE", "here")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    env = no_cache_env(None)
    assert env["AC_ENV_PROBE"] == "here"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTEST_ADDOPTS"] == "-p no:cacheprovider"


def test_input_not_mutated():
    given = {"A": "b"}
    no_cache_env(given)
    assert given == {"A": "b"}


def test_operator_addopts_kept_and_not_duplicated():
    env = no_cache_env({"PYTEST_ADDOPTS": "-x"})
    assert env["PYTEST_ADDOPTS"] == "-x -p no:cacheprovider"
    assert no_cache_env(env)["PYTEST_ADDOPTS"] == "-x -p no:cacheprovider"


def test_env_for_has_both(monkeypatch):
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    env = sandbox.env_for()
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTEST_ADDOPTS"] == "-p no:cacheprovider"
    assert sandbox.env_for({"PYTEST_ADDOPTS": "-q"})["PYTEST_ADDOPTS"] == "-q"  # extra still overrides


def test_fault_without_helper(tmp_path):
    """The fault: a plain pytest run writes both cache folders."""
    project(tmp_path)
    env = {k: v for k, v in no_cache_env(None).items() if k not in ("PYTHONDONTWRITEBYTECODE", "PYTEST_ADDOPTS")}
    assert run_pytest(tmp_path, env).returncode == 0
    assert {p.name for p in caches(tmp_path)} == set(CACHES)


def test_real_pytest_run_leaves_no_caches(tmp_path):
    project(tmp_path)
    p = run_pytest(tmp_path, no_cache_env(None))
    assert p.returncode == 0, p.stdout + p.stderr
    assert caches(tmp_path) == []


@pytest.mark.parametrize("given", [None, {"PYTEST_ADDOPTS": "-x"}])
def test_spawn_passes_the_env(monkeypatch, tmp_path, given):
    seen = {}

    class Stop(Exception):
        pass

    def fake_popen(cmd, **kw):
        seen.update(kw)
        raise Stop

    monkeypatch.setattr(executors.subprocess, "Popen", fake_popen)
    with pytest.raises(Stop):
        executors.Executor._spawn(object(), ["true"], None, tmp_path, env=given)
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "-p no:cacheprovider" in seen["env"]["PYTEST_ADDOPTS"]
    if given:
        assert seen["env"]["PYTEST_ADDOPTS"] == "-x -p no:cacheprovider"
