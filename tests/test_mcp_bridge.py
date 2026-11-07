"""EL-R-09: the bridge MCP server. A granted run reaches only its own goal's blackboard; other goals, ungranted agents
and dead tokens are refused, and every call is an McpCall."""

import json
import os
import secrets
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import blackboard, bridge, mcp, registry
from agent_company.api import create_app
from agent_company.db import Goal, McpCall, McpServer, Task, session

TEXT = "Wrote the bridge module; tests next."


@pytest.fixture
def benv():
    registry.seed()
    ids = {}
    with session() as s:
        for n in (1, 2):
            g = Goal(title=f"Goal {n} title", description=f"goal {n}")
            s.add(g)
            s.flush()
            t = Task(goal_id=g.id, iteration=1, key="t1", title=f"Task of goal {n}", role="developer",
                     contract={"description": f"Build part {n}.", "acceptance": [{"type": "min_length", "value": 20 + n}]})
            s.add(t)
            s.flush()
            ids[f"g{n}"], ids[f"t{n}"] = g.id, t.id
    agents = [a["id"] for a in registry.list_agents()]
    ids["a"], ids["b"] = agents[1], agents[2]
    srv = mcp.add_from_catalog("bridge", {}, "test")  # starts the real shim: initialize + tools/list need no token
    assert srv["status"] == "ok" and srv["everyone"] is False
    assert sorted(t["name"] for t in srv["tools"]) == sorted(bridge.TOOLS) and all(t["allowed"] for t in srv["tools"])
    mcp.set_grants(srv["id"], [ids["a"]])
    ids["srv"] = srv["id"]
    ids["client"] = TestClient(create_app(start_background=False), client=("127.0.0.1", 50000))
    return ids


def call(benv,token, tool, args=None, headers=None):  # noqa: ANN001
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return benv["client"].post("/api/bridge/call", json={"tool": tool, "arguments": args or {}}, headers={**h, **(headers or {})})


def calls(**where):
    with session() as s:
        q = select(McpCall).where(McpCall.actor == "bridge")
        for k, v in where.items():
            q = q.where(getattr(McpCall, k) == v)
        return [(c.tool, c.ok, c.agent_id, c.server_id, c.error) for c in s.scalars(q.order_by(McpCall.id))]


def progress(goal_id):
    return [e for e in blackboard.read(goal_id) if e["key"].startswith("progress:")]


def test_granted_post_progress_reaches_its_goal_blackboard_and_is_logged(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["a"])
    r = call(benv,tok, "post_progress", {"text": TEXT, "percent": 40})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "text": "posted engineering/progress:t1 v1"}
    [e] = progress(benv["g1"])
    assert (e["section"], e["content"], e["author_agent_id"], e["task_id"]) == ("engineering", TEXT, benv["a"], benv["t1"])
    assert e["meta"]["via"] == "bridge" and e["meta"]["percent"] == 40
    assert progress(benv["g2"]) == [] and TEXT not in json.dumps(blackboard.read(benv["g2"]))
    assert ("post_progress", True, benv["a"], benv["srv"], "") in calls()
    assert call(benv,tok, "post_progress", {"text": "again"}).json()["text"].endswith("v2")


def test_a_call_naming_another_goal_or_task_is_refused(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["a"])
    r = call(benv,tok, "post_progress", {"text": TEXT, "goal_id": benv["g2"]})
    assert r.status_code == 403 and "goal" in r.json()["error"]
    r = call(benv,tok, "post_progress", {"text": TEXT, "task_id": benv["t2"]})
    assert r.status_code == 403 and "task" in r.json()["error"]
    assert progress(benv["g1"]) == [] and progress(benv["g2"]) == []
    refused = calls(ok=False)
    assert len(refused) == 2 and all(("goal" in c[4] or "task" in c[4]) and c[2] == benv["a"] for c in refused)
    # its own ids are fine
    assert call(benv,tok, "post_progress", {"text": TEXT, "goal_id": benv["g1"], "task_id": benv["t1"]}).status_code == 200


def test_an_ungranted_agent_is_refused_until_granted(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["b"])
    r = call(benv,tok, "post_progress", {"text": TEXT})
    assert r.status_code == 403 and "grant" in r.json()["error"]
    assert calls(ok=False, agent_id=benv["b"]) and progress(benv["g1"]) == []
    mcp.set_grants(benv["srv"], [], everyone=True)
    assert call(benv,tok, "post_progress", {"text": TEXT}).status_code == 200
    mcp.set_grants(benv["srv"], [], everyone=False)  # taken away mid-run: checked per call
    assert call(benv,tok, "post_progress", {"text": "more"}).status_code == 403


