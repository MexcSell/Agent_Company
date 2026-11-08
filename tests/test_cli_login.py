"""Signing the Copilot CLI in to GitHub from the website: the device code reaches the page, the result is confirmed
from the CLI's own config (never from what it printed), and a CLI model records whose sign-in it was registered with."""

import json
import secrets
import stat
import time

import pytest
from fastapi.testclient import TestClient

from agent_company import router

# what `copilot login --device-code` prints, then it waits until the test confirms or denies on "GitHub"
FAKE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "GitHub Copilot CLI 1.0.107."; exit 0; fi
echo "To authenticate, visit https://github.com/login/device and enter code ABCD-12EF"
echo "Waiting for authorization..."
while [ ! -f "$HOME/answer" ]; do sleep 0.05; done
if [ "$(cat "$HOME/answer")" = ok ]; then
  mkdir -p "$HOME/.copilot"
  echo '{"logged_in_users":[{"host":"https://github.com","login":"octocat"}],"last_logged_in_user":{"host":"https://github.com","login":"octocat"}}' > "$HOME/.copilot/config.json"
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


def test_the_code_reaches_the_page_and_a_confirmed_sign_in_is_reported_with_the_account(copilot):
    assert router.cli_status("copilot")["signed_in"] is False
    out = router.cli_login("copilot", by="admin")
    assert out["state"] == "waiting" and out["url"] == "https://github.com/login/device" and out["code"] == "ABCD-12EF"
    assert 0 < out["seconds_left"] <= router.LOGIN_TIMEOUT and "proc" not in out
    assert router.cli_login("copilot")["code"] == "ABCD-12EF"  # a second click follows the running sign-in
    (copilot / "answer").write_text("ok")
    done = _until("signed_in")
    assert done["account"]["email"] == "octocat" and done["account"]["method"] == "GitHub account"
    st = router.cli_status("copilot")
    assert st["signed_in"] and st["account"]["email"] == "octocat" and st["can_login"]


def test_a_denied_sign_in_fails_with_the_reason(copilot):
    router.cli_login("copilot")
    (copilot / "answer").write_text("no")
    out = _until("failed")
    assert "denied" in out["error"] and not router.cli_status("copilot")["signed_in"]


def test_a_cli_that_exits_cleanly_without_signing_in_is_not_reported_signed_in(copilot):
    script = copilot.parent / "bin" / "copilot"
    script.write_text(FAKE.replace('mkdir -p "$HOME/.copilot"', "true").replace(
        "echo '{\"logged_in_users\"", "true # "))
    router.cli_login("copilot")
    (copilot / "answer").write_text("ok")
    assert _until("failed")["state"] == "failed"  # exit 0, but its config names nobody


def test_cancel_ends_the_waiting_cli(copilot):
    router.cli_login("copilot")
    proc = router._logins["copilot"]["proc"]
    assert router.cancel_login("copilot")["state"] == "cancelled"
    proc.wait(timeout=5)
    assert router.login_state("copilot")["state"] == "cancelled"


def test_a_cli_model_records_the_sign_in_it_was_registered_with(copilot):
    router.cli_login("copilot")
    (copilot / "answer").write_text("ok")
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
        _until("signed_in")
        assert app.get("/api/models/cli/copilot/login").json()["account"]["email"] == "octocat"
        actions = {(a["action"], a["actor"]) for a in app.get("/api/audit").json()["records"]}
        assert ("cli_login", "user:admin") in actions and ("cli_login_result", "user:admin") in actions
