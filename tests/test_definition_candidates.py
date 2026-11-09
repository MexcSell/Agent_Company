"""G-R-19: a study rewrite is a definition candidate an administrator applies after seeing the diff."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import agentdefs, learning, loop, registry
from agent_company.api import create_app
from agent_company.db import Agent, AuditEvent, Notification, session

LESSON = "Always run the tests before reporting."


def _dev() -> int:
    return next(a["id"] for a in registry.list_agents() if a["role"] == "developer")


def _state(agent_id: int) -> tuple[str, int, int, str]:
    d = agentdefs.read(agent_id)
    with session() as s:
        a = s.get(Agent, agent_id)
        return d["text"], a.definition_version, len(d["history"]), agentdefs.system_prompt(a)


def _studied() -> tuple[int, dict]:
    dev = _dev()
    agentdefs.sync(dev, "first")
    learning.learn(dev, loop.create_goal("Teach"), LESSON, False, ["testing"])
    return dev, agentdefs.study(dev)


def test_study_makes_a_pending_candidate_and_leaves_the_definition_alone():
    dev = _dev()
    agentdefs.sync(dev, "first")
    learning.learn(dev, loop.create_goal("Teach"), LESSON, False, ["testing"])
    before = _state(dev)
    out = agentdefs.study(dev)
    assert out["changed"] and out["candidate_id"] and out["model"] == "Offline simulator"
    assert _state(dev) == before, "file, version, git history and system prompt are unchanged"
    c = agentdefs.candidates(dev)
    assert [x["id"] for x in c] == [out["candidate_id"]] and c[0]["status"] == "pending"
    assert c[0]["base_version"] == before[1] and LESSON in c[0]["sections"]["Working method"]


def test_the_diff_shows_the_new_working_method():
    _, out = _studied()
    c = agentdefs.candidate(out["candidate_id"])
    added = [ln for ln in c["diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert any(LESSON in ln for ln in added)
    assert not c["stale"] and c["stale_note"] == ""


def test_apply_writes_one_commit_naming_the_candidate_and_the_actor():
    dev, out = _studied()
    text, version, commits, _ = _state(dev)
    applied = agentdefs.apply_candidate(out["candidate_id"], "user:luca")
    assert applied["status"] == "applied" and applied["decided_by"] == "user:luca" and applied["decided_at"]
    text2, version2, commits2, prompt2 = _state(dev)
    assert text2 != text and LESSON in agentdefs.sections(text2)["Working method"] and LESSON in prompt2
    assert version2 == version + 1 and commits2 == commits + 1
    msg = agentdefs.read(dev)["history"][0]["message"]
    assert f"definition candidate #{out['candidate_id']}" in msg and "user:luca" in msg


def test_discard_leaves_the_file_unchanged():
    dev, out = _studied()
    before = _state(dev)
    d = agentdefs.discard_candidate(out["candidate_id"], "user:luca", "not convinced")
    assert d["status"] == "discarded" and d["note"] == "not convinced" and d["decided_by"] == "user:luca"
    assert _state(dev) == before


def test_a_second_study_supersedes_the_pending_candidate_and_stale_is_noted():
    dev, first = _studied()
    second = agentdefs.study(dev)
    assert second["candidate_id"] != first["candidate_id"]
    by_id = {c["id"]: c for c in agentdefs.candidates(dev)}
    assert by_id[first["candidate_id"]]["status"] == "superseded" and by_id[first["candidate_id"]]["decided_by"] == "system"
    assert by_id[second["candidate_id"]]["status"] == "pending"
    assert [c["id"] for c in agentdefs.candidates(dev, "pending")] == [second["candidate_id"]]
    agentdefs.learn_into(dev, "Knowledge notes", "- a note written meanwhile", "test")
    c = agentdefs.candidate(second["candidate_id"])
    assert c["stale"] and "v" in c["stale_note"], "the definition moved on since the candidate was made"


def test_applying_twice_or_after_discard_raises():
    dev, out = _studied()
    agentdefs.apply_candidate(out["candidate_id"], "user:luca")
    with pytest.raises(ValueError):
        agentdefs.apply_candidate(out["candidate_id"], "user:luca")
    other = agentdefs.study(dev)["candidate_id"]
    agentdefs.discard_candidate(other, "user:luca")
    with pytest.raises(ValueError):
        agentdefs.apply_candidate(other, "user:luca")
    with pytest.raises(ValueError):
        agentdefs.discard_candidate(other, "user:luca")
    with pytest.raises(KeyError):
        agentdefs.candidate(9999)


def test_administrators_are_notified_of_a_new_candidate():
    _, out = _studied()
    with session() as s:
        n = s.scalars(select(Notification).where(Notification.title.contains(f"#{out['candidate_id']}"))).all()
    assert n and all(x.admin_only for x in n)


def test_gm_study_prints_the_candidate_and_its_diff(capsys):
    from agent_company import gm

    dev = _dev()
    learning.learn(dev, loop.create_goal("Teach"), LESSON, False, ["testing"])
    gm.main(["study", str(dev)])
    out = capsys.readouterr().out
    assert "definition candidate #" in out and "status pending" in out and "Offline simulator" in out
    assert f"+- {LESSON}" in out or any(ln.startswith("+") and LESSON in ln for ln in out.splitlines())


def test_the_api_needs_manage_agents_and_audits_apply_and_discard():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as admin:
        admin.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        member, sec = TestClient(admin.app), secrets.token_urlsafe(32)
        member.post("/api/auth/register", json={"name": "member", "device_secret": sec})
        uid = next(u["id"] for u in admin.get("/api/users").json() if u["name"] == "member")
        admin.post(f"/api/users/{uid}/approve")
        member.post("/api/auth/device", json={"device_secret": sec})
        assert member.get("/api/auth/me").status_code == 200

        dev, out = _studied()
        cid = out["candidate_id"]
        assert member.get(f"/api/agents/{dev}/definition/candidates").status_code == 403
        assert member.get(f"/api/definition-candidates/{cid}").status_code == 403
        assert member.post(f"/api/definition-candidates/{cid}/apply").status_code == 403
        assert member.post(f"/api/definition-candidates/{cid}/discard", json={"note": "x"}).status_code == 403

        assert [c["id"] for c in admin.get(f"/api/agents/{dev}/definition/candidates?status=pending").json()] == [cid]
        assert LESSON in admin.get(f"/api/definition-candidates/{cid}").json()["diff"]
        assert admin.get("/api/definition-candidates/9999").status_code == 404
        assert admin.post(f"/api/definition-candidates/{cid}/apply").json()["status"] == "applied"
        assert admin.post(f"/api/definition-candidates/{cid}/apply").status_code == 409
        new = admin.post(f"/api/learning/study?agent_id={dev}").json()["candidate_id"]
        assert admin.post(f"/api/definition-candidates/{new}/discard", json={"note": "no"}).json()["status"] == "discarded"
        with session() as s:
            actions = {(e.action, e.target) for e in s.scalars(select(AuditEvent))}
        assert ("definition_candidate_apply", str(cid)) in actions
        assert ("definition_candidate_discard", str(new)) in actions
        assert ("definition_candidates_read", str(dev)) in actions and ("definition_candidate_read", str(cid)) in actions


def test_study_and_preview_write_nothing_when_the_definition_file_is_missing():
    """Codex's review of goal 11: without a file, study and the preview used to create and commit it."""
    dev = _dev()
    learning.learn(dev, loop.create_goal("Teach"), LESSON, False, ["testing"])
    with session() as s:
        a = s.get(Agent, dev)
        path, version = agentdefs.path_for(a), a.definition_version
    path.unlink(missing_ok=True)
    out = agentdefs.study(dev)
    c = agentdefs.candidate(out["candidate_id"])
    assert not path.exists(), "neither the study visit nor the preview writes the file"
    with session() as s:
        assert s.get(Agent, dev).definition_version == version
    assert any(LESSON in ln for ln in c["diff"].splitlines() if ln.startswith("+"))
    agentdefs.apply_candidate(out["candidate_id"], "user:luca")
    assert path.exists() and LESSON in path.read_text()
