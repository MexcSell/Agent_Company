import io
import os
import json
import re
import secrets
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_company import files, projects, terminal, workspace
from agent_company.api import create_app


@pytest.fixture
def root(tmp_path):
    p = tmp_path / "proj"
    (p / "src").mkdir(parents=True)
    (p / "src" / "a.py").write_text("print('hi')\n")
    (p / "README.md").write_text("# Hi\n")
    projects.create("Work", path=str(p))
    return p


@pytest.fixture
def admin():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        yield c
    terminal.close_all()


def member_of(app):
    """An approved, signed-in non-administrator."""
    m, s = TestClient(app.app), secrets.token_urlsafe(32)
    m.post("/api/auth/register", json={"name": "member", "device_secret": s})
    uid = next(u["id"] for u in app.get("/api/users").json() if u["name"] == "member")
    app.post(f"/api/users/{uid}/approve")
    m.post("/api/auth/device", json={"device_secret": s})
    assert m.get("/api/auth/me").status_code == 200
    return m


# --- files ------------------------------------------------------------------------

def test_kinds_cover_documents_archives_and_media(tmp_path):
    for name, kind in [("a.pdf", "pdf"), ("b.docx", "docx"), ("c.svg", "svg"), ("d.png", "image"), ("e.jpg", "image"),
                       ("f.zip", "archive"), ("g.tar.gz", "archive"), ("h.mp3", "audio"), ("i.mp4", "video"),
                       ("Dockerfile", "docker"), ("j.md", "markdown"), ("k.py", "python")]:
        (tmp_path / name).write_bytes(b"x")
        assert files.kind_of(tmp_path / name) == kind, name


def test_compress_extract_and_open_folder(root, tmp_path):
    out = files.compress([str(root / "src"), str(root / "README.md")])
    assert out["path"].endswith("Archive.zip")
    with zipfile.ZipFile(out["path"]) as z:
        assert {"src/a.py", "README.md"} <= set(z.namelist())
    again = files.compress([str(root / "src")])
    assert again["path"].endswith("src.zip")
    assert files.compress([str(root / "src")])["path"].endswith("src (2).zip")
    ext = files.extract(out["path"])
    assert (root / "Archive" / "src" / "a.py").read_text() == "print('hi')\n" and ext["path"] == str(root / "Archive")
    with pytest.raises(files.FileError):
        files.extract(str(root / "README.md"))

    # A zip that tries to escape its folder stays inside it.
    evil = root / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../escaped.txt", "x")
    files.extract(str(evil))
    assert not (tmp_path / "escaped.txt").exists() and not (root.parent / "escaped.txt").exists()

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.txt").write_text("x")
    with pytest.raises(files.FileError):
        files.read(str(elsewhere / "x.txt"))
    files.add_root(str(elsewhere))
    assert files.read(str(elsewhere / "x.txt"))["text"] == "x"
    assert any(r["path"] == str(elsewhere) for r in files.roots())
    files.remove_root(str(elsewhere))
    with pytest.raises(files.FileError):
        files.read(str(elsewhere / "x.txt"))


def test_download_endpoints(admin, root):
    r = admin.get("/api/files/raw", params={"path": str(root / "README.md"), "download": 1})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.content == b"# Hi\n"
    assert "attachment" not in admin.get("/api/files/raw", params={"path": str(root / "README.md")}).headers.get("content-disposition", "")
    r = admin.get("/api/files/archive", params=[("paths", str(root / "src")), ("paths", str(root / "README.md"))])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert {"src/a.py", "README.md"} <= set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert not list((root.parent / "data" / "tmp").glob("*.zip"))  # temporary zip removed after sending
    assert admin.post("/api/files/compress", json={"paths": [str(root / "src")], "name": "pack"}).json()["path"].endswith("pack.zip")
    assert admin.post("/api/files/extract", json={"path": str(root / "pack.zip")}).status_code == 200
    actions = {r["action"] for r in admin.get("/api/audit").json()["records"]}
    assert {"file_download", "file_compress", "file_extract"} <= actions
    assert member_of(admin).get("/api/files/archive", params={"paths": str(root)}).status_code == 403


# --- terminal ---------------------------------------------------------------------

def _read_until(ws, needle, limit=200):
    seen = b""
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes"):
            seen += msg["bytes"]
        elif msg.get("text") and json.loads(msg["text"]).get("t") == "exit":
            return seen, True
        if needle in seen:
            return seen, False
    raise AssertionError(f"{needle!r} not in output: {seen[-500:]!r}")


