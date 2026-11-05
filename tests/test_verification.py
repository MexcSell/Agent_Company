"""ASEAO09 S09-R-04: the gate re-runs the check commands itself and keeps the evidence."""

from agent_company import contracts, loop, quality


def test_gate_records_its_own_command_runs():
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md", "[integration] echo checked"])
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    rows = [c for c in out["report"]["checks"] if c.get("command")]
    assert [r["command"] for r in rows] == ["test -f WORKLOG.md", "echo checked"]
    for r in rows:
        assert r["exit_code"] == 0 and r["timed_out"] is False and r["duration_s"] >= 0
        assert r["commit"] == out["report"]["head"] and len(r["commit"]) == 40
    assert "checked" in rows[1]["output_tail"]
    h = g.history[-1]
    assert [v["command"] for v in h["verification"]] == ["test -f WORKLOG.md", "echo checked"]
    assert h["verification"][1]["stage"] == "integration" and h["verification"][0]["passed"] is True
    assert "platform" in h["environment"] and h["environment"].get("python", "").startswith("Python 3")


def test_failing_and_refused_commands_keep_their_exit(tmp_path, env):
    from agent_company.config import settings

    wt = settings().worktrees_dir / "w"
    wt.mkdir(parents=True)
    info: dict = {}
    ok, _ = contracts.run_command("exit 3", wt, 30, info=info)
    assert not ok and info["exit_code"] == 3 and info["timed_out"] is False
    info = {}
    ok, _ = contracts.run_command("rm -rf /", wt, 30, info=info)
    assert not ok and info["exit_code"] is None and info["output_tail"] == ""


def test_no_commands_no_environment_probe():
    gid = loop.create_goal("Write notes", quality_commands=[])
    loop.run_goal(gid)
    h = loop._goal(gid).history[-1]
    assert h["verification"] == [] and h["environment"] == {}
    assert quality.environment() is quality.environment()  # probed once per process