def test_revoked_expired_unknown_and_missing_tokens_are_refused(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["a"])
    assert call(benv,tok, "get_task").status_code == 200
    bridge.revoke(tok)
    r = call(benv,tok, "post_progress", {"text": TEXT})
    assert r.status_code == 401 and "revoked" in r.json()["error"] and "Bearer" in r.headers["www-authenticate"]
    old = bridge.issue(benv["g1"], benv["t1"], benv["a"], ttl=-1)
    r = call(benv,old, "post_progress", {"text": TEXT})
    assert r.status_code == 401 and "expired" in r.json()["error"]
    assert call(benv,"acb_" + secrets.token_urlsafe(32), "post_progress", {"text": TEXT}).status_code == 401
    assert call(benv,None, "post_progress", {"text": TEXT}).status_code == 401
    refused = calls(ok=False)
    assert [c[2] for c in refused] == [benv["a"], benv["a"], None, None]
    assert progress(benv["g1"]) == []
    assert not any(tok in c[4] or old in c[4] for c in refused), "a token never lands in the log"


def test_the_shim_lists_its_tools_without_a_token():
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "post_progress", "arguments": {"text": "x"}}}]
    p = subprocess.run([sys.executable, "-I", str(mcp.BUNDLED / "bridge.py")], input="".join(json.dumps(m) + "\n" for m in lines),
                       env={"PATH": os.environ["PATH"]}, capture_output=True, text=True, timeout=30)
    out = {m["id"]: m for m in map(json.loads, p.stdout.splitlines())}
    assert out[1]["result"]["serverInfo"]["name"] == "agent-company-bridge"
    tools = {t["name"]: t for t in out[2]["result"]["tools"]}
    assert sorted(tools) == sorted(bridge.TOOLS)
    assert [n for n, t in tools.items() if t["annotations"]["readOnlyHint"]] == ["get_task"]
    assert out[3]["result"]["isError"] and "token" in out[3]["result"]["content"][0]["text"]


def test_disabled_server_and_unallowed_tool_are_refused(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["a"])
    with session() as s:
        s.get(McpServer, benv["srv"]).enabled = False
    assert call(benv,tok, "post_progress", {"text": TEXT}).status_code == 403
    with session() as s:
        s.get(McpServer, benv["srv"]).enabled = True
    mcp.set_tools(benv["srv"], ["get_task"])
    assert call(benv,tok, "post_progress", {"text": TEXT}).status_code == 403
    assert call(benv,tok, "get_task").status_code == 200
    assert call(benv,tok, "approve_goal").status_code == 403
    assert progress(benv["g1"]) == []


def test_get_task_returns_only_the_token_task(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["a"])
    r = call(benv,tok, "get_task")
    assert r.status_code == 200
    text = r.json()["text"]
    got = json.loads(text)
    assert got["task"]["title"] == "Task of goal 1" and got["goal"]["title"] == "Goal 1 title"
    assert got["task"]["acceptance"] == [{"type": "min_length", "value": 21}]
    assert "goal 2" not in text.lower() and "part 2" not in text


def test_forwarded_and_oversized_calls_are_refused(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["a"])
    assert call(benv,tok, "post_progress", {"text": TEXT}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert call(benv,tok, "post_progress", {"text": "x" * 4001}).status_code == 400
    assert call(benv,tok, "post_progress", {"text": TEXT, "section": "planning"}).status_code == 400
    assert call(benv,tok, "post_progress", {"text": TEXT, "goal_id": True}).status_code == 400
    assert benv["client"].post("/api/bridge/call", content=b"x" * (bridge.MAX_BODY + 1),
                              headers={"Authorization": f"Bearer {tok}"}).status_code == 413
    assert benv["client"].post("/api/bridge/call", content=b"[1]", headers={"Authorization": f"Bearer {tok}"}).status_code == 400
    assert progress(benv["g1"]) == [] and progress(benv["g2"]) == []
    assert len(calls(ok=False)) == 6


def test_insights_and_review_requests_stay_on_the_goal_within_their_budgets(benv):
    tok = bridge.issue(benv["g1"], benv["t1"], benv["a"])
    for n in range(bridge.INSIGHTS_PER_TASK):
        assert call(benv,tok, "save_insight", {"title": f"Lesson {n}", "text": "Detail", "evidence": ["log"]}).status_code == 200
    assert call(benv,tok, "save_insight", {"title": "One more", "text": "Detail"}).status_code == 429
    for _ in range(bridge.REVIEWS_PER_TASK):
        assert call(benv,tok, "request_review", {"summary": "Check it", "files": ["src/x.py"]}).status_code == 200
    assert call(benv,tok, "request_review", {"summary": "Again"}).status_code == 429
    board = {(e["section"], e["key"]): e for e in blackboard.read(benv["g1"])}
    assert ("reports", "insight:t1:5") in board and ("messages", "t1:review:2") in board
    assert board[("messages", "t1:review:1")]["meta"]["to"] == "qa"
    assert "Check it" in blackboard.inbox(benv["g1"], "qa", benv["b"])
    assert blackboard.read(benv["g2"]) == []
