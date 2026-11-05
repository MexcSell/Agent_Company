"""G-R-04: each gated room passes its own eval suite, run as a room trial, before it is enabled; one room at a time."""

import secrets
import subprocess
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import evaluation, governance, performance, registry
from agent_company.api import create_app
from agent_company.db import Agent, AgentVersion, EvalRun, Goal, Run, Task, now, session
from agent_company.quality import split_tag

# what a careful agent writes, per task of the two room suites
GOOD = {
    "password-hash": {"auth.py": "import hashlib, hmac, os\n\n\ndef hash_password(password):\n"
                                 "    salt = os.urandom(16)\n"
                                 "    return salt.hex() + '$' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 10000).hex()\n\n\n"
                                 "def verify_password(password, stored):\n    salt, dk = stored.split('$')\n"
                                 "    new = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 10000)\n"
                                 "    return hmac.compare_digest(new.hex(), dk)\n"},
    "token-check": {"tokens.py": "import hmac\n\n\ndef check_token(given, expected):\n"
                                 "    return bool(given) and bool(expected) and hmac.compare_digest(given, expected)\n"},
    "secret-path": {"secrets_store.py": "from pathlib import Path\n\nVAULT = (Path(__file__).parent / 'vault').resolve()\n\n\n"
                                        "def read_secret(name):\n    p = (VAULT / name).resolve()\n"
                                        "    if not name or VAULT not in p.parents:\n        raise ValueError(name)\n"
                                        "    if not p.is_file():\n        raise KeyError(name)\n"
                                        "    return p.read_text().strip()\n"},
    "accessible-form": {"static/signup.html": '<!doctype html>\n<html lang="en"><body>\n<img src="logo.png" alt="Acme">\n'
                                             '<form><label for="e">Email</label><input id="e" type="email" name="email">\n'
                                             '<label>Name <input type="text" name="name"></label>\n'
                                             '<label><input type="checkbox" name="newsletter"> Newsletter</label>\n'
                                             '<button type="submit">Sign up</button></form></body></html>\n'},
    "dockerfile": {"Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\n"
                                 "RUN pip install --no-cache-dir -r requirements.txt\nCOPY app.py .\nUSER 1000\n"
                                 "EXPOSE 8000\nHEALTHCHECK CMD python -c \"print(1)\"\nCMD [\"python\", \"app.py\"]\n"},
}
# what works but is insecure: only the [security] checks fail
INSECURE = {
    "password-hash": {"auth.py": "import hashlib\n\n\ndef hash_password(p):\n    return hashlib.sha256(p.encode()).hexdigest()\n\n\n"
                                 "def verify_password(p, s):\n    return bool(p) and hash_password(p) == s\n"},
    "token-check": {"tokens.py": "def check_token(g, e):\n    return bool(g) and bool(e) and g == e\n"},
    "secret-path": {"secrets_store.py": "from pathlib import Path\n\nV = Path(__file__).parent / 'vault'\n\n\n"
                                        "def read_secret(name):\n    if not (V / name).exists():\n        raise KeyError(name)\n"
                                        "    return (V / name).read_text().strip()\n"},
}


def _checks(tmp_path, task, extra):
    repo = tmp_path / task["name"]
    for rel, text in ((task.get("files") or {}) | extra).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    out = {}
    for cmd in task["checks"]:
        stage, _, run = split_tag(cmd)
        out[cmd] = (stage, subprocess.run(["/bin/sh", "-c", run], cwd=repo, capture_output=True).returncode == 0)
    return out


