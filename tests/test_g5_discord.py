"""G5 UI-G5-KN-02: Discord as an external knowledge source (discord.py), against a stubbed Discord API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, discord, research
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import AuditEvent, DiscordConnector, Document, Event, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "member", "is_admin": False, "permissions": dict(DEFAULT_PERMISSIONS)}
TOKEN = "bot-token-abcd1234"
GUILD, TEXT, FORUM_ID, THREAD = "143276403741805787", "147088244485193761", "147088244485193799", "150000000000000001"


class FakeDiscord:
    """Answers the paths discord.py asks for; `status` overrides one path's status."""

    def __init__(self):
        self.status: dict[str, int] = {}
        self.flags = 1 << 19
        self.guilds = [{"id": GUILD, "name": "Ridges"}]
        self.messages = {
            TEXT: [{"id": str(1000 + i), "content": f"How do I fix the CUDA error {i}?", "timestamp":
                    "2026-11-09T10:00:00+00:00", "author": {"id": "77", "username": "miner7"}, "attachments": []}
                   for i in range(3)],
            THREAD: [{"id": "2000", "content": "Use docker with --gpus all", "timestamp": "2026-11-09T11:00:00+00:00",
                      "author": {"id": "88", "username": "op"}, "message_reference": {"message_id": "1999"},
                      "attachments": [{"filename": "log.txt", "url": "https://cdn.discordapp.com/x/log.txt"}]}],
        }
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, token, path, params=None):
        self.calls.append((path, dict(params or {})))
        if token != TOKEN:
            return 401, {"message": "401: Unauthorized"}, {}
        for prefix, code in self.status.items():
            if path.startswith(prefix):
                return code, {"message": "Missing Access", "retry_after": 3}, {}
        if path == "/users/@me":
            return 200, {"id": "1", "username": "axor-bot"}, {}
        if path == "/oauth2/applications/@me":
            return 200, {"flags": self.flags}, {}
        if path == "/users/@me/guilds":
            return 200, self.guilds, {}
        if path == f"/guilds/{GUILD}/channels":
            return 200, [{"id": TEXT, "name": "general", "type": 0}, {"id": FORUM_ID, "name": "discussions", "type": 15},
                         {"id": "5", "name": "voice", "type": 2}], {}
        if path == f"/guilds/{GUILD}/threads/active":
            return 200, {"threads": [{"id": THREAD, "name": "GPU setup", "parent_id": FORUM_ID}]}, {}
        if path.endswith("/threads/archived/public"):
            return 200, {"threads": []}, {}
        if path.startswith("/channels/") and path.endswith("/messages"):
            where = path.split("/")[2]
            after = int((params or {}).get("after") or 0)
            return 200, [m for m in self.messages.get(where, []) if int(m["id"]) > after], {}
        return 404, {"message": "Unknown"}, {}


@pytest.fixture
def fake(monkeypatch):
    f = FakeDiscord()
    monkeypatch.setattr(discord, "_request", f)
    return f


def client(user):
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: user
    return TestClient(app)


def connect(c):
    made = c.post("/api/discord", json={"name": "Ridges", "token": TOKEN, "guild_id": GUILD}).json()
    c.post(f"/api/discord/{made['id']}/test")
    return c.patch(f"/api/discord/{made['id']}", json={"channels": [TEXT, FORUM_ID], "agent_access": "all"}).json()


def test_the_token_is_stored_encrypted_and_never_returned(fake):
    c = client(ADMIN)
    out = c.post("/api/discord", json={"name": "Ridges", "token": TOKEN, "guild_id": GUILD}).json()
    assert out["token_hint"] == "…1234" and out["has_token"] and "token_enc" not in out
    with session() as s:
        row = s.get(DiscordConnector, out["id"])
        assert row.token_enc and TOKEN not in row.token_enc
        audits = [a for a in s.scalars(select(AuditEvent)) if a.action == "discord_create"]
        assert audits and TOKEN not in str(audits[0].after)
    assert TOKEN not in c.get("/api/discord").text
    assert client(MEMBER).get("/api/discord").status_code == 403


def test_test_discovers_servers_and_channels_and_says_connected(fake):
    c = client(ADMIN)
    cid = c.post("/api/discord", json={"name": "R", "token": TOKEN, "guild_id": GUILD}).json()["id"]
    out = c.post(f"/api/discord/{cid}/test").json()
    assert out["state"] == "connected" and out["guild_name"] == "Ridges"
    assert [x["type"] for x in out["discovered"]["channels"]] == ["text", "forum"], "voice channels are left out"
    assert c.patch(f"/api/discord/{cid}", json={"channels": ["999999"]}).status_code == 400, "only discovered ones"


@pytest.mark.parametrize("case, state", [("token", "invalid_token"), ("guild", "no_access"),
                                         ("intent", "missing_intent"), ("channels", "no_access")])
def test_test_reports_the_access_problem_never_a_success(fake, case, state):
    c = client(ADMIN)
    body = {"name": "R", "token": TOKEN if case != "token" else "wrong", "guild_id": GUILD}
    if case == "guild":
        fake.guilds = []
    if case == "intent":
        fake.flags = 0
    if case == "channels":
        fake.status[f"/guilds/{GUILD}/channels"] = 403
    cid = c.post("/api/discord", json=body).json()["id"]
    out = c.post(f"/api/discord/{cid}/test").json()
    assert out["state"] == state and out["state_detail"]


