"""Per-user GitHub access (gitsync.py): tokens, SSH keys, which credential a push uses."""

import secrets
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_company import auth, gitsync, projects
from agent_company.api import create_app
from agent_company.db import User, session

TOKEN = "github_pat_" + "A1b2C3d4" * 6


def make_user(name: str) -> int:
    user, _ = auth.register(name, secrets.token_urlsafe(32))
    return user["id"]


def test_access_resolution():
    uid = make_user("owner")
    url = "https://github.com/MexcSell/Agent_Company.git"
    a = gitsync.access(url, uid)
    assert a["via"] == "the server's GitHub login" and a["url"] == url and a["secret"] is None

    gitsync.create_ssh_key(uid)
    a = gitsync.access(url, uid)
    assert a["via"] == "the user's SSH key" and a["url"] == "git@github.com:MexcSell/Agent_Company.git"
    assert str(gitsync.key_path(uid)) in a["env"]["GIT_SSH_COMMAND"] and "IdentitiesOnly=yes" in a["env"]["GIT_SSH_COMMAND"]

    gitsync.set_token(uid, TOKEN)  # the token wins over the key
    a = gitsync.access("git@github.com:MexcSell/Agent_Company.git", uid)
    assert a["via"] == "the user's GitHub token" and a["url"] == url
    assert a["env"] == {"AC_GIT_TOKEN": TOKEN}
    assert not any(TOKEN in x for x in a["args"])  # never on the command line
    assert gitsync.access("/srv/repos/local.git", uid)["url"] == "/srv/repos/local.git"  # non-GitHub: untouched


def test_token_is_encrypted_and_never_returned():
    uid = make_user("owner")
    with pytest.raises(ValueError):
        gitsync.set_token(uid, "short")
    gitsync.set_token(uid, TOKEN)
    with session() as s:
        stored = s.get(User, uid).git_token
    assert stored and TOKEN not in stored
    st = gitsync.access_status(uid)
    assert st["token"] is True and st["token_hint"] == "…" + TOKEN[-4:] and TOKEN not in str(st)
    gitsync.clear_token(uid)
    assert gitsync.access_status(uid)["token"] is False


def test_ssh_key_files_and_user_deletion():
    admin, uid = make_user("boss"), make_user("member")
    pub = gitsync.create_ssh_key(uid)
    assert pub.startswith("ssh-ed25519 ")
    assert gitsync.create_ssh_key(uid) == pub  # created once
    assert oct(gitsync.key_path(uid).stat().st_mode & 0o777) == "0o600"
    assert oct(gitsync.keys_dir().stat().st_mode & 0o777) == "0o700"
    with session() as s:
        assert auth.user_dict(s.get(User, uid))["git"]["ssh_key"] is True
    auth.delete_user(admin, uid)
    assert not gitsync.key_path(uid).exists() and not gitsync.key_path(uid).with_suffix(".pub").exists()


def test_push_with_token_does_not_leak_it(tmp_path):
    uid = make_user("owner")
    gitsync.set_token(uid, TOKEN)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    p = projects.create("Tokened", github_url="https://example.invalid/placeholder.git")
    with session() as s:
        s.get(projects.Project, p["id"]).github_url = str(remote)
    out = gitsync.push(p["id"], uid)
    assert out["status"] == "pushed", out
    assert "with the user's GitHub token" in out["message"]
    assert TOKEN not in out["message"] and all(TOKEN not in step for step in out["steps"])
    assert gitsync.push(p["id"], uid)["status"] == "noop"


def test_test_access_parses_github_answers(monkeypatch):
    uid = make_user("owner")
    assert gitsync.test_access(uid)["via"] == "none"
    gitsync.create_ssh_key(uid)
    monkeypatch.setattr(gitsync.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="Hi octocat! You've successfully authenticated, but GitHub does not provide shell access."))
    r = gitsync.test_access(uid)
    assert r["ok"] and r["login"] == "octocat"  # GitHub exits 1 even on success

    import httpx

    gitsync.set_token(uid, TOKEN)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"login": "mexcsell"}))
    r = gitsync.test_access(uid)
    assert r["ok"] and r["via"] == "token" and r["login"] == "mexcsell"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=401, json=lambda: {}))
    assert gitsync.test_access(uid)["ok"] is False


def test_git_settings_api_self_or_admin():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as admin:
        assert admin.post("/api/auth/register", json={"name": "boss", "device_secret": secrets.token_urlsafe(32)}).json()["user"]["is_admin"]
        member = TestClient(admin.app)
        ms = secrets.token_urlsafe(32)
        mid = member.post("/api/auth/register", json={"name": "member", "device_secret": ms}).json()["user"]["id"]
        admin.post(f"/api/users/{mid}/approve")
        assert member.post("/api/auth/device", json={"device_secret": ms}).status_code == 200

        # a user edits their own settings, not someone else's
        assert member.put(f"/api/users/{mid}/git", json={"name": "Mem", "email": "mem@example.com", "branch": "dev"}).status_code == 200
        assert member.put(f"/api/users/{mid}/git/token", json={"token": TOKEN}).json()["token"] is True
        assert member.post(f"/api/users/{mid}/git/ssh-key").json()["ssh_key"].startswith("ssh-ed25519 ")
        assert member.get("/api/users/1/git").status_code == 403
        assert member.put("/api/users/1/git/token", json={"token": TOKEN}).status_code == 403

        # the token never comes back from any endpoint
        for r in (member.get(f"/api/users/{mid}/git"), member.get("/api/auth/me"), admin.get("/api/users")):
            assert r.status_code == 200 and TOKEN not in r.text
        assert member.get("/api/auth/me").json()["git"]["token"] is True

        # an administrator may manage anyone's
        assert admin.delete(f"/api/users/{mid}/git/token").json()["token"] is False
        assert admin.delete(f"/api/users/{mid}/git/ssh-key").json()["ssh_key"] == ""
        actions = [r["action"] for r in admin.get("/api/audit").json()["records"]]
        assert {"user_git_token_set", "user_git_token_removed", "user_git_ssh_key_created"} <= set(actions)
        assert TOKEN not in admin.get("/api/audit").text


def test_token_helper_feeds_git_and_overrides_server_login():
    import os

    uid = make_user("owner")
    gitsync.set_token(uid, TOKEN)
    a = gitsync.access("https://github.com/o/r.git", uid)  # exactly what a push passes to git
    r = subprocess.run(["git", *a["args"], "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, env={**os.environ, **a["env"], "GIT_TERMINAL_PROMPT": "0"})
    assert r.returncode == 0 and f"password={TOKEN}" in r.stdout and "username=x-access-token" in r.stdout
