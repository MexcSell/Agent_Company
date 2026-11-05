"""Requirements and traceability (ASEAO08 S08-R-01, AT-002, AT-003, AT-031)."""

import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_company import decisions, executors, gm, loop, projects, requirements
from agent_company.api import create_app


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def with_plan(monkeypatch, keys):
    real = executors.standard_plan

    def plan(goal):
        out = real(goal)
        out["tasks"][1]["requirement_keys"] = keys  # the build task
        return out

    monkeypatch.setattr(executors, "standard_plan", plan)


def test_keys_versions_and_who_may_move_a_requirement():
    p = projects.create("Shop")
    a = requirements.create(p["id"], "Customers can pay by card", acceptance="a card payment succeeds")
    b = requirements.create(p["id"], "Prices are shown with VAT", "non_functional", key="s08-r-99")
    assert (a["key"], a["status"], a["version"], a["acceptance"]) == ("REQ-001", "draft", 1, ["a card payment succeeds"])
    assert b["key"] == "S08-R-99" and requirements.create(p["id"], "Orders get a number")["key"] == "REQ-002"
    with pytest.raises(ValueError, match="already exists"):
        requirements.create(p["id"], "again", key="REQ-001")
    with pytest.raises(PermissionError, match="only a person"):
        requirements.set_status(a["id"], "approved", "agent:general_manager")
    with pytest.raises(ValueError, match="no check passed"):
        requirements.set_status(a["id"], "verified", "user:admin")
    assert requirements.set_status(a["id"], "approved", "user:admin")["status"] == "approved"
    same = requirements.update(a["id"], "user:admin", subject="payments")
    assert same["version"] == 1 and same["status"] == "approved"
    new = requirements.update(a["id"], "user:admin", text="Customers can pay by card or invoice")
    assert new["version"] == 2 and new["status"] == "draft", "the new version was never approved"
    assert new["history"][0]["text"] == "Customers can pay by card" and new["history"][0]["status"] == "approved"
    requirements.set_status(a["id"], "retired", "user:admin")
    with pytest.raises(ValueError, match="retired"):
        requirements.update(a["id"], "user:admin", text="x")


def test_a_goal_traces_its_requirements_through_the_gate_and_the_merge(monkeypatch):
    p = projects.create("Traced")
    r1 = requirements.create(p["id"], "Write a work log", status="approved")
    r2 = requirements.create(p["id"], "Say hello", status="approved")
    draft = requirements.create(p["id"], "Log in German")
    with pytest.raises(ValueError, match="unknown requirement"):
        loop.create_goal("x", project_id=p["id"], requirement_keys=["REQ-404"])
    with pytest.raises(ValueError, match="belong to a project"):
        loop.create_goal("x", requirement_keys=["REQ-001"])
    with_plan(monkeypatch, ["REQ-001", "REQ-003", "REQ-777"])
    gid = loop.create_goal("Do REQ-002 now", project_id=p["id"], requirement_keys=["req-001"],
                           quality_commands=["test -f WORKLOG.md"], max_iterations=1)
    assert loop._goal(gid).requirement_keys == ["REQ-001", "REQ-002"], "named in the title"
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    t = {x["key"]: x for x in requirements.traceability(p["id"])["requirements"]}
    assert t["REQ-001"]["implemented"] and t["REQ-001"]["tested"] and not t["REQ-001"]["merged"]
    assert not t["REQ-002"]["implemented"] and t["REQ-002"]["tested"], "on the goal, on no task"
    assert t["REQ-003"]["implemented"]
    sha = loop.approve(gid)["merge_commit"]
    t = {x["key"]: x for x in requirements.traceability(p["id"])["requirements"]}
    assert t["REQ-001"]["merged"] == [sha] and t["REQ-001"]["goals"] == [gid]
    assert requirements.get(r1["id"])["status"] == "verified" and requirements.get(r2["id"])["status"] == "verified"
    assert requirements.get(draft["id"])["status"] == "draft", "merged work does not approve a draft"
    detail = requirements.get(r1["id"], links=True)
    assert {x["kind"] for x in detail["links"]} == {"goal", "task", "check", "merge"}
    # a new version is untested and unmerged until a goal works on it again
    requirements.update(r1["id"], "user:admin", text="Write a work log with dates")
    t = {x["key"]: x for x in requirements.traceability(p["id"])["requirements"]}
    assert not (t["REQ-001"]["tested"] or t["REQ-001"]["merged"]) and t["REQ-001"]["earlier_versions_linked"]


def test_the_planner_sees_the_goals_requirements(monkeypatch):
    p = projects.create("Planned")
    requirements.create(p["id"], "Write a work log file for every change", status="approved")
    requirements.create(p["id"], "Unrelated: invoices are numbered", status="approved")
    prompts = []
    real = executors.Executor.run

    def spy(self, req):
        if req.purpose == "plan":
            prompts.append(req.prompt)
        return real(self, req)

    monkeypatch.setattr(executors.Executor, "run", spy)
    gid = loop.create_goal("Implement REQ-001", project_id=p["id"], max_iterations=1)
    loop.run_goal(gid)
    assert "REQ-001 [approved, v1]: Write a work log" in prompts[0] and "requirement_keys" in prompts[0]
    assert "REQ-002" not in prompts[0], "only related requirements, not the whole list"


