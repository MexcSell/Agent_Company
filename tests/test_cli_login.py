"""Signing the Copilot CLI in to GitHub from the website: the device code reaches the page, the result is confirmed
from the CLI's own config (never from what it printed), and a CLI model records whose sign-in it was registered with."""

import json
import os
import secrets
import stat
import time

import pytest
from fastapi.testclient import TestClient

from agent_company import router

# What `copilot login --device-code` (1.0.x) does, then it waits until the test confirms or denies on "GitHub". As the
# real CLI on a server without a keychain, it asks before keeping the token as plain text, and only in a terminal
# (without one it gives up: "Login succeeded, but the token was not saved"); its config starts with comment lines.
FAKE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "GitHub Copilot CLI 1.0.107."; exit 0; fi
echo "To authenticate, visit https://github.com/login/device and enter code ABCD-12EF"
echo "Waiting for authorization..."
echo "Failed to copy to clipboard. Please visit https://github.com/login/device and enter the code ABCD-12EF manually."
while [ ! -f "$HOME/answer" ]; do sleep 0.05; done
if [ "$(cat "$HOME/answer")" = ok ]; then
  if [ -t 0 ] && [ -t 1 ]; then
    printf "System keychain unavailable. Store token in plaintext config file? (y/N) "
    read reply
  fi
  case "$reply" in y|yes) ;; *)
    echo "Login succeeded, but the token was not saved. Install a system keychain or rerun login and accept plaintext storage." >&2
    exit 1;;
  esac
  mkdir -p "$HOME/.copilot"
  printf '// User settings belong in settings.json.\n// This file is managed automatically.\n' > "$HOME/.copilot/config.json"
  echo '{"logged_in_users":[{"host":"https://github.com","login":"octocat"}],"last_logged_in_user":{"host":"https://github.com","login":"octocat"}}' >> "$HOME/.copilot/config.json"
  exit 0
fi
echo "Error: the authorization request was denied"
exit 1
"""


@pytest.fixture
def copilot(tmp_path, monkeypatch):
    home, bin_dir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    exe = bin_dir / "copilot"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    for k in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(router, "_logins", {})
    yield home
    for job in router._logins.values():
        router._end_login(job)


def _until(state, tool="copilot", timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        out = router.login_state(tool)
        if out["state"] == state:
            return out
        time.sleep(0.05)
    raise AssertionError(f"still {router.login_state(tool)['state']}, wanted {state}")


def _approve(home, store=True):
    """The person approves on GitHub, then answers the page's plaintext question."""
    (home / "answer").write_text("ok")
    assert _until("consent")["code"] == "ABCD-12EF"
    return router.answer_login("copilot", store)


def test_the_code_reaches_the_page_and_a_confirmed_sign_in_is_reported_with_the_account(copilot):
    assert router.cli_status("copilot")["signed_in"] is False
    out = router.cli_login("copilot", by="admin")
    assert out["state"] == "waiting" and out["url"] == "https://github.com/login/device" and out["code"] == "ABCD-12EF"
    assert 0 < out["seconds_left"] <= router.LOGIN_TIMEOUT and "proc" not in out and "output" not in out
    assert router.cli_login("copilot")["code"] == "ABCD-12EF"  # a second click follows the running sign-in
    assert _approve(copilot)["state"] == "finishing"
    done = _until("signed_in")
    assert done["account"]["email"] == "octocat" and done["account"]["method"] == "GitHub account"
    st = router.cli_status("copilot")
    assert st["signed_in"] and st["account"]["email"] == "octocat" and st["can_login"]


def test_a_denied_sign_in_fails_with_the_reason(copilot):
    router.cli_login("copilot")
    (copilot / "answer").write_text("no")
    out = _until("failed")
    assert "denied" in out["error"] and not router.cli_status("copilot")["signed_in"]


def test_declining_plain_text_storage_cancels_without_signing_in(copilot):
    router.cli_login("copilot")
    _approve(copilot, store=False)
    out = _until("cancelled")
    assert "not stored" in out["error"] and not router.cli_status("copilot")["signed_in"]
    with pytest.raises(ValueError):
        router.answer_login("copilot", True)  # nothing is waiting for an answer any more


def test_without_a_terminal_the_cli_could_not_ask_and_the_error_says_why(copilot, monkeypatch):
    """What happened before the sign-in ran in a pseudo-terminal: the CLI's message is the error, without the noise."""
    import pty

    def no_tty():
        r, w = os.pipe()
        return r, w

    monkeypatch.setattr(pty, "openpty", no_tty)
    router.cli_login("copilot")
    (copilot / "answer").write_text("ok")
    out = _until("failed")
    assert out["error"].startswith("Login succeeded, but the token was not saved")
    assert "clipboard" not in out["error"] and "Waiting" not in out["error"]


