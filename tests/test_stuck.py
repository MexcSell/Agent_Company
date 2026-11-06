import json
import os
import stat
import sys

from agent_company import executors, prefs, router
from agent_company.config import settings


def _use(i, cmd="pytest -q", tid=None):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid or f"t{i}", "name": "Bash", "input": {"command": cmd}}]}})


def _err(tid, text):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": text}]}})


def test_the_watch_names_a_run_that_repeats_or_fails_the_same_way():
    w = executors.StuckWatch("claude", 5, 3)
    assert not any(w.feed(_use(i)) for i in range(4))
    assert w.feed(_use(9, "ls")) == "", "a different call breaks the streak"
    assert not any(w.feed(_use(10 + i)) for i in range(4))
    assert w.feed(_use(20)).startswith("stuck: Bash repeated 5 times")
    e = executors.StuckWatch("claude", 0, 3)
    for i in range(2):
        e.feed(_use(i, "make", tid=f"m{i}")), e.feed(_use(i, "ls", tid=f"l{i}"))
        assert e.feed(_err(f"m{i}", "No rule to make target")) == ""
    e.feed(_use(5, "make", tid="m5"))
    assert e.feed(_err("m5", "No rule to make target")) == "stuck: Bash failed 3 times with the same error"
    c = executors.StuckWatch("codex", 3, 0)
    item = json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "npm test", "exit_code": 1}})
    assert [c.feed(item) for _ in range(3)][-1].startswith("stuck: command repeated 3 times")
    assert not executors.StuckWatch("claude", 0, 0).active


def test_a_stuck_claude_code_run_is_ended_and_fails_with_the_reason(tmp_path, monkeypatch):
    # A fake `claude` that loops on the same call: the watch ends it long before its 60 s sleep.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    lines = "\n".join(_use(i) for i in range(10))
    script.write_text(f"#!{sys.executable}\nimport sys, time\nfor l in {lines.splitlines()!r}:\n"
                      "    print(l, flush=True)\n    time.sleep(0.05)\ntime.sleep(60)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    wt = settings().worktrees_dir / "stuck"
    wt.mkdir(parents=True)
    ex = executors.ClaudeCodeExecutor(router.Choice("Claude Code", "claude_code", "", "test"), timeout=30)
    res = ex.run(executors.Request(purpose="task", role="developer", system="s", prompt="p", workdir=wt))
    assert not res.ok and res.error.startswith("stuck: Bash repeated 5 times") and res.duration < 20
    assert len(res.tool_calls) >= 5
    # Off: the same stream runs to its end (here: the timeout).
    prefs.set_value("stuck_repeat_limit", 0)
    prefs.set_value("stuck_error_limit", 0)
    try:
        assert not executors.stuck_watch("claude").active
    finally:
        prefs.set_value("stuck_repeat_limit", 5)
        prefs.set_value("stuck_error_limit", 3)