def test_sync_imports_text_and_forum_threads_incrementally_with_provenance(fake):
    c = client(ADMIN)
    cid = connect(c)["id"]
    out = c.post(f"/api/discord/{cid}/sync").json()
    assert out["ok"] and out["new"] == 4 and out["state"] == "connected"
    with session() as s:
        docs = {d.url: d for d in s.scalars(select(Document).where(Document.source_url.like(f"discord://{cid}/%")))}
        thread_doc = docs[f"https://discord.com/channels/{GUILD}/{THREAD}/2000"]
        assert "Unverified community discussion" in thread_doc.text and "Thread: GPU setup" in thread_doc.text
        assert "Author: op (88)" in thread_doc.text and "log.txt <https://cdn.discordapp.com/x/log.txt>" in thread_doc.text
        assert f"Reply to: https://discord.com/channels/{GUILD}/{THREAD}/1999" in thread_doc.text
        assert thread_doc.published_at is not None and thread_doc.category == "community Q&A"
        assert s.get(DiscordConnector, cid).cursors == {TEXT: "1002", THREAD: "2000"}
    again = c.post(f"/api/discord/{cid}/sync").json()
    assert again["ok"] and again["new"] == 0, "the cursor continues where the last sync ended"
    assert ("/channels/%s/messages" % TEXT, {"limit": 100, "after": "1002"}) in fake.calls
    with session() as s:
        ev = [e for e in s.scalars(select(Event).where(Event.kind == "source_sync"))]
        assert len(ev) == 2 and all(e.data["ok"] for e in ev)
    assert [m["title"] for m in c.get(f"/api/discord/{cid}/messages?q=docker").json()] == ["#discussions / GPU setup · op"]


def test_a_failed_sync_is_recorded_and_alerted_never_reported_as_success(fake):
    from agent_company import command

    c = client(ADMIN)
    cid = connect(c)["id"]
    fake.status[f"/channels/{TEXT}/messages"] = 429
    out = c.post(f"/api/discord/{cid}/sync").json()
    assert not out["ok"] and out["state"] == "rate_limited" and out["not_before"]
    assert out["history"][0]["ok"] is False
    assert any(a["kind"] == "source" for a in command.center(ADMIN)["alerts"])
    unset = client(ADMIN).post("/api/discord", json={"name": "Empty"}).json()
    assert client(ADMIN).post(f"/api/discord/{unset['id']}/sync").json()["state"] == "unconfigured"


def test_messages_without_text_mean_the_intent_is_off(fake):
    c = client(ADMIN)
    cid = connect(c)["id"]
    for msgs in fake.messages.values():
        for m in msgs:
            m["content"], m["attachments"] = "", []
    out = c.post(f"/api/discord/{cid}/sync").json()
    assert not out["ok"] and out["state"] == "missing_intent"


def test_agent_access_decides_who_reads_the_messages(fake):
    c = client(ADMIN)
    cid = connect(c)["id"]
    c.post(f"/api/discord/{cid}/sync")
    assert research.search("docker gpus", audience="agents"), "access all: agents read it"
    c.patch(f"/api/discord/{cid}", json={"agent_access": "research"})
    assert not research.search("docker gpus", audience="agents")
    assert research.search("docker gpus", audience="research")
    c.patch(f"/api/discord/{cid}", json={"agent_access": "none"})
    assert not research.search("docker gpus", audience="research")
    assert research.search("docker gpus", audience="people")
    assert c.delete(f"/api/discord/{cid}").json()["documents_removed"] == 0
    assert not research.search("docker gpus", audience="agents"), "kept messages of a removed source: people only"
    assert research.search("docker gpus", audience="people")


def test_the_schedule_syncs_only_enabled_due_sources(fake):
    c = client(ADMIN)
    cid = connect(c)["id"]
    assert discord.tick() == []
    c.patch(f"/api/discord/{cid}", json={"enabled": True, "interval_minutes": 30})
    assert discord.tick() == [cid]
    assert discord.tick() == [], "not due again before its interval"
    assert c.patch(f"/api/discord/{cid}", json={"interval_minutes": 2}).status_code == 400


def test_topics_are_a_marked_keyword_rule():
    assert discord.topic("Traceback: CUDA error") == "errors and troubleshooting"
    assert discord.topic("anyone running a validator?") in ("miner strategy", "question")
    assert discord.topic("hello") == "discussion"


def test_purge_removes_the_messages_with_their_earlier_versions(fake):
    from agent_company.db import DocumentVersion

    c = client(ADMIN)
    cid = connect(c)["id"]
    c.post(f"/api/discord/{cid}/sync")
    with session() as s:
        doc = s.scalars(select(Document).where(Document.source_url.like(f"discord://{cid}/%"))).first()
        s.add(DocumentVersion(document_id=doc.id, version=1, title="old", text="old", content_hash="x"))
    out = c.delete(f"/api/discord/{cid}?purge=true").json()
    assert out["documents_removed"] == 4
    with session() as s:
        assert not s.scalars(select(Document).where(Document.source_url.like(f"discord://{cid}/%"))).all()
        assert s.get(DiscordConnector, cid) is None