def test_a_cli_that_exits_cleanly_without_signing_in_is_not_reported_signed_in(copilot):
    script = copilot.parent / "bin" / "copilot"
    script.write_text(FAKE.replace('mkdir -p "$HOME/.copilot"', "true").replace(
        "echo '{\"logged_in_users\"", "true # "))
    router.cli_login("copilot")
    _approve(copilot)
    assert _until("failed")["state"] == "failed"  # exit 0, but its config names nobody


def test_cancel_ends_the_waiting_cli(copilot):
    router.cli_login("copilot")
    proc = router._logins["copilot"]["proc"]
    assert router.cancel_login("copilot")["state"] == "cancelled"
    proc.wait(timeout=5)
    assert router.login_state("copilot")["state"] == "cancelled"


def test_a_cli_model_records_the_sign_in_it_was_registered_with(copilot):
    router.cli_login("copilot")
    _approve(copilot)
    _until("signed_in")
    m = router.add_model({"name": "Copilot of octocat", "kind": "copilot"})
    assert m["signin"]["method"] == "GitHub account" and m["signin"]["email"] == "octocat" and m["signin"]["at"]
    assert "token" not in json.dumps(m["signin"]).lower()
    api_model = router.add_model({"name": "Some API model", "kind": "llm", "model_id": "openai/gpt-5"})
    assert api_model["signin"] == {}


def test_the_api_is_for_administrators_and_audits_start_and_result(copilot):
    from agent_company.api import create_app

    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        assert app.get("/api/models/cli/copilot/login").json()["state"] == "none"
        r = app.post("/api/models/cli/copilot/login")
        assert r.status_code == 200 and r.json()["code"] == "ABCD-12EF"
        assert app.post("/api/models/cli/claude/login").status_code == 404  # no website sign-in for that CLI
        (copilot / "answer").write_text("ok")
        _until("consent")
        assert app.post("/api/models/cli/copilot/login/consent", json={"store": "yes"}).status_code == 422
        assert app.post("/api/models/cli/copilot/login/consent", json={"store": True}).json()["state"] == "finishing"
        assert app.post("/api/models/cli/copilot/login/consent", json={"store": True}).status_code == 409
        _until("signed_in")
        assert app.get("/api/models/cli/copilot/login").json()["account"]["email"] == "octocat"
        actions = {(a["action"], a["actor"]) for a in app.get("/api/audit").json()["records"]}
        assert {("cli_login", "user:admin"), ("cli_login_consent", "user:admin"),
                ("cli_login_result", "user:admin")} <= actions


# What `codex login --device-auth` (0.162) prints: the address and the code on separate lines, in colour. Once the test
# confirms on "OpenAI" it writes auth.json into its CODEX_HOME, as the real CLI does.
CODEX_FAKE = r"""#!/bin/sh
if [ "$1" = "--version" ]; then echo "codex-cli 0.162.1"; exit 0; fi
printf '\nWelcome to Codex [v\033[90m0.162.1\033[0m]\n\nFollow these steps to sign in with ChatGPT using device code authorization:\n\n'
printf '1. Open this link in your browser and sign in to your account\n   \033[94mhttps://auth.openai.com/codex/device\033[0m\n\n'
printf '2. Enter this one-time code \033[90m(expires in 15 minutes)\033[0m\n   \033[94m792J-YS4QE\033[0m\n\n'
while [ ! -f "$ANSWER_FILE" ]; do sleep 0.05; done
if [ "$(cat "$ANSWER_FILE")" = ok ]; then
  echo "{\"auth_mode\":\"chatgpt\",\"tokens\":{\"id_token\":\"$ID_TOKEN\"}}" > "$CODEX_HOME/auth.json"
  echo "Successfully logged in"
  exit 0
fi
echo "Error logging in with device code: access denied"
exit 1
"""


