import io
import json
import secrets
import time
import zipfile

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
    assert admin.get("/api/workspace/claude").json()["available"] is True
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