def test_terminal_session_runs_commands_and_replays(admin, root):
    s = admin.post("/api/terminal/sessions", json={"cwd": str(root), "cols": 100, "rows": 30}).json()
    assert s["alive"] and s["cwd"] == str(root)
    with admin.websocket_connect(f"/api/terminal/sessions/{s['id']}/ws") as ws:
        ws.send_text(json.dumps({"t": "resize", "cols": 90, "rows": 20}))
        ws.send_text(json.dumps({"t": "in", "data": "echo hel''lo-$((6*7)); stty size\n"}))
        out, _ = _read_until(ws, b"20 90")
        assert b"hello-42" in out
    # Re-attaching replays what the shell printed.
    with admin.websocket_connect(f"/api/terminal/sessions/{s['id']}/ws") as ws:
        out, _ = _read_until(ws, b"hello-42")
        ws.send_bytes(b"exit 3\n")
        _, exited = _read_until(ws, b"\x00never")
        assert exited
    listed = {x["id"]: x for x in admin.get("/api/terminal/sessions").json()}
    assert listed[s["id"]]["exit_code"] == 3 and not listed[s["id"]]["alive"]
    assert admin.delete(f"/api/terminal/sessions/{s['id']}").status_code == 200
    actions = {r["action"] for r in admin.get("/api/audit").json()["records"]}
    assert {"terminal_open", "terminal_close"} <= actions


def test_terminal_reconnect_resumes_where_the_screen_was(admin, root):
    """A browser that reconnects with `after` gets only the output it has not seen, so its screen stays as it was."""
    s = admin.post("/api/terminal/sessions", json={"cwd": str(root)}).json()
    url = f"/api/terminal/sessions/{s['id']}/ws"
    with admin.websocket_connect(url) as ws:
        assert json.loads(ws.receive_text()) == {"t": "at", "from": 0}
        ws.send_text(json.dumps({"t": "in", "data": "echo first-$((2+2))\n"}))
        seen, _ = _read_until(ws, b"first-4\r\n")
    # The browser is offline while the shell prints more.
    with admin.websocket_connect(url) as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"t": "in", "data": "echo second-$((3+3))\n"}))
        _read_until(ws, b"second-6\r\n")
    with admin.websocket_connect(f"{url}?after={len(seen)}") as ws:
        assert json.loads(ws.receive_text()) == {"t": "at", "from": len(seen)}
        rest, _ = _read_until(ws, b"second-6\r\n")
        assert b"first-4\r\n" not in rest
    # An offset the buffer cannot serve replays everything from its start.
    with admin.websocket_connect(f"{url}?after=999999999") as ws:
        assert json.loads(ws.receive_text()) == {"t": "at", "from": 0}
        full, _ = _read_until(ws, b"second-6\r\n")
        assert full.startswith(seen[:20])
    admin.delete(f"/api/terminal/sessions/{s['id']}")
    # A shell the server no longer has (it restarted) answers 4404, which the browser must see to start a new one.
    with admin.websocket_connect(url) as ws:
        msg = ws.receive()
    assert msg["type"] == "websocket.close" and msg["code"] == 4404


def test_terminal_is_admin_only_and_same_origin(admin, root):
    s = admin.post("/api/terminal/sessions", json={"cwd": str(root)}).json()
    member = member_of(admin)
    assert member.post("/api/terminal/sessions", json={}).status_code == 403
    for client, headers in [(member, {}), (admin, {"origin": "https://evil.example"})]:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/terminal/sessions/{s['id']}/ws", headers=headers) as ws:
                ws.receive()
    with admin.websocket_connect(f"/api/terminal/sessions/{s['id']}/ws", headers={"origin": "http://testserver"}) as ws:
        ws.send_text(json.dumps({"t": "in", "data": "echo ok-$((1+1))\n"}))
        _read_until(ws, b"ok-2")
    assert admin.post("/api/terminal/sessions", json={"cwd": str(root / "missing")}).status_code == 400


# --- Claude Code panel ----------------------------------------------------------

