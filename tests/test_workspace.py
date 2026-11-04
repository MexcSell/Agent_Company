import io
import os
import json
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
    info = admin.get("/api/workspace/claude").json()
    assert info["available"] is True and info["version"] == "2.1.310" and info["account"]["method"]
    up = admin.post("/api/uploads", files={"file": ("shot.png", b"\x89PNG\r\n\x1a\n", "image/png")}).json()
    run = admin.post("/api/workspace/claude", json={"prompt": "-rf what is here?", "cwd": str(root), "attachments": [up["id"]],
                                                    "model": "sonnet"}).json()
    for _ in range(100):
        d = admin.get(f"/api/workspace/claude/{run['id']}").json()
        if d["status"] != "running":
            break
        time.sleep(0.02)
    assert d["status"] == "done" and d["session_id"].startswith("11111111")
    assert [e["kind"] for e in d["events"]] == ["init", "thinking", "tool_use", "tool_result", "text", "result", "log"]
    assert admin.get(f"/api/workspace/claude/{run['id']}?after=5").json()["events"][0]["kind"] == "result"
    cmd, kw = FakeProc.calls[0]
    assert kw["cwd"] == root and "--allowedTools" in cmd and "Bash" in cmd[cmd.index("--allowedTools") + 1]
    assert cmd[cmd.index("--model") + 1] == "sonnet" and "--add-dir" in cmd
    assert "-rf what is here?" not in cmd  # the prompt goes through stdin, never argv
    assert written[0].startswith("-rf what is here?") and "shot.png" in written[0]
    # A follow-up resumes the same Claude session.
    admin.post("/api/workspace/claude", json={"prompt": "and now?", "cwd": str(root), "session_id": d["session_id"]})
    cmd2, _ = FakeProc.calls[1]
    assert cmd2[cmd2.index("--resume") + 1] == d["session_id"]
    assert admin.post("/api/workspace/claude", json={"prompt": "x", "session_id": "bad id; rm"}).status_code == 400
    assert any(r["action"] == "workspace_claude" for r in admin.get("/api/audit").json()["records"])
    assert member_of(admin).post("/api/workspace/claude", json={"prompt": "x"}).status_code == 403


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
