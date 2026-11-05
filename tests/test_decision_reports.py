"""Decision reports (ASEAO07 S07-R-01, AT-007): labelled evidence, limitations, missing parts, independent review."""

import secrets

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import select

from agent_company import decisions, gm, loop
from agent_company.api import create_app
from agent_company.db import Agent, session


def agent_of(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role, Agent.parent_id.is_(None))).first().id


def full(gid, **kw):
    return decisions.record(
        gid, "Database", "SQLite", ["PostgreSQL"], "one writer is enough for one server",
        [{"ref": "bench: 2k writes/s", "class": "measured"}, {"ref": "SQLite docs: one writer", "class": "FACT"},
         {"ref": "load stays below 50 writes/s", "class": "PREDICTION"}, {"type": "hypothesis", "id": 4},
         "a hunch", {"ref": "x", "class": "made-up"}],
        ["one server only"], 0.7, requires_human_approval=True, question="Which database should the API use?",
        criteria=["write load", "operations effort"], limitations=["not for several servers"],
        confidence_note="measured on one machine", **kw)


def test_a_report_keeps_facts_measurements_hypotheses_and_assumptions_apart():
    gid = loop.create_goal("Report")
    d = full(gid)
    r = decisions.report(d["id"])
    by = {c: [x.get("ref", x.get("id")) for x in v] for c, v in r["evidence_by_class"].items()}
    assert by["MEASURED"] == ["bench: 2k writes/s"] and by["FACT"] == ["SQLite docs: one writer"]
    assert by["PREDICTION"] == ["load stays below 50 writes/s"] and by["HYPOTHESIS"] == [4]
    assert by["ASSUMPTION"] == ["one server only"] and by["UNKNOWN"] == ["a hunch", "x"]  # an unknown class is dropped
    assert r["criteria"] == ["write load", "operations effort"] and r["limitations"] == ["not for several servers"]
    # waiting for a person: neither approved nor reviewed yet, and not authorised to implement
    assert r["important"] and r["missing"] == ["responsible approver", "independent review"]
    assert not r["authorised"] and "not authorised to implement" in r["markdown"]
    for part in ("### Measured", "### Facts", "### Hypotheses", "### Assumptions", "## Limitations",
                 "Which database should the API use?", "measured on one machine"):
        assert part in r["markdown"]
    decisions.decide(d["id"], True, "user:admin")
    r = decisions.report(d["id"])
    assert r["missing"] == [] and r["authorised"] and "not authorised" not in r["markdown"]


def test_a_bare_decision_names_what_its_report_lacks():
    d = decisions.record(loop.create_goal("Bare"), "Naming", "snake_case")
    assert not d["important"] and d["authorised"]
    assert d["missing"] == ["rationale", "alternatives", "criteria", "evidence", "limitations", "confidence explanation"]
    assert d["question"] == "Naming"
    crit = decisions.record(None, "Drop the old API", "yes", category="critical")
    assert {"responsible approver", "independent review"} <= set(crit["missing"])


def test_plans_may_give_report_fields_and_single_strings():
    gid = loop.create_goal("Plan")
    [d] = decisions.from_plan(gid, [{"topic": "Cache", "recommendation": "none yet", "alternatives": "Redis",
                                     "criteria": "latency", "limitations": ["guess"], "confidence_note": "no data",
                                     "evidence": [{"ref": "p95 120 ms", "class": "MEASURED"}]}], None)
    assert d["alternatives"] == ["Redis"] and d["criteria"] == ["latency"] and d["limitations"] == ["guess"]
    assert d["evidence"] == [{"ref": "p95 120 ms", "class": "MEASURED"}] and d["confidence_note"] == "no data"
    bare = decisions.record(gid, "Names", "short", evidence=["K1", {"type": "task", "id": 2}])
    assert bare["evidence"] == ["K1", {"type": "task", "id": 2}]  # stored and returned as given
    assert [x["class"] for x in bare["evidence_labelled"]] == ["UNKNOWN", "UNKNOWN"]


def test_an_independent_review_is_never_the_authors_and_a_dispute_goes_to_a_person():
    gid = loop.create_goal("Review")
    dev, qa = agent_of("developer"), agent_of("qa")
    d = decisions.record(gid, "Retry policy", "three retries", ["none"], "transient errors", agent_id=dev)
    assert d["status"] == "approved" and d["approver"] == "auto"
    with pytest.raises(PermissionError, match="author"):
        decisions.review(d["id"], f"agent:{dev}", "agree", agent_id=dev)
    with pytest.raises(ValueError, match="what is wrong"):
        decisions.review(d["id"], f"agent:{qa}", "disagree", agent_id=qa)
    out = decisions.review(d["id"], f"agent:{qa}", "disagree", "retries hide a real outage", agent_id=qa)
    assert out["status"] == "under_review" and out["requires_human_approval"] and not out["authorised"]
    assert [b["id"] for b in decisions.blocking(gid)] == [d["id"]]
    with pytest.raises(ValueError, match="already reviewed"):
        decisions.review(d["id"], f"agent:{qa}", "agree", agent_id=qa)
    assert decisions.decide(d["id"], True, "user:admin", "outage alerts cover it")["status"] == "approved"
    # a person's approval stands: a later dispute is recorded and announced, not reopened
    later = decisions.review(d["id"], "user:bob", "disagree", "still too many")
    assert later["status"] == "approved" and len(later["reviews"]) == 2


def test_gm_toolbox_prints_a_report_and_reviews_as_an_agent(capsys):
    gid = loop.create_goal("Toolbox")
    d = full(gid)
    gm.main(["decision", str(d["id"])])
    assert "# Decision #" in capsys.readouterr().out
    gm.main(["decision-review", str(d["id"]), "concerns", "--note", "benchmark on one machine"])
    assert decisions.get(d["id"])["reviews"][0]["by"] == "agent:general_manager"


def test_decision_report_and_review_api_are_audited():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        d = full(loop.create_goal("Api"))
        r = app.get(f"/api/decisions/{d['id']}").json()
        assert r["evidence_by_class"]["FACT"] and r["markdown"].startswith("# Decision")
        assert app.get("/api/decisions/999999").status_code == 404
        assert app.post(f"/api/decisions/{d['id']}/review", json={"verdict": "maybe"}).status_code == 422
        out = app.post(f"/api/decisions/{d['id']}/review", json={"verdict": "agree", "note": "fine"}).json()
        assert out["reviews"][0]["by"] == "user:admin"
        assert "decision_review" in {r["action"] for r in app.get("/api/audit").json()["records"]}
