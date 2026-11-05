"""L3 isolation of the commands that run agent-written code (SPEC-14 S14-R-07, SELF-EVOLUTION SE-R-22)."""

import shutil

import pytest

from agent_company import config, contracts, loop, policies, prefs, projects, sandbox, worktrees
from agent_company.config import REPO_ROOT
from agent_company.db import Goal, session

real = pytest.mark.skipif(not shutil.which("bwrap"), reason="bubblewrap is not installed")


@pytest.fixture(autouse=True)
def _usable(request):
    if request.node.get_closest_marker("skipif") and not sandbox.available()[0]:
        pytest.skip(f"bubblewrap cannot start a sandbox here: {sandbox.available()[1]}")


def _worktree():
    repo = worktrees.new_workspace("sbx")
    wt, _, _ = worktrees.create(repo, "sbx")
    return wt


def _gone(monkeypatch):
    monkeypatch.setattr(sandbox, "_probe", (False, "bubblewrap (bwrap) is not installed"))


def test_the_command_line_has_no_network_no_capabilities_and_read_only_git():
    wt = _worktree()
    cmd = sandbox.build(["true"], wt, writable=[wt], limits=False)
    assert cmd[0] == "bwrap" and "--unshare-net" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    joined = " ".join(cmd)
    assert f"--bind {wt} {wt}" in joined
    assert joined.index(f"--ro-bind {wt / '.git'} {wt / '.git'}") > joined.index(f"--bind {wt} {wt}")
    common = (worktrees.new_workspace("sbx") / ".git").resolve()
    assert f"--ro-bind {common} {common}" in joined  # hooks live in the common git directory
    assert "--clearenv" in cmd and "--unshare-net" not in sandbox.build(["true"], wt, writable=[wt], network=True,
                                                                            limits=False)
    limited = sandbox.build(["true"], wt, writable=[wt], limits=True)
    assert limited[:2] == ["systemd-run", "--scope"] and "TasksMax=512" in limited and "MemorySwapMax=0" in limited


def test_a_bound_checkout_hides_its_data_and_env():
    cmd = " ".join(sandbox.build(["true"], REPO_ROOT, writable=[], readonly=[REPO_ROOT], limits=False))
    if (REPO_ROOT / ".env").exists():
        assert f"--ro-bind /dev/null {REPO_ROOT / '.env'}" in cmd
    git = REPO_ROOT / ".git"  # a directory in the main checkout, a file in a linked worktree (a goal's)
    assert (f"--tmpfs {git}" if git.is_dir() else f"--ro-bind /dev/null {git}") in cmd


def test_the_environment_never_carries_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("UV_INDEX_TOKEN", "t")
    monkeypatch.setenv("UV_INDEX_URL", "https://pypi.example")
    monkeypatch.setenv("CUSTOM_FLAG", "1")
    env = sandbox.env_for()
    assert "ANTHROPIC_API_KEY" not in env and "UV_INDEX_TOKEN" not in env and "DATA_DIR" not in env
    assert env["UV_INDEX_URL"] == "https://pypi.example" and "CUSTOM_FLAG" not in env
    prefs.set_value("sandbox_env", ["CUSTOM_FLAG"])
    assert sandbox.env_for()["CUSTOM_FLAG"] == "1"
    with pytest.raises(ValueError, match="credentials"):
        prefs.set_value("sandbox_env", ["MY_API_KEY"])
    with pytest.raises(ValueError, match="data directory"):
        prefs.set_value("sandbox_paths", [str(config.settings().data_dir.parent)])


def test_required_mode_refuses_to_run_without_the_sandbox(monkeypatch):
    _gone(monkeypatch)
    wt = _worktree()
    info = {}
    ok, detail = contracts.run_command("touch ran.txt", wt, 30, mode="required", info=info)
    assert not ok and "not run" in detail and "bwrap" in detail and info["isolation"] == ""
    assert not (wt / "ran.txt").exists()