STREAM = [
    {"type": "system", "subtype": "init", "session_id": "11111111-2222-3333-4444-555555555555", "model": "m", "cwd": "/x", "tools": ["Bash"]},
    {"type": "rate_limit_event"},
    {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Let me look."}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "a.py"}]}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "There is **one** file."}]}},
    {"type": "result", "result": "There is **one** file.", "is_error": False, "total_cost_usd": 0.01, "duration_ms": 900,
     "num_turns": 2, "session_id": "11111111-2222-3333-4444-555555555555"},
]


class FakeProc:
    calls = []

    def __init__(self, cmd, **kw):
        FakeProc.calls.append((cmd, kw))
        self.pid = 999999
        self.stdin = io.StringIO()
        self.stdout = iter([json.dumps(e) + "\n" for e in STREAM] + ["not json\n"])
        self.stderr = iter([])

    def wait(self, timeout=None):
        return 0


def test_normalise_maps_stream_to_display_events():
    kinds = [e["kind"] for raw in STREAM for e in workspace.normalise(raw)]
    assert kinds == ["init", "thinking", "tool_use", "tool_result", "text", "result"]
    sub = workspace.normalise({"type": "assistant", "parent_tool_use_id": "t9", "message": {"content": [{"type": "text", "text": "x"}]}})
    assert sub[0]["parent"] == "t9"


def test_claude_panel_runs_resumes_and_attaches_images(admin, root, monkeypatch):
    FakeProc.calls = []
    written = []

    class Stdin(io.StringIO):
        def close(self):
            written.append(self.getvalue())
            super().close()

    def fake(cmd, **kw):
        p = FakeProc(cmd, **kw)
        p.stdin = Stdin()
        return p

    monkeypatch.setattr(workspace.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(workspace.subprocess, "Popen", fake)
    monkeypatch.setattr(workspace, "_version", lambda exe: "2.1.310")
    monkeypatch.setattr(workspace, "make_title", lambda prompt: "Look around")
    info = admin.get("/api/workspace/claude").json()
    assert info["available"] is True and info["version"] == "2.1.310" and info["account"]["method"]
    assert info["providers"][0]["efforts"] == ["default", "low", "medium", "high", "xhigh", "max"]
    up = admin.post("/api/uploads", files={"file": ("shot.png", b"\x89PNG\r\n\x1a\n", "image/png")}).json()
    run = admin.post("/api/workspace/claude", json={"prompt": "-rf what is here?", "cwd": str(root), "attachments": [up["id"]],
                                                    "model": "sonnet", "effort": "high"}).json()
    for _ in range(100):
        d = admin.get(f"/api/workspace/claude/{run['id']}").json()
        if d["status"] != "running":
            break
        time.sleep(0.02)
    assert d["status"] == "done" and d["session_id"].startswith("11111111") and d["title"] == "Look around"
    assert [e["kind"] for e in d["events"]] == ["init", "thinking", "tool_use", "tool_result", "text", "result", "log"]
    assert admin.get(f"/api/workspace/claude/{run['id']}?after=5").json()["events"][0]["kind"] == "result"
    cmd, kw = FakeProc.calls[0]
    assert kw["cwd"] == root and "--allowedTools" in cmd and "Bash" in cmd[cmd.index("--allowedTools") + 1]
    assert cmd[cmd.index("--model") + 1] == "sonnet" and "--add-dir" in cmd
    assert cmd[cmd.index("--effort") + 1] == "high" and d["effort"] == "high"
    assert "-rf what is here?" not in cmd  # the prompt goes through stdin, never argv
    assert written[0].startswith("-rf what is here?") and "shot.png" in written[0]
    # A new conversation gets its session id up front, so the panel knows it before Claude Code answers.
    first = cmd[cmd.index("--session-id") + 1]
    assert "--resume" not in cmd and "--name" not in cmd and re.fullmatch(r"[0-9a-f-]{36}", first)
    # A follow-up resumes the same Claude session under the conversation's title.
    folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-x"
    folder.mkdir(parents=True)
    (folder / f"{d['session_id']}.jsonl").write_text("{}\n")
    admin.post("/api/workspace/claude", json={"prompt": "and now?", "cwd": str(root), "session_id": d["session_id"],
                                              "title": "Look around"})
    cmd2, _ = FakeProc.calls[1]
    assert cmd2[cmd2.index("--resume") + 1] == d["session_id"] and cmd2[cmd2.index("--name") + 1] == "Look around"
    assert "--effort" not in cmd2  # no effort chosen: Claude Code's own setting
    # A conversation whose first prompt never reached Claude Code has no transcript: it starts under its id.
    admin.post("/api/workspace/claude", json={"prompt": "again", "cwd": str(root), "session_id": first})
    cmd3, _ = FakeProc.calls[2]
    assert "--resume" not in cmd3 and cmd3[cmd3.index("--session-id") + 1] == first
    assert admin.post("/api/workspace/claude", json={"prompt": "x", "session_id": "bad id; rm"}).status_code == 400
    assert admin.post("/api/workspace/claude", json={"prompt": "x", "cwd": str(root), "effort": "huge"}).status_code == 400
    assert any(r["action"] == "workspace_claude" for r in admin.get("/api/audit").json()["records"])
    assert member_of(admin).post("/api/workspace/claude", json={"prompt": "x"}).status_code == 403


def test_codex_effort_is_a_config_override():
    run = workspace.Run("hi", "/tmp", "admin", "", "", [], provider="codex", effort="medium")
    cmd = workspace._command(run)
    assert cmd[cmd.index("-c") + 1] == "model_reasoning_effort=medium" and cmd[-1] == "-"
    assert "-c" not in workspace._command(workspace.Run("hi", "/tmp", "admin", "", "", [], provider="codex"))


def test_make_title_asks_haiku_without_tools_and_falls_back(monkeypatch):
    seen = {}

    class Proc:
        pid, returncode = 4242, 0

        def __init__(self, cmd, **kw):
            seen["cmd"], seen["listed"] = cmd, set(workspace._helper_pids)

        def communicate(self, text, timeout=None):
            seen["listed"] |= set(workspace._helper_pids)
            seen["text"] = text
            return '"Fix the login redirect."\n', ""

    monkeypatch.setattr(workspace.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(workspace.subprocess, "Popen", Proc)
    assert workspace.make_title("the login page sends me back to /\nplease fix") == "Fix the login redirect"
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd and "the login page" in seen["text"]
    assert 4242 in seen["listed"] and 4242 not in workspace._helper_pids  # hidden from the sessions list while it runs
    monkeypatch.setattr(workspace.shutil, "which", lambda _: None)
    assert workspace.make_title("\n  Explain   the build step by step and also every single flag it takes please") \
        == "Explain the build step by step and also every…"


def test_claude_panel_reports_missing_cli(admin, monkeypatch):
    monkeypatch.setattr(workspace.shutil, "which", lambda _: None)
    r = admin.post("/api/workspace/claude", json={"prompt": "hi"})
    assert r.status_code == 503 and "not installed" in r.json()["detail"]


# --- Claude Code sessions on the host (/status, /tasks, /agents) -------------------------

SID = "aaaaaaaa-1111-2222-3333-444444444444"


def _line(**kw):
    return json.dumps({"isSidechain": False, "timestamp": "2026-11-04T10:00:00.000Z", **kw})


def _assistant(*content, usage=None):
    return _line(type="assistant", cwd="/srv/app", gitBranch="main", version="2.1.310", permissionMode="acceptEdits",
                 message={"model": "claude-opus-5-5", "content": list(content),
                          "usage": usage or {"input_tokens": 3, "cache_read_input_tokens": 1000,
                                             "cache_creation_input_tokens": 200, "output_tokens": 50}})


def _result(tid, text, res=None, is_error=False):
    return _line(type="user", toolUseResult=res or {},
                 message={"content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]})


def _claude_host(tmp_path, *lines, pid=None):
    home = tmp_path / "claude"
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    pid = pid or os.getpid()
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    (home / "sessions" / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": SID, "cwd": "/srv/app", "procStart": stat[19], "name": "app-1", "status": "busy",
        "entrypoint": "claude-vscode", "kind": "interactive", "version": "2.1.310", "startedAt": 1, "updatedAt": 2}))
    # A crashed session leaves its file behind: another process now has that pid, or none does.
    (home / "sessions" / "999999.json").write_text(json.dumps({"pid": 999999, "sessionId": "b" * 8 + "-0000", "procStart": "1"}))
    (home / "sessions" / "1.json").write_text(json.dumps({"pid": 1, "sessionId": "c" * 8 + "-0000", "procStart": "12345678"}))
    folder = home / "projects" / "-srv-app"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{SID}.jsonl").write_text("".join(x + "\n" for x in lines))
    return home, folder


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_claude_sessions_list_only_live_processes(admin, tmp_path):
    _claude_host(tmp_path)
    got = admin.get("/api/workspace/claude-sessions").json()
    assert [s["session_id"] for s in got] == [SID]
    assert got[0]["status"] == "busy" and got[0]["entrypoint"] == "claude-vscode" and got[0]["name"] == "app-1"
    assert got[0]["title"] == ""  # nothing written yet
    # The tab name comes from the transcript (AI title, rename, else the first prompt), not the derived name.
    workspace._titles.clear()
    (tmp_path / "claude" / "projects" / "-srv-app" / f"{SID}.jsonl").write_text(
        _line(type="user", message={"content": "Fix the login bug"}) + "\n"
        + json.dumps({"type": "ai-title", "aiTitle": "Login bug fix", "sessionId": SID}) + "\n")
    assert workspace.sessions(refresh=True)[0]["title"] == "Login bug fix"
    assert admin.get(f"/api/workspace/claude-sessions/{SID}").json()["title"] == "Login bug fix"
    # A title helper registers like a session: it is never listed.
    workspace._helper_pids.add(os.getpid())
    try:
        assert workspace.sessions(refresh=True) == []
    finally:
        workspace._helper_pids.discard(os.getpid())
    assert member_of(admin).get("/api/workspace/claude-sessions").status_code == 403


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_claude_session_status_tasks_and_agents(admin, tmp_path):
    lines = [
        _line(type="attachment", attachment={"type": "agent_listing_delta", "addedTypes": ["general-purpose", "Explore"]}),
        _line(type="user", permissionMode="acceptEdits", message={"content": "Fix the login bug"}),
        _line(type="user", isMeta=True, message={"content": "[Image: original 10x10]"}),
        _assistant({"type": "thinking", "thinking": "look first"},
                   {"type": "tool_use", "id": "t1", "name": "Agent",
                    "input": {"subagent_type": "Explore", "description": "Find login code", "prompt": "..."}}),
        _result("t1", "found it", {"status": "completed", "agentId": "a1", "resolvedModel": "claude-haiku-5-5",
                                   "totalTokens": 900, "totalToolUseCount": 4}),
        _assistant({"type": "tool_use", "id": "t2", "name": "Bash",
                    "input": {"command": "npm run dev", "description": "Dev server", "run_in_background": True}}),
        _result("t2", "Command running in background with ID: bg1.", {"backgroundTaskId": "bg1"}),
        _assistant({"type": "tool_use", "id": "t3", "name": "Bash",
                    "input": {"command": "pytest", "run_in_background": True}}),
        _result("t3", "Command running in background with ID: bg2.", {"backgroundTaskId": "bg2"}),
        _assistant({"type": "tool_use", "id": "t4", "name": "Agent",
                    "input": {"subagent_type": "general-purpose", "description": "Write tests", "run_in_background": True}}),
        _result("t4", "Launched", {"status": "async_launched", "agentId": "a2"}),
        _line(type="user", message={"content": "<task-notification>\n<task-id>bg2</task-id>\n<status>completed</status>\n"
                                               "<summary>pytest finished</summary>\n</task-notification>"}),
        _assistant({"type": "tool_use", "id": "t5", "name": "TodoWrite",
                    "input": {"todos": [{"content": "Reproduce", "status": "completed"},
                                        {"content": "Fix", "status": "in_progress"}]}}),
        _result("t5", "Todos updated"),
        # One line per content block, each repeating the same usage: counted once.
        _line(type="assistant", message={"id": "m7", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "Now"}],
                                         "usage": {"input_tokens": 1, "output_tokens": 30}}),
        _line(type="assistant", message={"id": "m7", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "the diff"}],
                                         "usage": {"input_tokens": 1, "output_tokens": 30}}),
        _assistant({"type": "tool_use", "id": "t6", "name": "Bash", "input": {"command": "git diff", "note": "x" * 5000}},
                   usage={"input_tokens": 5, "cache_read_input_tokens": 40000, "cache_creation_input_tokens": 0,
                          "output_tokens": 70}),
    ]
    home, folder = _claude_host(tmp_path, *lines)
    d = admin.get(f"/api/workspace/claude-sessions/{SID}").json()
    assert d["live"] and d["status"] == "busy" and d["model"] == "claude-opus-5-5"
    assert d["permission_mode"] == "acceptEdits" and d["git_branch"] == "main" and d["cwd"] == "/srv/app"
    assert d["context_tokens"] == 40005 and d["output_tokens"] == 50 * 5 + 30 + 70
    assert d["current"]["name"] == "Bash" and d["current"]["input"]["command"] == "git diff"
    assert d["last_prompt"] == "Fix the login bug"
    assert d["agent_types"] == ["general-purpose", "Explore"]
    agents = {a["description"]: a for a in d["agents"]}
    assert agents["Find login code"]["status"] == "completed" and agents["Find login code"]["model"] == "claude-haiku-5-5"
    assert agents["Write tests"]["status"] == "running" and agents["Write tests"]["background"]
    tasks = {t["id"]: t for t in d["tasks"]}
    assert tasks["bg1"]["status"] == "running" and tasks["bg1"]["command"] == "npm run dev"
    assert tasks["bg2"]["status"] == "completed" and tasks["bg2"]["summary"] == "pytest finished"
    assert d["todos"] == [{"content": "Reproduce", "status": "completed"}, {"content": "Fix", "status": "in_progress"}]
    kinds = [e["kind"] for e in d["events"]]
    assert kinds.count("prompt") == 1 and "notice" in kinds and "thinking" in kinds and "tool_result" in kinds
    # The transcript is read incrementally: a poll returns only what was appended since `next`.
    with (folder / f"{SID}.jsonl").open("a") as f:
        f.write(_result("t6", "diff --git a b") + "\n")
        f.write(_line(type="user", message={"content": "<task-notification><task-id>a2</task-id><status>completed</status>"
                                                       "</task-notification>"}) + "\n")
        f.write('{"type": "assistant", "message": {"content": [{"type": "te')  # still being written
    d2 = admin.get(f"/api/workspace/claude-sessions/{SID}?after={d['next']}").json()
    assert [e["kind"] for e in d2["events"]] == ["tool_result", "notice"]
    assert d2["current"]["name"] == "" and {a["description"]: a["status"] for a in d2["agents"]}["Write tests"] == "completed"
    # Agents defined as files are listed too.
    (home / "agents").mkdir()
    (home / "agents" / "reviewer.md").write_text("---\nname: reviewer\ndescription: Reviews diffs\nmodel: sonnet\n---\nBe strict.")
    assert admin.get(f"/api/workspace/claude-sessions/{SID}").json()["configured_agents"] == [
        {"name": "reviewer", "description": "Reviews diffs", "model": "sonnet"}]
    assert admin.get("/api/workspace/claude-sessions/not%20a%20session").status_code == 400
    assert admin.get("/api/workspace/claude-sessions/dddddddd-0000").status_code == 404