def test_room_suites_fail_on_the_seed_pass_with_a_reference_and_catch_insecure_work(tmp_path):
    names = {s["name"]: s for s in evaluation.list_suites()}
    for suite in ("room-security", "room-development"):
        data = evaluation.load_suite(suite)
        assert names[suite]["kind"] == "room" and data["room"] in performance.gated_rooms()
        for task in data["tasks"]:
            assert any(registry_sensitive(p) for p in task["allowed_paths"]) or suite != "room-security", task["name"]
            for cmd in task["checks"]:
                assert governance.check_command(split_tag(cmd)[2]) is None, cmd
            assert not all(ok for _, ok in _checks(tmp_path / "seed", task, {}).values()), task["name"]
            good = _checks(tmp_path / "good", task, GOOD[task["name"]])
            assert all(ok for _, ok in good.values()), (task["name"], good)
            if task["name"] in INSECURE:
                bad = _checks(tmp_path / "bad", task, INSECURE[task["name"]])
                assert all(ok for stage, ok in bad.values() if stage != "security"), task["name"]
                assert not all(ok for stage, ok in bad.values() if stage == "security"), task["name"]


def registry_sensitive(path: str) -> bool:
    from agent_company.teststrategy import sensitive

    return sensitive(path)  # a security-room task is high-risk, so its plan gets a security review


def _agent(role: str) -> int:
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


def _goal(trial: dict | None = None, origin: str = "eval") -> int:
    with session() as s:
        g = Goal(title="g", description="", origin=origin, trial=trial or {})
        s.add(g)
        s.flush()
        return g.id


def test_a_disabled_room_is_staffed_only_for_its_own_room_trial():
    sec = _agent("security")
    with session() as s:
        assert not s.get(Agent, sec).enabled
    plain, other, own = _goal(origin="user"), _goal({"room": "analysis"}), _goal({"room": "security"})
    assert registry.resolve_role("security", plain)[0] == "qa"
    assert registry.resolve_role("security", other)[0] == "qa"
    assert registry.resolve_role("security", own) == ("security", None)
    with session() as s:  # the room is not "enabled" by a trial: an ordinary eval goal is not one
        assert registry.trial_room(s, _goal()) is None
    a = registry.staff("security", ["security_review"], own)
    assert a.id == sec
    b = registry.staff("security", ["security_review"], own)  # busy: a clone, disabled like its parent
    with session() as s:
        clone = s.get(Agent, b.id)
        assert clone.parent_id == sec and not clone.enabled
    registry.release(sec, own)
    registry.release(b.id, own)
    with session() as s:  # outside its trial the room stays off: neither the agent nor its clone is enabled
        assert not s.scalars(select(Agent).where(Agent.role == "security", Agent.enabled)).first()
    for gid in (plain, other):
        with session() as s:
            pool = registry._usable(registry.trial_room(s, gid))
            assert not s.scalars(select(Agent).where(Agent.role == "security", pool)).first()


def test_run_suite_refuses_a_room_that_is_not_gated_or_not_the_suites():
    with pytest.raises(ValueError, match="not a room of a gated role"):
        evaluation.run_suite("smoke", "x", repeats=1, trial={"room": "general_management"})
    with pytest.raises(ValueError, match="measures room security"):
        evaluation.run_suite("room-security", "x", repeats=1, trial={"room": "analysis"})
    with session() as s:
        assert not s.scalars(select(Goal).where(Goal.origin == "eval")).first()


def _label(label: str, suite: str, room: str | None, passed: int, total: int = 3, worker: str | None = None,
           at=None) -> None:
    """`total` real-model runs of one task under `label`; `worker` (a role) did a task in each."""
    worker_id = _agent(worker) if worker else None
    for rep in range(total):
        gid = _goal({"room": room} if room else {})
        with session() as s:
            s.add(EvalRun(label=label, suite=suite, task="t", repeat=rep, goal_id=gid, passed=rep < passed,
                          **({"created_at": at} if at else {})))
            s.add(Run(goal_id=gid, model_name="Claude Sonnet 5.5", kind="llm", cost=0.01))
            if worker_id:
                s.add(Task(goal_id=gid, iteration=1, key="review", title="review", role=worker, agent_id=worker_id))