def _id_token(email):
    import base64

    claims = {"email": email, "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


@pytest.fixture
def codex(tmp_path, monkeypatch):
    """The host is signed in to Codex as first@example.com; a sign-in from the website is second@example.com."""
    host, bin_dir = tmp_path / "host-codex", tmp_path / "bin"
    host.mkdir()
    bin_dir.mkdir()
    (host / "auth.json").write_text(json.dumps({"tokens": {"id_token": _id_token("first@example.com")}}))
    exe = bin_dir / "codex"
    exe.write_text(CODEX_FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("CODEX_HOME", str(host))
    monkeypatch.setenv("ANSWER_FILE", str(tmp_path / "answer"))
    monkeypatch.setenv("ID_TOKEN", _id_token("second@example.com"))
    monkeypatch.setattr(router, "_logins", {})
    yield tmp_path
    for job in router._logins.values():
        router._end_login(job)


def test_codex_signs_another_chatgpt_account_in_beside_the_host_and_a_model_uses_it(codex):
    st = router.cli_status("codex")
    assert st["signed_in"] and st["account"]["email"] == "first@example.com" and st["can_login"]
    assert st["site"] == "ChatGPT" and st["next_account"] == "codex-2"
    out = router.cli_login("codex", by="admin")
    assert out["state"] == "waiting" and out["url"] == "https://auth.openai.com/codex/device"
    assert out["code"] == "792J-YS4QE" and out["folder"] == "codex-2" and out["site"] == "ChatGPT"
    assert "home" not in out and json.dumps(out)  # what the page gets is plain JSON
    (codex / "answer").write_text("ok")
    done = _until("signed_in", tool="codex")
    assert done["account"]["email"] == "second@example.com"
    assert router.cli_status("codex")["account"]["email"] == "first@example.com"  # the host's sign-in is as it was
    assert router.cli_status("codex", "codex-2")["account"]["email"] == "second@example.com"
    assert router.next_account("codex") == "codex-3"
    m = router.add_model({"name": "Codex of second", "kind": "codex", "account": "codex-2"})
    assert m["account"] == "codex-2" and m["signin"]["email"] == "second@example.com"
    from agent_company.db import ModelEntry, session

    with session() as s:
        info = router.model_info(s.get(ModelEntry, m["id"]))
    assert info["account"]["email"] == "second@example.com" and "data/codex-accounts/codex-2" in info["scope"]


def test_a_failed_codex_sign_in_leaves_no_new_account_folder(codex):
    out = router.cli_login("codex")
    home = router.codex_home(out["folder"])
    assert home.is_dir()
    (codex / "answer").write_text("no")
    assert "access denied" in _until("failed", tool="codex")["error"]
    assert not home.exists() and router.next_account("codex") == out["folder"]


def test_codex_accounts_are_folder_names_and_only_for_codex_and_claude_code(codex):
    with pytest.raises(ValueError):
        router.cli_login("codex", account="../escape")
    with pytest.raises(ValueError):
        router.cli_login("copilot", account="codex-2")
    with pytest.raises(ValueError):
        router.add_model({"name": "x", "kind": "copilot", "account": "codex-2"})
    with pytest.raises(ValueError, match="codex-accounts"):
        router.add_model({"name": "y", "kind": "codex", "account": "../x"})
    m = router.add_model({"name": "Codex without its sign-in", "kind": "codex", "account": "codex-9"})
    with __import__("agent_company.db", fromlist=["session"]).session() as s:
        from agent_company.db import ModelEntry

        ok, why = router.available(s.get(ModelEntry, m["id"]), ignore_limit=True)
    assert not ok and ("codex-accounts/codex-9" in why or "ENABLE_CODEX" in why)


def test_a_codex_model_with_an_account_runs_with_that_codex_home(codex):
    from agent_company import executors

    choice = router.Choice(name="c", kind="codex", model_id="", reason="", account="codex-2")
    assert executors.codex_env(choice)["CODEX_HOME"] == str(router.codex_home("codex-2"))
    assert router.codex_home("codex-2").parent.name == "codex-accounts"
    assert executors.codex_env(router.Choice(name="c", kind="codex", model_id="", reason="")) is None


def test_the_codex_sign_in_api_takes_an_account_folder(codex):
    from agent_company.api import create_app

    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        assert app.post("/api/models/cli/codex/login", json={"account": "../x"}).status_code == 400
        assert app.get("/api/models/cli/copilot?account=x").status_code == 400
        r = app.post("/api/models/cli/codex/login", json={"account": "work"})
        assert r.status_code == 200 and r.json()["folder"] == "work" and r.json()["code"] == "792J-YS4QE"
        (codex / "answer").write_text("ok")
        _until("signed_in", tool="codex")
        assert app.get("/api/models/cli/codex?account=work").json()["account"]["email"] == "second@example.com"
        logins = [a for a in app.get("/api/audit").json()["records"] if a["action"] == "cli_login"]
        assert logins and "work" in json.dumps(logins[0])
