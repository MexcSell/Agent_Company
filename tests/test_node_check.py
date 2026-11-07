"""EL-R-05, goal 86: the JS syntax check (`node --check`) for developer and QA runs, only through the platform's
`edit_check.py` wrapper. A read-only run with `shell` at G2+ gets the check list and never Edit/Write."""

import json
import os
import shutil
import subprocess
import sys
from glob import glob
from pathlib import Path

import pytest

from agent_company import executors, router
from agent_company.config import settings

CHECK = f"Bash({executors.CHECK_CMD}:*)"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node is not on PATH")


class Proc:
    returncode = 0
    stderr = ""
    stdout = json.dumps({"result": "done", "session_id": "", "usage": {}, "total_cost_usd": 0.0})


@pytest.fixture
def claude(monkeypatch):
    calls = []

    def fake_spawn(self, cmd, req, cwd, env=None, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(executors.Executor, "_spawn", fake_spawn)
    return calls


@pytest.fixture
def wt():
    path = settings().worktrees_dir / "nc"
    path.mkdir(parents=True)
    return path


def allowed(cmd):
    return cmd[cmd.index("--allowedTools") + 1].split(",")


def run(wt, role, perms, autonomy="G3", writes=False):
    req = executors.Request(purpose="review" if role == "qa" else "task", role=role, system="s", prompt="p",
                            workdir=wt, writes=writes, permissions=perms, autonomy=autonomy)
    return executors.ClaudeCodeExecutor(router.Choice("Claude Code", "claude_code", "", "test")).run(req)


def test_the_check_entry_is_granted_to_writing_developers_and_read_only_qa(claude, wt):  # (a)
    assert executors.CHECK_OK, "the server's path is usable in a rule"
    dev = run(wt, "developer", ["read", "write", "shell"], writes=True)
    assert CHECK in allowed(claude[0]) and "Bash(uv run:*)" in allowed(claude[0]) and dev.isolation == "L2"
    qa = run(wt, "qa", ["read", "shell"])
    tools = allowed(claude[1])
    assert CHECK in tools and "Bash(uv run pytest:*)" in tools
    assert executors.CHECK_CMD in claude[1][claude[1].index("--append-system-prompt") + 1], "the agent is told the path"
    assert qa.isolation == "L1" and qa.permissions == ["read", "shell"] and qa.ok


def test_a_read_only_grant_has_no_edit_or_write(claude, wt):  # (b)
    req = executors.Request(purpose="review", role="qa", system="s", prompt="p", workdir=wt,
                            permissions=["read", "write", "shell"], autonomy="G4")
    grant = executors.gate(req, "claude_code")
    assert grant.checks and not grant.shell and not grant.writes
    run(wt, "qa", ["read", "write", "shell"], autonomy="G4")
    tools = allowed(claude[0])
    assert not {"Edit", "Write", "MultiEdit"} & set(tools)
    assert "Bash(uv run:*)" not in tools and "Bash(npm run:*)" not in tools and "Bash(npm test:*)" not in tools
    assert "--permission-mode" not in claude[0] and "--settings" not in claude[0]
    assert executors.CopilotExecutor.tools(None, grant) == executors.COPILOT_CHECK
    assert "write" not in executors.COPILOT_CHECK and "shell(uv run)" not in executors.COPILOT_CHECK


def test_no_shell_permission_or_g1_gives_no_node_entry(claude, wt):  # (c)
    run(wt, "qa", ["read"])
    g1 = run(wt, "qa", ["read", "shell"], autonomy="G1")
    run(wt, "developer", ["read", "write"], writes=True)
    for cmd in claude:
        assert not any(t.startswith("Bash(") for t in allowed(cmd))
        assert executors.CHECK_CMD not in cmd[cmd.index("--append-system-prompt") + 1]
    assert "checks: autonomy G1" in g1.dropped
    none = executors.gate(executors.Request(purpose="review", role="qa", system="s", prompt="p", workdir=wt), "copilot")
    assert not none.checks and executors.CopilotExecutor.tools(None, none) == []


def test_no_rule_runs_node_directly():
    assert CHECK in executors.CLAUDE_BASH and CHECK in executors.CLAUDE_CHECK
    assert f"shell({executors.CHECK_CMD})" in executors.COPILOT_SHELL
    assert not any(t.startswith("Bash(node") for t in executors.CLAUDE_BASH + executors.CLAUDE_CHECK)
    assert not any(t.startswith("shell(node") for t in executors.COPILOT_SHELL + executors.COPILOT_CHECK)
    assert executors.EDIT_CHECK == Path(executors.__file__).with_name("edit_check.py") and executors.EDIT_CHECK.is_file()


def test_git_diff_output_is_denied_to_read_only_runs(claude, wt):
    run(wt, "qa", ["read", "shell"])
    run(wt, "developer", ["read", "write", "shell"], writes=True)
    qa, dev = claude
    assert qa[qa.index("--disallowedTools") + 1].split(",") == ["Bash(git diff --output:*)"]
    assert "--disallowedTools" not in dev
    assert executors.COPILOT_CHECK_DENY == ["shell(git diff --output)"]


def test_copilot_read_only_run_gets_only_the_check_shell(monkeypatch, wt):  # design §5 test 5
    seen = []

    def spawn(self, cmd, req, cwd, env=None, **kw):
        seen.append(cmd)
        return type("P", (), {"returncode": 0, "stdout": "done", "stderr": ""})()

    monkeypatch.setattr(executors.Executor, "_spawn", spawn)
    ex = executors.for_choice(router.Choice(name="GitHub Copilot", kind="copilot", model_id="", reason="t"))
    req = executors.Request(purpose="review", role="qa", system="s", prompt="p", workdir=wt,
                            permissions=["read", "shell"], autonomy="G2")
    req.grant = executors.gate(req, "copilot")
    assert req.grant.checks and not req.grant.writes and not req.grant.shell
    assert ex._run(req).ok
    cmd = seen[-1]
    pairs = list(zip(cmd, cmd[1:]))
    allows = [b for a, b in pairs if a == "--allow-tool"]
    denies = [b for a, b in pairs if a == "--deny-tool"]
    assert f"shell({executors.CHECK_CMD})" in allows and "write" not in allows and "shell(uv run)" not in allows
    assert not any(t.startswith("shell(node") for t in allows)
    assert "shell(git push)" in denies and "shell(git diff --output)" in denies
    assert executors.CHECK_HINT in cmd[2]


def wrapper(cwd, *args, env=None):
    return subprocess.run([sys.executable, "-I", str(executors.EDIT_CHECK), *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60, env=env)


@pytest.fixture
def files(tmp_path):
    (tmp_path / "good.js").write_text("let a = 1;\n")
    (tmp_path / "bad.js").write_text("let = = ;\n")
    (tmp_path / "evil.js").write_text(
        f"require('fs').writeFileSync({json.dumps(str(tmp_path / 'sentinel'))}, 'ran');\n")
    (tmp_path / "notes.md").write_text("# notes\n")
    return tmp_path


@needs_node
def test_the_check_passes_valid_and_fails_invalid_js(files):  # (d)
    good = wrapper(files, "good.js")
    assert good.returncode == 0 and "OK good.js" in good.stdout
    bad = wrapper(files, "bad.js")
    assert bad.returncode == 1 and "bad.js" in bad.stdout and "SyntaxError" in bad.stdout
    assert wrapper(files, "good.js", "bad.js").returncode == 1
    assert "SyntaxError" in executors_check(files / "bad.js") and executors_check(files / "good.js") == ""


def executors_check(path):
    from agent_company import edit_check

    return edit_check.check(str(path))


@needs_node
def test_flags_that_run_code_are_refused(files, tmp_path_factory):  # (e)
    outside = tmp_path_factory.mktemp("outside") / "o.js"
    outside.write_text("let a = 1;\n")
    for args in (["-r", "./evil.js", "good.js"], ["--require=./evil.js", "good.js"], ["--import=./evil.js", "good.js"],
                 ["--loader", "./evil.js", "good.js"], ["-e", "1", "good.js"], [str(outside)], ["../o.js"],
                 ["notes.md"]):
        res = wrapper(files, *args)
        assert res.returncode != 0, args
    assert "option not allowed" in wrapper(files, "-r", "./evil.js").stdout
    assert not (files / "sentinel").exists(), "no refused argument ran code"


@needs_node
def test_node_options_do_not_reach_the_check(files):
    env = {**os.environ, "NODE_OPTIONS": f"--require {files / 'evil.js'}"}
    res = wrapper(files, "good.js", env=env)
    assert res.returncode == 0 and not (files / "sentinel").exists()


def test_the_check_fails_without_node(files, tmp_path_factory):
    empty = tmp_path_factory.mktemp("nopath")
    res = wrapper(files, "good.js", env={"PATH": str(empty)})
    assert res.returncode == 3 and "node" in res.stdout


@needs_node
def test_the_dashboard_scripts_pass_the_check():  # goal 26's check as a regression test
    root = Path(__file__).resolve().parents[1]
    scripts = sorted(glob("src/agent_company/static/*.js", root_dir=root))
    assert scripts
    res = wrapper(root, *scripts)
    assert res.returncode == 0, res.stdout[-2000:]