def test_contradicting_requirements_become_one_decision_for_a_person():
    p = projects.create("Rules")
    a = requirements.create(p["id"], "Agents get access to production servers")
    b = requirements.create(p["id"], "Agents must never get access to production servers")
    requirements.create(p["id"], "Agents must not deploy on Fridays")  # a different subject
    a, b = requirements.get(a["id"]), requirements.get(b["id"])
    assert [c["key"] for c in a["conflicts"]] == [b["key"]] and [c["key"] for c in b["conflicts"]] == [a["key"]]
    d = decisions.get(a["conflicts"][0]["decision_id"])
    assert d["requires_human_approval"] and d["status"] == "escalated" and d["project_id"] == p["id"]
    assert len(d["alternatives"]) == 2 and d["limitations"]
    requirements.update(b["id"], "user:admin", subject="production access")  # re-checked, not recorded twice
    assert len(requirements.get(b["id"])["conflicts"]) == 1
    x = requirements.create(p["id"], "Use dark mode", subject="theme")
    y = requirements.create(p["id"], "No dark theme anywhere", subject="Theme")
    assert requirements.get(x["id"])["conflicts"][0]["key"] == y["key"]


def test_import_of_the_platforms_own_documents_is_idempotent(tmp_path):
    docs = tmp_path / "repo" / "docs" / "requirements"
    docs.mkdir(parents=True)
    (docs / "SPEC-99.md").write_text(
        "| ID | Requirement | Priority | Status |\n| --- | --- | --- | --- |\n"
        "| S99-FR-01 | Register things | P0 | done (`x.py`) |\n| S99-FR-02 | Delete things | P1 | open → S99-R-01 |\n"
        "| S99-FR-03 | Gate things | P1 | gated |\n\n"
        "### S99-R-01 Deleting — §3\n- Things can be deleted.\n- **Status: partial** (soft delete only).\n\n"
        "| ID | Item | Spec |\n| --- | --- | --- |\n| S99-G-01 | Later things | §9 |\n")
    import subprocess

    subprocess.run(["git", "init", "-q", str(docs.parents[1])], check=True)
    p = projects.create("Platform", path=str(docs.parents[1]))
    out = requirements.import_docs(p["id"], "user:admin")
    assert (out["added"], out["changed"], out["unchanged"]) == (5, 0, 0)
    got = {r["key"]: r for r in requirements.list_requirements(p["id"])}
    assert {k: got[k]["status"] for k in got} == {"S99-FR-01": "implemented", "S99-FR-02": "approved",
                                                  "S99-FR-03": "draft", "S99-R-01": "approved", "S99-G-01": "draft"}
    assert got["S99-R-01"]["text"].startswith("Deleting — §3\n- Things can be deleted.")
    requirements.set_status(got["S99-FR-03"]["id"], "approved", "user:admin")
    assert requirements.import_docs(p["id"], "user:admin")["unchanged"] == 5
    assert requirements.get(got["S99-FR-03"]["id"])["status"] == "approved", "a person's status stays"
    (docs / "SPEC-99.md").write_text((docs / "SPEC-99.md").read_text().replace("Register things", "Register items"))
    assert requirements.import_docs(p["id"], "user:admin")["changed"] == 1
    r = requirements.get(got["S99-FR-01"]["id"])
    assert r["version"] == 2 and r["status"] == "implemented"


def test_the_repositorys_own_documents_parse():
    items = requirements.parse_docs(Path(__file__).parents[1] / "docs" / "requirements")
    keys = {i["key"] for i in items}
    assert {"S08-R-01", "S07-R-01", "S14-R-01", "SE-R-01", "WS-R-01", "S10-G-01"} <= keys and len(items) > 200


def test_gm_toolbox_writes_drafts_and_names_requirements_on_goals(capsys):
    p = projects.create("Toolbox")
    gm.main(["requirement", str(p["id"]), "Reports export to CSV", "--accept", "a CSV file is written"])
    assert requirements.list_requirements(p["id"])[0]["status"] == "draft"
    capsys.readouterr()
    gm.main(["requirements", str(p["id"]), "--trace"])
    assert '"total": 1' in capsys.readouterr().out


def test_requirements_api_checks_permissions_and_audits():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        p = projects.create("Api")
        r = app.post(f"/api/projects/{p['id']}/requirements", json={"text": "Search finds orders by number",
                                                                   "acceptance": ["order 7 is found"]}).json()
        assert r["key"] == "REQ-001" and r["source"] == "user:admin"
        assert app.post(f"/api/projects/{p['id']}/requirements", json={"text": "x x x", "kind": "wish"}).status_code == 422
        assert app.post(f"/api/requirements/{r['id']}/status", json={"status": "approved"}).json()["status"] == "approved"
        assert app.patch(f"/api/requirements/{r['id']}", json={"text": "Search finds orders by number or name"}
                         ).json()["version"] == 2
        assert app.post(f"/api/requirements/{r['id']}/status", json={"status": "verified"}).status_code == 400
        assert app.get(f"/api/projects/{p['id']}/traceability").json()["counts"]["total"] == 1
        assert app.get(f"/api/requirements/{r['id']}").json()["links"] == []
        assert app.get("/api/requirements/9999").status_code == 404
        assert app.post(f"/api/projects/{p['id']}/requirements/import").status_code == 400  # no docs folder
        actions = {x["action"] for x in app.get("/api/audit").json()["records"]}
        assert {"requirement_create", "requirement_status", "requirement_update"} <= actions
