"""The GM's company tools: MCP catalog, skills/plugins catalog, agent definition files, hooks, emergency stop."""

import json
import sys

import pytest
from fastapi.testclient import TestClient

from agent_company import agentdefs, catalog, executors, governance, hooks, loop, mcp, registry, workspace
from agent_company.api import _index_html, create_app
from agent_company.db import Agent, Goal, session


def test_bundled_stdio_server_lists_tools_and_starts_read_only():
    srv = mcp.save_server({"name": "Papers", "transport": "stdio", "command": "python", "args": ["papers.py"],
                           "env": {"S2_API_KEY": "secret-value"}}, "test")
    assert srv["env"] == ["S2_API_KEY"] and "secret-value" not in json.dumps(srv), "values never leave the server"
    out = mcp.test(srv["id"])
    assert out["status"] == "ok" and out["tools"], "initialize + tools/list over stdio"
    assert all(t["allowed"] == t["read_only"] for t in out["tools"]), "only read-only tools start allowed"
    first = out["tools"][0]["name"]
    mcp.set_tools(srv["id"], [])
    with pytest.raises(PermissionError):
        mcp.call(srv["id"], first, {}, "test")


def test_claude_config_gives_an_agent_only_granted_servers():
    srv = mcp.save_server({"name": "DB", "transport": "stdio", "command": "python", "args": ["database.py"],
                           "everyone": False}, "test")
    mcp.test(srv["id"], allow="all")
    dev = registry.list_agents()[2]["id"]
    assert mcp.claude_config(dev) == ({"mcpServers": {}}, [])
    mcp.set_grants(srv["id"], [dev])
    servers, tools = mcp.claude_config(dev)
    assert servers["mcpServers"]["db"]["command"] == sys.executable
    assert servers["mcpServers"]["db"]["args"][0].endswith("mcp_servers/database.py")
    assert tools and all(t.startswith("mcp__db__") for t in tools)


def test_catalog_seeds_the_image_and_exports_claude_config(tmp_path):
    names = {(i["kind"], i["name"]) for i in catalog.list_items()}
    assert {("plugin", "context7"), ("rule", "agent-team-protocol"), ("skill", "spec-workflow"),
            ("command", "brainstorm"), ("hook", "TeammateIdle")} <= names
    assert catalog.ensure("skill", ["kubernetes", "spec-workflow"]) == ["kubernetes"], "only the missing one is added"
    with session() as s:
        gm = s.query(Agent).filter_by(role="general_manager").first().id
    written = catalog.export_claude(tmp_path, [gm])
    assert ".claude/rules/security-guidelines.md" in written and ".claude/skills/spec-workflow/SKILL.md" in written
    assert ".claude/agents/general-manager.md" in written and ".claude/commands/brainstorm.md" in written
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert set(settings["hooks"]) >= {"TaskCreated", "TaskCompleted", "TeammateIdle"}
    assert catalog.export_claude(tmp_path, [gm]) == [], "nothing changes on a second export"


def test_learning_changes_the_agent_definition_file():
    dev = next(a for a in registry.list_agents() if a["role"] == "developer")
    assert agentdefs.sync(dev["id"], "first")
    before = agentdefs.read(dev["id"])
    assert "## Mission" in before["text"] and before["history"]
    with session() as s:
        a = s.get(Agent, dev["id"])
        assert agentdefs.system_prompt(a).startswith("# ")
    from agent_company import learning

    gid = loop.create_goal("Teach")
    learning.learn(dev["id"], gid, "Always run the tests before reporting.", False, ["testing"])
    out = agentdefs.study(dev["id"])
    assert out["changed"] and out["model"] == "Offline simulator"
    after = agentdefs.read(dev["id"])
    assert "Always run the tests before reporting." in agentdefs.sections(after["text"])["Working method"]
    assert len(after["history"]) > len(before["history"]), "every change is a commit in data/agents"


def test_hooks_record_task_and_idle_events():
    p = loop.create_goal("Hooked")
    loop.run_goal(p)
    events = {e["event"] for e in hooks.recent()}
    assert {"TaskCreated", "TaskCompleted", "TeammateIdle"} <= events


def test_emergency_stop_cancels_and_refuses_work():
    gid = loop.create_goal("Stopped")
    out = governance.set_emergency(True, "test", "drill")
    assert gid in out["cancelled_goals"]
    gm = registry.staff("general_manager")
    res, choice = loop._execute(gm, executors.Request(purpose="plan", role="general_manager", system="", prompt="p"), "plan")
    assert not res.ok and "emergency stop" in res.error
    governance.set_emergency(False, "test")
    assert governance.emergency() is None
    with session() as s:
        assert s.get(Goal, gid).status == "cancelled"


def test_codex_events_become_panel_events():
    state = {"cwd": "/x", "t0": 0}
    lines = [{"type": "thread.started", "thread_id": "t-1"},
             {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}},
             {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "ls", "aggregated_output": "a", "exit_code": 0}},
             {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "Done."}},
             {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}]
    kinds = [e["kind"] for raw in lines for e in workspace.normalise_codex(raw, state)]
    assert kinds == ["init", "tool_use", "tool_result", "text", "result"]
    assert state["session_id"] == "t-1"
    assert workspace.normalise({"type": "system", "subtype": "compact_boundary",
                                "compact_metadata": {"pre_tokens": 100, "post_tokens": 10}})[0]["kind"] == "compact"


def test_index_versions_static_files_and_headers_are_set():
    html = _index_html()
    assert "/static/app.js?v=" in html and "/static/style.css?v=" in html
    with TestClient(create_app(start_background=False)) as app:
        r = app.get("/static/app.js?v=abc")
        assert "immutable" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
        assert app.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_goal_leaves_spec_artifacts_and_keeps_them_in_scope():
    import subprocess

    gid = loop.create_goal("Spec me", "Write the worklog", allowed_paths=["WORKLOG.md"])
    out = loop.run_goal(gid)
    with session() as s:
        g = s.get(Goal, gid)
        wt, title = g.worktree, g.title
    files = subprocess.run(["git", "ls-files", ".claude/specs"], cwd=wt, capture_output=True, text=True).stdout
    for name in ("requirements.md", "tasks.md", "spec.md", "review.md", "decisions.md"):
        assert f"/{name}" in files, name
    assert out["status"] in {"awaiting_approval", "approved"}, "spec files never break the scope check"
