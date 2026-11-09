"""G-R-05 least privilege: a new MCP server is offered to no agent until an administrator grants it."""

from agent_company import mcp, registry
from agent_company.api import McpServerBody
from agent_company.db import McpServer, session

TOOLS = [{"name": "read", "description": "Read one issue.", "annotations": {"readOnlyHint": True}}]


class FakeSession:
    def __init__(self, server, secrets):  # noqa: ANN001
        pass

    def tools(self) -> list[dict]:
        return [dict(t) for t in TOOLS]

    def close(self) -> None:
        pass


def _agents() -> tuple[int, int]:
    ids = [a["id"] for a in registry.list_agents()]
    return ids[1], ids[2]


def _server(monkeypatch, **extra) -> int:  # noqa: ANN001
    monkeypatch.setattr(mcp, "Session", FakeSession)
    srv = mcp.save_server({"name": "Tracker", "transport": "http", "url": "https://mcp.example.test/mcp", **extra}, "test")
    out = mcp.test(srv["id"])
    assert out["enabled"] and out["status"] == "ok" and any(t["allowed"] for t in out["tools"])
    return srv["id"]


def _offered(agent_id: int) -> set[str]:
    return set(mcp.claude_config(agent_id)[0]["mcpServers"])


def test_a_new_server_is_offered_to_no_agent(monkeypatch):
    sid = _server(monkeypatch)
    assert mcp.get_server(sid)["everyone"] is False
    a, b = _agents()
    assert _offered(a) == set() and _offered(b) == set(), "enabled and tested, but granted to nobody"


def test_a_catalog_server_is_offered_to_no_agent(monkeypatch):
    monkeypatch.setattr(mcp, "Session", FakeSession)
    srv = mcp.add_from_catalog("deepwiki", {}, "test")  # no fields to fill
    assert srv["everyone"] is False and srv["grants"] == []
    assert _offered(_agents()[0]) == set()


def test_a_grant_offers_it_to_that_agent_only(monkeypatch):
    sid = _server(monkeypatch)
    a, b = _agents()
    mcp.set_grants(sid, [a])
    assert _offered(a) == {"tracker"} and _offered(b) == set()


def test_everyone_true_still_opens_it_to_all(monkeypatch):
    sid = _server(monkeypatch, everyone=True)
    a, b = _agents()
    assert _offered(a) == {"tracker"} and _offered(b) == {"tracker"}
    mcp.set_grants(sid, [], everyone=False)
    assert _offered(a) == set()
    mcp.set_grants(sid, [], everyone=True)
    assert _offered(a) == {"tracker"} and _offered(b) == {"tracker"}


def test_the_api_body_defaults_to_no_agent():
    assert McpServerBody(name="x").everyone is False


def test_editing_an_open_server_without_the_key_keeps_it_open(monkeypatch):
    sid = _server(monkeypatch)
    with session() as s:  # a row stored under the old default
        s.get(McpServer, sid).everyone = True
    out = mcp.save_server({"name": "Tracker", "transport": "http", "url": "https://mcp.example.test/v2"}, "test", sid)
    assert out["everyone"] is True, "existing rows are not changed"
