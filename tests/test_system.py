import secrets

from fastapi.testclient import TestClient

from agent_company import supervisor, system
from agent_company.api import create_app


def test_bad_code_never_replaces_the_service(tmp_path, monkeypatch):
    bad = tmp_path / "pkg"
    bad.mkdir()
    (bad / "broken.py").write_text("def f(:\n")
    monkeypatch.setattr(supervisor, "SRC", bad)
    sup = supervisor.Supervisor(port=4999)
    problem = sup.check_candidate()
    assert problem and problem.startswith("syntax check failed")
    spawned = []
    monkeypatch.setattr(sup, "spawn", lambda *a, **k: spawned.append(a))
    sup.safe_restart("test")
    assert spawned == [] and "syntax check failed" in sup.state["last_error"]
    assert supervisor.read_state()["running"] is True  # the state file names this (live) process


def test_restart_request_needs_a_running_supervisor():
    assert supervisor.request_restart("x") is False


def test_system_status_and_restart_api():
    st = system.status()
    assert st["host"]["cpus"] and st["service"]["pid"] and "branch" in st["repository"]
    with TestClient(create_app(start_background=False)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        assert app.get("/api/system").json()["work"]["running_tasks"] == 0
        assert app.post("/api/system/restart").status_code == 409