def test_claude_session_that_ended_reports_unfinished_work(admin, tmp_path):
    folder = tmp_path / "claude" / "projects" / "-srv-app"
    folder.mkdir(parents=True)
    (folder / f"{SID}.jsonl").write_text(
        _assistant({"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "sleep 99", "run_in_background": True}})
        + "\n" + _result("t2", "running", {"backgroundTaskId": "bg1"}) + "\n")
    d = admin.get(f"/api/workspace/claude-sessions/{SID}").json()
    assert not d["live"] and d["status"] == "ended" and d["current"] is None
    assert d["tasks"][0]["status"] == "ended"


def _session_file(home, pid, sid, status="idle"):
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    (home / "sessions" / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": "/srv/app", "procStart": stat[19], "name": sid[:8], "status": status,
        "entrypoint": "cli", "startedAt": 1, "updatedAt": 2}))


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_claude_history_lists_running_and_ended_sessions_and_deletes(admin, tmp_path):
    home, folder = _claude_host(tmp_path, _line(type="user", cwd="/srv/app", message={"content": "Fix the login bug"}),
                                json.dumps({"type": "custom-title", "sessionId": SID, "customTitle": "Login fix"}))
    renamed, titled, plain, empty = ("dddddddd-0000-0000-0000-00000000000" + x for x in "1234")
    other = home / "projects" / "-srv-web"
    other.mkdir()
    (other / f"{titled}.jsonl").write_text("\n".join([
        _line(type="user", cwd="/srv/web", gitBranch="dev", entrypoint="claude-vscode", message={"content": "Add dark mode"}),
        json.dumps({"type": "ai-title", "aiTitle": "Dark mode toggle", "sessionId": titled}),
        _line(type="user", cwd="/srv/web", message={"content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}),
        _line(type="user", cwd="/srv/web", message={"content": "Now the tests"})]) + "\n")
    (other / f"{plain}.jsonl").write_text(_line(type="user", cwd="/srv/web", message={"content": "Explain the build\nin detail"}) + "\n")
    (other / f"{empty}.jsonl").write_text(json.dumps({"type": "queue-operation", "sessionId": empty}) + "\n")
    (other / f"{renamed}.jsonl").write_text(  # a long transcript: the title is read from its tail
        _line(type="user", cwd="/srv/web", message={"content": "Start"}) + "\n"
        + "".join(_line(type="assistant", message={"content": [{"type": "text", "text": "x" * 1000}]}) + "\n" for _ in range(300))
        + json.dumps({"type": "custom-title", "sessionId": renamed, "customTitle": "Renamed late"}) + "\n")
    (other / plain / "subagents").mkdir(parents=True)
    (other / plain / "subagents" / "agent-1.jsonl").write_text("{}\n")
    (home / "file-history" / plain).mkdir(parents=True)
    os.utime(other / f"{titled}.jsonl", (time.time() + 10, time.time() + 10))  # the most recent ended session

    d = admin.get("/api/workspace/claude-history").json()
    rows = {r["session_id"]: r for r in d["items"]}
    assert d["total"] == 4 and empty not in rows  # opened and closed without a prompt: not history
    assert d["items"][0]["session_id"] == SID and rows[SID]["status"] == "busy" and rows[SID]["title"] == "Login fix"
    assert d["items"][1]["session_id"] == titled
    t = rows[titled]
    assert t["status"] == "ended" and t["title"] == "Dark mode toggle" and t["cwd"] == "/srv/web" and t["git_branch"] == "dev"
    assert t["first_prompt"] == "Add dark mode" and t["last_prompt"] == "Now the tests" and t["entrypoint"] == "claude-vscode"
    assert rows[plain]["title"] == "Explain the build" and rows[renamed]["title"] == "Renamed late"
    assert [r["session_id"] for r in admin.get("/api/workspace/claude-history?q=dark").json()["items"]] == [titled]
    assert admin.get("/api/workspace/claude-history?q=srv/app").json()["total"] == 1

    # Deleting: never a running session; an ended one goes with its subagents and checkpoints.
    assert admin.delete(f"/api/workspace/claude-history/{SID}").status_code == 400
    assert admin.delete(f"/api/workspace/claude-history/{plain}").status_code == 200
    assert not (other / f"{plain}.jsonl").exists() and not (other / plain).exists()
    assert not (home / "file-history" / plain).exists() and (other / f"{titled}.jsonl").exists()
    assert admin.get("/api/workspace/claude-history").json()["total"] == 3
    assert admin.delete(f"/api/workspace/claude-history/{plain}").status_code == 404
    assert admin.delete("/api/workspace/claude-history/..%2Fx").status_code in (400, 404)
    audit = admin.get("/api/audit").json()["records"]
    assert any(a["action"] == "workspace_claude_session_delete" and a["target"] == plain for a in audit)
    m = member_of(admin)
    assert m.get("/api/workspace/claude-history").status_code == 403
    assert m.delete(f"/api/workspace/claude-history/{titled}").status_code == 403


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_claude_session_stop_ends_the_process(admin, tmp_path, monkeypatch):
    import subprocess
    import sys

    home, _ = _claude_host(tmp_path)  # SID runs as this very process: never stopped
    assert admin.post(f"/api/workspace/claude-sessions/{SID}/stop").status_code == 400
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        sid = "eeeeeeee-0000-0000-0000-000000000001"
        _session_file(home, proc.pid, sid)
        workspace._sessions_cache = (0.0, [])
        # A conversation still working elsewhere (a run that outlived a restart) is not resumed twice.
        monkeypatch.setattr(workspace.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(workspace, "_version", lambda exe: "2.1.310")
        busy = admin.post("/api/workspace/claude", json={"prompt": "go on", "cwd": str(tmp_path), "session_id": sid})
        assert busy.status_code == 400 and "still running" in busy.json()["detail"]
        r = admin.post(f"/api/workspace/claude-sessions/{sid}/stop")
        assert r.status_code == 200 and r.json()["pid"] == proc.pid
        assert proc.wait(timeout=10) != 0
        assert sid not in {s["session_id"] for s in admin.get("/api/workspace/claude-sessions").json()}
        assert admin.post(f"/api/workspace/claude-sessions/{sid}/stop").status_code == 404
        assert any(a["action"] == "workspace_claude_session_stop" for a in admin.get("/api/audit").json()["records"])
        assert member_of(admin).post(f"/api/workspace/claude-sessions/{SID}/stop").status_code == 403
    finally:
        proc.kill()


def test_claude_usage_counts_today_and_this_week_once_per_message(admin):
    from datetime import timedelta

    from agent_company import usage

    workspace._usage_files.clear()
    now = usage.now()
    folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-x"
    (folder / "s1" / "subagents").mkdir(parents=True)

    def line(mid, at, model="claude-opus-5-5", **u):
        return json.dumps({"type": "assistant", "requestId": f"r{mid}", "timestamp": at.isoformat().replace("+00:00", "Z"),
                           "message": {"id": mid, "model": model, "usage": u, "content": []}}) + "\n"

    week = usage.week_start(now)
    earlier = week + timedelta(minutes=1) if usage.day_start(now) > week else None
    text = line("m1", now, input_tokens=1000, output_tokens=100, cache_read_input_tokens=10_000)
    text += line("m1", now, input_tokens=1000, output_tokens=100, cache_read_input_tokens=10_000)  # the same reply's next block
    text += line("m0", week - timedelta(hours=1), input_tokens=999_999)  # last week
    text += json.dumps({"type": "user", "message": {"content": "usage of the word"}}) + "\n"
    if earlier:
        text += line("m2", earlier, model="claude-sonnet-5-5", output_tokens=1000)
    (folder / "s1.jsonl").write_text(text)
    (folder / "s1" / "subagents" / "agent-a.jsonl").write_text(
        line("m3", now, model="claude-haiku-5-5-20260101", input_tokens=500, cache_creation_input_tokens=200,
             cache_creation={"ephemeral_1h_input_tokens": 200}) + line("m4", now, model="mystery", output_tokens=7))
    d = admin.get("/api/workspace/claude-usage").json()
    day, wk = d["daily"], d["weekly"]
    assert day["tokens"] == 11_100 + 700 + 7 and day["messages"] == 3 and day["unpriced_tokens"] == 7
    # Opus 4/20 $ per 1M: 1000 input + 10k cache reads at 0.1x + 100 output; Haiku 0.1/0.5: 500 + 200 1-hour writes at 2x.
    assert day["cost"] == round((4 * (1000 + 1000) + 20 * 100) / 1e6 + 0.1 * (500 + 400) / 1e6, 4)
    assert wk["tokens"] == day["tokens"] + (1000 if earlier else 0)
    assert 0 < day["resets_in_seconds"] <= 86400 and day["resets_at"] == (usage.day_start(now) + timedelta(days=1)).isoformat()
    assert wk["resets_at"] == (week + timedelta(days=7)).isoformat()
    # Appended lines are read on the next call; a member never sees it.
    with (folder / "s1.jsonl").open("a") as f:
        f.write(line("m5", now, output_tokens=50))
    assert admin.get("/api/workspace/claude-usage").json()["daily"]["output_tokens"] == day["output_tokens"] + 50
    assert member_of(admin).get("/api/workspace/claude-usage").status_code == 403


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_subagents_and_tasks_beyond_the_transcript_read_and_the_host_overview(admin, tmp_path):
    home, folder = _claude_host(tmp_path, _line(type="user", message={"content": "go"}),
                                _assistant({"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "sleep 9",
                                                                                                   "run_in_background": True}}),
                                _result("t9", "running", {"backgroundTaskId": "bg9"}))
    # A subagent started before the part of a long transcript that is read: known from its meta file only.
    sub = folder / SID / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a7.meta.json").write_text(json.dumps({"agentType": "web-fetch", "description": "Read a page",
                                                        "toolUseId": "toolu_old", "finished": True}))
    (sub / "agent-a7.jsonl").write_text(_line(type="user", message={"content": "fetch"}) + "\n")
    (sub / "agent-a8.meta.json").write_text(json.dumps({"agentType": "Explore", "toolUseId": "toolu_new", "finished": False}))
    # Claude Code's own task list (TaskCreate / TaskUpdate) lives in ~/.claude/tasks/<session>/.
    (home / "tasks" / SID).mkdir(parents=True)
    for n, status in ((1, "completed"), (2, "in_progress"), (10, "deleted")):
        (home / "tasks" / SID / f"{n}.json").write_text(json.dumps({"id": str(n), "subject": f"step {n}", "status": status}))
    d = admin.get(f"/api/workspace/claude-sessions/{SID}").json()
    agents = {a["id"]: a for a in d["agents"]}
    assert agents["toolu_old"]["type"] == "web-fetch" and agents["toolu_old"]["status"] == "completed"
    assert agents["toolu_old"]["agent_id"] == "a7" and agents["toolu_old"]["started"] > 0
    assert agents["toolu_new"]["status"] == "running"
    assert d["task_list"] == [{"id": "1", "subject": "step 1", "status": "completed"},
                              {"id": "2", "subject": "step 2", "status": "in_progress"}]
    # /agents and /tasks without a session in the tab: every live session on the host.
    (tmp_path / "proj" / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / "proj" / ".claude" / "agents" / "qa.md").write_text("---\nname: qa\ndescription: Tests\n---\n")
    h = admin.get("/api/workspace/claude-activity", params={"cwd": str(tmp_path / "proj")}).json()
    assert [r["session_id"] for r in h["sessions"]] == [SID] and h["busy"] == 1
    assert h["agents_running"] == 1 and h["tasks_running"] == 1 and h["sessions"][0]["agents"] == 2
    assert h["sessions"][0]["todos_open"] == 1 and h["configured_agents"][0]["name"] == "qa"
    assert member_of(admin).get("/api/workspace/claude-activity").status_code == 403