def test_auto_mode_falls_back_to_the_host_and_says_so(monkeypatch):
    _gone(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    wt = _worktree()
    info = {}
    ok, detail = contracts.run_command('test -z "$OPENAI_API_KEY" && touch ran.txt', wt, 30, mode="auto", info=info)
    assert ok and (wt / "ran.txt").exists() and info["isolation"] == "L2"
    assert "(L2)" in detail and "ran on the host: bubblewrap (bwrap) is not installed" in detail


@real
def test_a_sandboxed_check_cannot_reach_the_network_the_data_or_git():
    wt = _worktree()
    secret = config.settings().data_dir / "secret.txt"
    secret.write_text("company secret")
    hooks = (worktrees.new_workspace("sbx") / ".git" / "hooks").resolve()
    probe = (f"test ! -e {secret} && test ! -e {config.settings().data_dir / 'agent_company.db'} "
             f"&& git status --short >/dev/null && test -d {hooks} && ! touch {hooks}/pre-commit 2>/dev/null "
             "&& ! (echo 'gitdir: /tmp/x' > .git) 2>/dev/null && python3 -c \"import socket\ntry:\n socket.create_connection(('1.1.1.1', 80), 2)\n"
             "except OSError: pass\nelse: raise SystemExit('network reachable')\" && echo inside > out.txt "
             "&& test \"$HOME\" != /tmp && touch $HOME/cache-ok")
    info = {}
    ok, detail = contracts.run_command(probe, wt, 60, mode="required", info=info)
    assert ok, detail
    assert info["isolation"] == "L3" and "(L3)" in detail
    assert (wt / "out.txt").read_text() == "inside\n"  # the worktree itself is writable
    assert (sandbox.home() / "cache-ok").exists()  # HOME is the sandbox's own, not the server user's
    assert not (hooks / "pre-commit").exists() and (wt / ".git").read_text().startswith("gitdir: /")


@real
def test_the_prepare_step_runs_once_per_lock_file(monkeypatch):
    monkeypatch.setattr(sandbox, "PREPARE", {"deps.lock": ("sh", "mkdir -p deps && echo x >> prepared.txt", "deps")})
    wt = _worktree()
    (wt / "deps.lock").write_text("v1")
    for _ in range(2):
        ok, detail = contracts.run_command("test -f prepared.txt", wt, 60, mode="required")
        assert ok, detail
    assert (wt / "prepared.txt").read_text() == "x\n"
    (wt / "deps.lock").write_text("v2")
    contracts.run_command("true", wt, 60, mode="required")
    assert (wt / "prepared.txt").read_text() == "x\nx\n"
    (wt / "deps").rmdir()  # what it installed is gone (a recreated checkout): prepare again
    contracts.run_command("true", wt, 60, mode="required")
    assert (wt / "prepared.txt").read_text() == "x\nx\nx\n"


@real
def test_the_gate_records_the_isolation_of_every_command():
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    rows = [c for c in out["report"]["checks"] if c["check"].startswith("command")]
    assert rows and all(c["isolation"] == "L3" for c in rows)
    with session() as s:
        assert all(r["isolation"] == "L3" for r in s.get(Goal, gid).baseline.values())


def test_projects_may_only_make_the_sandbox_stricter_and_a2a_work_always_requires_it():
    p = projects.create("Untrusted input")
    with pytest.raises(ValueError, match="weaker"):
        policies.set_project_rules(p["id"], {"sandbox": "off"}, "user:luca")
    assert policies.set_project_rules(p["id"], {"sandbox": "required"}, "user:luca")["own"] == {"sandbox": "required"}
    gid = loop.create_goal("Inside", quality_commands=[], project_id=p["id"])
    other = loop.create_goal("Outside", quality_commands=[])
    with session() as s:
        assert sandbox.mode_for(s.get(Goal, gid)) == "required"
        assert sandbox.mode_for(s.get(Goal, other)) == "auto"
        s.get(Goal, other).origin = "a2a"
        assert sandbox.mode_for(s.get(Goal, other)) == "required"
    prefs.set_value("sandbox", "off")
    assert sandbox.mode_for(project_id=p["id"]) == "required"
    assert "sandbox" in policies.GOVERNANCE_KEYS
