"""EL-R-05: a writing Claude Code run gets a PostToolUse hook that parses each edited file and returns a syntax error
to the agent (exit 2); nothing is run, paths outside the working directory are ignored."""

import json
import subprocess
import sys
from pathlib import Path

from agent_company import edit_check, executors

SCRIPT = Path(edit_check.__file__)


def _hook(tmp_path, rel):
    event = {"tool_name": "Edit", "tool_input": {"file_path": rel}, "cwd": str(tmp_path)}
    return subprocess.run([sys.executable, "-I", str(SCRIPT)], input=json.dumps(event), capture_output=True,
                          text=True, cwd=tmp_path)


def test_a_syntax_error_goes_back_to_the_agent(tmp_path):
    (tmp_path / "good.py").write_text("x = 1\n")
    (tmp_path / "bad.py").write_text("def f(:\n    pass\n")
    assert _hook(tmp_path, "good.py").returncode == 0
    out = _hook(tmp_path, "bad.py")
    assert out.returncode == 2 and "bad.py:1" in out.stderr and "SyntaxError" in out.stderr
    assert not (tmp_path / "__pycache__").exists(), "nothing is written into the worktree"
    outside = tmp_path.parent / "elsewhere.py"
    outside.write_text("def f(:\n")
    assert _hook(tmp_path, str(outside)).returncode == 0, "only files inside the working directory are checked"
    assert _hook(tmp_path, "notes.md").returncode == 0


def test_the_hook_runs_after_edits_and_writes():
    hook = executors.EDIT_CHECK_SETTINGS["hooks"]["PostToolUse"][0]
    assert hook["matcher"] == "Edit|Write|MultiEdit" and "edit_check.py" in hook["hooks"][0]["command"]