def test_plan_limits_from_the_claude_sign_in(admin, monkeypatch):
    workspace._limits_cache.update(key=None, at=0.0, out=None)
    assert admin.get("/api/workspace/claude-usage").json()["plan"] == {
        "available": False, "reason": "Claude Code is not signed in with a Claude account", "limits": []}
    creds = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".credentials.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "subscriptionType": "pro",
                                                   "expiresAt": (time.time() + 3600) * 1000}}))
    calls = []

    def fake(token):
        calls.append(token)
        return {"five_hour": {"utilization": 25.0, "resets_at": "2026-11-05T05:49:59+00:00"},
                "limits": [{"kind": "session", "percent": 25, "severity": "normal", "resets_at": "2026-11-05T05:49:59+00:00"},
                           {"kind": "weekly_all", "percent": 91, "severity": "warning", "resets_at": "2026-11-07T18:00:00+00:00"},
                           {"kind": "weekly_scoped", "percent": 2, "scope": {"model": {"display_name": "Fable"}}},
                           {"kind": "odd", "percent": None}]}

    monkeypatch.setattr(workspace, "_oauth_usage", fake)
    p = admin.get("/api/workspace/claude-usage").json()["plan"]
    assert p["available"] and p["plan"] == "pro" and calls == ["tok"]
    assert [(x["label"], x["percent"], x["severity"]) for x in p["limits"]] == [
        ("5-hour session", 25.0, "normal"), ("Weekly limit", 91.0, "warning"), ("Weekly limit · Fable", 2.0, "normal")]
    assert "tok" not in json.dumps(p)  # the token never reaches the browser
    admin.get("/api/workspace/claude-usage")
    assert calls == ["tok"]  # read once a minute
    # The older answer shape; a refused sign-in says so.
    assert [x["label"] for x in workspace._parse_limits({"five_hour": {"utilization": 3}, "seven_day": {"utilization": 9}})] == [
        "5-hour session", "Weekly limit"]

    def refused(token):
        raise workspace.urllib.error.HTTPError(workspace.USAGE_URL, 401, "no", {}, None)

    monkeypatch.setattr(workspace, "_oauth_usage", refused)
    workspace._limits_cache.update(key=None)
    p = workspace.claude_limits()
    assert not p["available"] and "sign in again" in p["reason"]