def test_the_room_gate_needs_its_suite_the_room_working_and_no_regression():
    _label("base", "smoke", None, 3)
    assert performance.enablement_gate()["met"]
    gate = performance.room_gate("security")
    assert not gate["met"]
    assert any("no room-security run yet" in r for r in gate["reasons"])
    assert any("no smoke run with room security" in r for r in gate["reasons"])
    assert any("no eval suite for room analysis" in r for r in performance.room_gate("analysis")["reasons"])
    _label("sec-idle", "room-security", "security", 3)  # passed, but the room's agents did nothing
    assert any("did no task in 3 run(s)" in r for r in performance.room_gate("security")["reasons"])
    _label("sec", "room-security", "security", 3, worker="security")
    _label("smoke-sec-worse", "smoke", "security", 2)  # 67 % with the room against 100 % without
    gate = performance.room_gate("security")
    assert any("less than the pipeline's 100%" in r for r in gate["reasons"]), gate["reasons"]
    assert performance.enablement_gate()["metrics"]["label"] == "base"  # a room trial is not the pipeline's label
    _label("smoke-sec", "smoke", "security", 3)
    gate = performance.room_gate("security")
    assert gate["met"], gate["reasons"]
    assert gate["suite"]["label"] == "sec" and gate["with_room"]["change"] == 0.0


def test_one_room_at_a_time():
    _label("base", "smoke", None, 3, at=now() - timedelta(hours=2))
    _label("sec", "room-security", "security", 3, worker="security")
    _label("smoke-sec", "smoke", "security", 3)
    assert performance.room_gate("security")["met"]
    scout = _agent("tech_scout")
    with session() as s:  # another gated room enabled after the pipeline's label
        a = s.get(Agent, scout)
        a.enabled = True
        registry.record_version(s, a, "admin", "enabled")
    gate = performance.room_gate("security")
    assert any("one room at a time" in r and a_room(scout) in r for r in gate["reasons"]), gate["reasons"]
    with session() as s:
        assert s.scalars(select(AgentVersion).where(AgentVersion.agent_id == scout)).first()
    _label("base2", "smoke", None, 3)  # the pipeline measured again with that room in it
    assert performance.room_gate("security")["met"]


def a_room(agent_id: int) -> str:
    with session() as s:
        return s.get(Agent, agent_id).room_key


def test_enabling_or_resuming_a_gated_agent_needs_its_rooms_gate():
    sec = _agent("security")
    _label("base", "smoke", None, 3)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        r = app.patch(f"/api/agents/{sec}", json={"enabled": True})
        assert r.status_code == 409 and "room security is not measured reliable" in r.json()["detail"]
        r = app.post(f"/api/agents/{sec}/lifecycle", json={"state": "active"})  # resuming enables it as well
        assert r.status_code == 409, r.json()
        gate = app.get("/api/performance/enablement?room=security").json()
        assert not gate["met"] and gate["room"] == "security"
        assert "security" in app.get("/api/performance/enablement").json()["rooms"]
        _label("sec", "room-security", "security", 3, worker="security")
        _label("smoke-sec", "smoke", "security", 3)
        assert app.patch(f"/api/agents/{sec}", json={"enabled": True}).json()["enabled"] is True


def test_a_room_suite_run_staffs_the_room_in_its_goals():
    """Offline, end to end: the room suite's goal is a room trial and the security review is planned for the room
    (the simulator fails the task itself; the measurement only counts with real models)."""
    sec = _agent("security")
    out = evaluation.run_suite("room-security", "trial", repeats=1, only=["token-check"])
    assert out["runs"] == 1
    with session() as s:
        g = s.scalars(select(Goal).where(Goal.origin == "eval")).one()
        assert g.trial.get("room") == "security"
        review = [t for t in s.scalars(select(Task).where(Task.goal_id == g.id)) if t.role == "security"]
        assert review, "the high-risk change got no security review"
        worker = s.get(Agent, review[0].agent_id)
        assert worker.room_key == "security" and (worker.id == sec or worker.parent_id == sec)
        assert not s.scalars(select(Agent).where(Agent.role == "security", Agent.enabled)).first()
    assert not any("did no task" in r for r in performance.room_gate("security")["reasons"])
