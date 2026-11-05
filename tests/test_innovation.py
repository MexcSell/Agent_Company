"""ASEAO10 S10-R-01: a proposed product becomes a project only with its documented case."""
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import auth, loop, prefs, projects, requirements
from agent_company.api import create_app
from agent_company.db import Project, Proposal, User, session

CASE = """Work is done.
PROPOSAL[product]: Release notes generator :: teams keep writing release notes by hand
- Concept: a CLI that turns merged goals into release notes
- Target user: maintainers of the projects here
- Expected value: an hour saved per release
- Feasibility: the goal and commit data is already stored
PROPOSAL: Add a changelog check :: nothing checks it today
"""


def _goal():
    gid = loop.create_goal("Ship a feature")
    return gid


def test_case_lines_are_read_and_an_incomplete_case_stays_a_proposal():
    gid = _goal()
    assert projects.extract_proposals(gid, 1, CASE) == 2
    props = {p["title"]: p for p in projects.list_proposals({"id": 1, "is_admin": True})}
    product, follow = props["Release notes generator"], props["Add a changelog check"]
    assert follow["kind"] == "follow_up" and follow["case_missing"] == []
    assert product["kind"] == "product" and product["case"]["target_user"] == "maintainers of the projects here"
    assert product["case_missing"] == ["requirements", "estimated_cost"]
    with pytest.raises(ValueError, match="incomplete.*Requirements, Estimated cost"):
        projects.decide(product["id"], True, "admin")
    with session() as s:
        assert s.get(Proposal, product["id"]).status == "pending", "not converted, still a proposal"
        assert s.scalars(select(Project).where(Project.name == "Release notes generator")).first() is None
    out = projects.set_case(product["id"], case={"requirements": "Generate notes from approved goals of a release",
                                                 "estimated_cost": "$5 of model time"})
    assert out["case_missing"] == []
    with pytest.raises(PermissionError):  # the GM never turns a proposal into a project
        projects.decide(product["id"], True, "GM", by_human=False)
    made = projects.decide(product["id"], True, "admin")
    assert made["status"] == "approved" and made["spawned_project_id"] and made["spawned_goal_id"] is None
    with session() as s:
        p = s.get(Project, made["spawned_project_id"])
        assert p.name == "Release notes generator" and "Target user: maintainers" in p.description
    reqs = requirements.list_requirements(made["spawned_project_id"])
    assert [(r["text"], r["status"]) for r in reqs] == [("Generate notes from approved goals of a release", "draft")]
    # an ordinary follow-up still becomes a goal
    assert projects.decide(follow["id"], True, "admin")["spawned_goal_id"]


def test_product_proposals_always_wait_for_a_person():
    prefs.set_value("proposal_policy", "superiors")
    gid = _goal()
    projects.extract_proposals(gid, 1, "PROPOSAL[new_project]: A new venture :: big idea\n- Concept: x")
    projects.settle_proposals(gid)
    with session() as s:
        assert s.scalars(select(Proposal).where(Proposal.goal_id == gid)).one().status == "pending"


def test_case_api_is_admin_only_and_audited():
    admin_secret = secrets.token_urlsafe(32)
    auth.register("admin", admin_secret)
    gid = _goal()
    projects.extract_proposals(gid, 1, "PROPOSAL[product]: Widget :: why not")
    pid = projects.list_proposals({"id": 1, "is_admin": True})[0]["id"]
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(admin_secret)
        assert app.post("/api/auth/device", json={"device_secret": admin_secret}).status_code == 200
        r = app.post(f"/api/proposals/{pid}/approve")
        assert r.status_code == 400 and "incomplete" in r.json()["detail"]
        assert app.post(f"/api/proposals/{pid}/case", json={"case": {"bogus": "x"}}).status_code == 400
        full = {k: f"{k} text" for k in projects.CASE_FIELDS}
        r = app.post(f"/api/proposals/{pid}/case", json={"kind": "new_project", "case": full})
        assert r.status_code == 200 and r.json()["case_missing"] == [] and r.json()["kind"] == "new_project"
        r = app.post(f"/api/proposals/{pid}/approve")
        assert r.status_code == 200 and r.json()["spawned_project_id"]
        audit = app.get("/api/audit").json()["records"]
        assert {"proposal_case", "proposal_approve"} <= {a["action"] for a in audit}
