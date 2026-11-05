"""Hypotheses (ASEAO07 S07-R-03, AT-008): their life, the agent rule, and experiments that test them."""

import json
import secrets

import pytest
from fastapi.testclient import TestClient

from agent_company import experiments, gm, hypotheses
from agent_company.api import create_app
from agent_company.db import session


def new(**kw):
    return hypotheses.create("Smaller tasks pass the gate more often", "fewer files per task", actor="user:admin", **kw)


def test_a_hypothesis_follows_its_life_and_nothing_else():
    h = new()
    assert h["status"] == "proposed" and h["next"] == ["archived", "investigating"]
    with pytest.raises(ValueError, match="cannot become supported"):
        hypotheses.set_status(h["id"], "supported", "user:admin", "obvious")
    for to in ("investigating", "testing", "inconclusive", "investigating", "testing", "rejected", "archived"):
        assert hypotheses.set_status(h["id"], to, "user:admin")["status"] == to
    with pytest.raises(ValueError):
        hypotheses.set_status(h["id"], "testing", "user:admin")
    with pytest.raises(ValueError, match="archived"):
        hypotheses.add_evidence(h["id"], "supporting", {"type": "goal", "id": 1}, "user:admin")


def test_an_agent_cannot_mark_a_hypothesis_supported_without_a_test_result():
    h = new()
    hypotheses.set_status(h["id"], "investigating", "agent:general_manager")
    hypotheses.set_status(h["id"], "testing", "agent:general_manager")
    with pytest.raises(PermissionError, match="recorded test result"):
        hypotheses.set_status(h["id"], "supported", "agent:general_manager", "I am sure")
    hypotheses.record_result(h["id"], "9 of 10 small tasks passed, 5 of 10 large", {"source": "eval:smoke",
                                                                                 "passed": True}, "agent:qa")
    out = hypotheses.set_status(h["id"], "supported", "agent:general_manager")
    assert out["status"] == "supported" and out["test_result"]["source"] == "eval:smoke"


def test_a_person_concluding_without_a_result_must_say_why():
    h = new()
    hypotheses.set_status(h["id"], "investigating", "user:admin")
    hypotheses.set_status(h["id"], "testing", "user:admin")
    with pytest.raises(ValueError, match="say why"):
        hypotheses.set_status(h["id"], "supported", "user:admin")
    assert hypotheses.set_status(h["id"], "supported", "user:admin", "seen in the last 20 goals")["note"]


def test_evidence_for_and_against_is_kept_with_its_author():
    h = new()
    hypotheses.add_evidence(h["id"], "supporting", {"type": "goal", "id": 4, "note": "passed first try"}, "user:a")
    out = hypotheses.add_evidence(h["id"], "contradicting", {"type": "url", "id": "https://x.example"}, "agent:qa")
    assert out["supporting"][0]["id"] == 4 and out["contradicting"][0]["by"] == "agent:qa"
    with pytest.raises(ValueError):
        hypotheses.add_evidence(h["id"], "neutral", {"note": "x"}, "user:a")
    with pytest.raises(ValueError):
        hypotheses.add_evidence(h["id"], "supporting", {}, "user:a")


@pytest.mark.parametrize("comparison, status", [
    ({"better": True, "pass_rate_change": 0.3, "samples": {"baseline": 3, "candidate": 3}}, "supported"),
    ({"better": False, "pass_rate_change": -0.1, "samples": {"baseline": 3, "candidate": 3}}, "rejected"),
    ({"better": False, "samples": {"baseline": 3, "candidate": 0}}, "inconclusive"),
    ({"passed": True, "pass_rate_change": 0.2}, "supported"),  # an evolution smoke trial
])
def test_an_experiment_tests_its_hypothesis(comparison, status):
    with session() as s:
        e = experiments.record(s, "route", role="qa", candidate={"model": "Claude Sonnet 5.5"},
                               hypothesis="Sonnet reviews as well as Opus", suite="smoke", repeats=3)
        eid = e.id
    [h] = hypotheses.list_hypotheses(experiment_id=eid)
    assert h["status"] == "testing" and "smoke" in h["test_method"] and h["expected_outcome"]
    with session() as s:
        experiments.update(s, eid, status="review", decision="the verdict", comparison=comparison)
    h = hypotheses.get(h["id"])
    assert h["status"] == status and h["test_result"]["source"] == f"experiment:{eid}"
    assert h["actual_outcome"] == "the verdict"


def test_an_experiment_without_a_hypothesis_records_none():
    with session() as s:
        eid = experiments.record(s, "route", role="qa", candidate={"model": "x"}).id
    assert hypotheses.list_hypotheses(experiment_id=eid) == []


def test_the_gm_toolbox_records_hypotheses_as_an_agent(capsys):
    gm.main(["hypothesis", "Caching the catalog halves page load", "--reason", "profile", "--test", "timing script"])
    hid = json.loads(capsys.readouterr().out)["id"]
    gm.main(["hypothesis-status", str(hid), "investigating"])
    gm.main(["hypothesis-status", str(hid), "testing"])
    capsys.readouterr()
    with pytest.raises(PermissionError):
        gm.main(["hypothesis-status", str(hid), "supported"])
    gm.main(["hypotheses", "--status", "testing"])
    assert json.loads(capsys.readouterr().out)[0]["created_by"] == "agent:general_manager"


def test_the_api_records_and_audits_every_step():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        h = app.post("/api/hypotheses", json={"statement": "Typed code has fewer defects", "reason": "studies"}).json()
        hid = h["id"]
        assert app.post(f"/api/hypotheses/{hid}/evidence", json={"side": "supporting", "type": "url",
                                                                 "id": "https://x.example"}).status_code == 200
        for to in ("investigating", "testing"):
            assert app.post(f"/api/hypotheses/{hid}/status", json={"status": to}).json()["status"] == to
        assert app.post(f"/api/hypotheses/{hid}/status", json={"status": "supported"}).status_code == 400
        r = app.post(f"/api/hypotheses/{hid}/result", json={"actual_outcome": "12% fewer", "passed": True}).json()
        assert r["test_result"]["passed"] is True
        assert app.post(f"/api/hypotheses/{hid}/status", json={"status": "supported"}).json()["status"] == "supported"
        assert app.get("/api/hypotheses?status=supported").json()[0]["id"] == hid
        actions = {a["action"] for a in app.get("/api/audit").json()["records"]}
        assert {"hypothesis_create", "hypothesis_evidence", "hypothesis_status", "hypothesis_result"} <= actions
