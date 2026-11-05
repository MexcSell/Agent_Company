import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from agent_company import loop, management, prefs, projects
from agent_company.api import create_app
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import ActionItem, Defect, Goal, GoalTransition, Insight, Lesson, Proposal, Risk, now, session
from agent_company.workflow import set_status

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def users():
    from agent_company import auth

    auth.register("admin", secrets.token_urlsafe(32))


def _project(name="Plans"):
    return projects.create(name)


def _goal(pid, status="queued", **cols):
    gid = loop.create_goal(f"Work for {pid}", project_id=pid)
    with session() as s:
        g = s.get(Goal, gid)
        for k, v in cols.items():
            setattr(g, k, v)
        if status != "queued":
            set_status(s, g, status, "test")
    return gid


def test_milestone_cannot_complete_while_a_mandatory_criterion_is_unmet():
    p = _project()
    gid = _goal(p["id"], "running")
    m = management.create_milestone(p["id"], "Beta", goal_ids=[gid], deliverables="Release notes\nInstaller",
                                    criteria=[{"kind": "command", "text": "test -f DONE.md"},
                                              {"kind": "manual", "text": "Owner signed off"},
                                              {"kind": "manual", "text": "Nice to have", "mandatory": False}])
    assert m["deliverables"] == ["Release notes", "Installer"] and m["progress"] == 0
    with pytest.raises(ValueError, match="not approved"):
        management.complete_milestone(m["id"], "admin")
    after = management.list_milestones(p["id"])[0]
    assert after["criteria"][0]["checked_at"] and not after["criteria"][0]["met"]  # the command was re-run
    # a failed command criterion becomes an action item
    assert any(a["source"] == f"criterion:{m['id']}:0" for a in management.ranked_items(p["id"]))

    with session() as s:
        set_status(s, s.get(Goal, gid), "approved", "test")
    management.set_criterion(m["id"], 1, True, "admin")
    with pytest.raises(ValueError, match="DONE.md"):  # the command still fails on the project's HEAD
        management.complete_milestone(m["id"], "admin")
    with pytest.raises(ValueError, match="running them"):
        management.set_criterion(m["id"], 0, True, "admin")

    import subprocess

    (management.Path(p["path"]) / "DONE.md").write_text("done\n")
    subprocess.run(["git", "add", "DONE.md"], cwd=p["path"], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "done"], cwd=p["path"],
                   check=True)
    done = management.complete_milestone(m["id"], "admin")
    assert done["status"] == "completed" and done["progress"] == 1.0
    assert not management._check_dir(m["id"]).exists()  # the throwaway worktree is gone
    # the criterion's action item closed once the criterion was met
    assert not any(a["source"].startswith("criterion:") for a in management.ranked_items(p["id"]))


def test_refused_command_criteria_and_foreign_goals_are_rejected():
    p, other = _project("One"), _project("Two")
    with pytest.raises(ValueError, match="refused"):
        management.create_milestone(p["id"], "Bad", criteria=[{"kind": "command", "text": "sudo rm -rf /"}])
    gid = _goal(other["id"])
    with pytest.raises(ValueError, match="not goals of this project"):
        management.create_milestone(p["id"], "Bad", goal_ids=[gid])


def test_blockers_open_and_close_with_their_cause():
    p = _project()
    paused = _goal(p["id"], "running")
    with session() as s:
        g = s.get(Goal, paused)
        set_status(s, g, "paused", "budget")
        g.checkpoint = {"paused_for": "budget", "paused_why": "spent $2.00 of $2.00"}
    waiting = _goal(p["id"], "running")
    with session() as s:
        set_status(s, s.get(Goal, waiting), "awaiting_approval", "gate passed")
    management.sync(p["id"])
    causes = {r["cause"] for r in management.list_risks(p["id"])}
    assert causes == {f"goal:{paused}:budget"}  # the approval wait is still short

    with session() as s:  # the approval has now waited two days
        for t in s.query(GoalTransition).filter(GoalTransition.goal_id == waiting):
            t.created_at = now() - timedelta(days=2)
    management.sync(p["id"])
    management.sync(p["id"])  # idempotent
    blockers = management.list_risks(p["id"])
    assert sorted(r["cause"] for r in blockers) == sorted([f"goal:{paused}:budget", f"goal:{waiting}:approval"])
    assert all(r["kind"] == "blocker" and r["status"] == "open" for r in blockers)
    text = projects.status_text(p["id"])
    assert text.startswith("Open blockers:") and "budget used up" in text and "Next:" in text
    assert projects.standup(p["id"]).splitlines()[1] == "Open blockers:"

    with session() as s:
        set_status(s, s.get(Goal, paused), "cancelled", "test")
        set_status(s, s.get(Goal, waiting), "approved", "test")
    management.sync(p["id"])
    assert management.list_risks(p["id"]) == []
    with session() as s:
        assert {r.status for r in s.query(Risk)} == {"resolved"}
        # their "Resolve blocker" action items were closed too
        assert {a.status for a in s.query(ActionItem).filter(ActionItem.source.like("risk:%"))} == {"done"}


def test_failed_dependency_goal_blocks_its_milestone():
    p = _project()
    gid = _goal(p["id"], "running")
    m = management.create_milestone(p["id"], "Launch", goal_ids=[gid])
    with session() as s:
        set_status(s, s.get(Goal, gid), "failed", "test")
    management.sync(p["id"])
    [b] = management.list_risks(p["id"])
    assert b["cause"] == f"milestone:{m['id']}:goal:{gid}" and b["category"] == "requirements"
    management.update_milestone(m["id"], {"goal_ids": []})
    management.sync(p["id"])
    assert management.list_risks(p["id"]) == []


def test_action_items_are_generated_ranked_and_closed():
    p = _project()
    gid = _goal(p["id"], "running")
    with session() as s:
        s.add(Proposal(goal_id=gid, project_id=p["id"], title="Add caching"))
        s.add(Defect(goal_id=gid, check="pytest", severity="critical", detail="2 failed"))
        s.add(Defect(goal_id=gid, check="lint", severity="low"))
    manual = management.create_item(p["id"], "Write the release notes", priority=1,
                                    due_date=(now() - timedelta(days=1)).date().isoformat())
    later = management.create_item(p["id"], "Announce", priority=1, depends_on=[manual["id"]])
    items = management.ranked_items(p["id"])  # before a sync: only the manual ones
    assert [a["title"] for a in items] == ["Write the release notes", "Announce"]
    management.sync(p["id"])
    items = management.ranked_items(p["id"])
    titles = [a["title"] for a in items]
    # done dependencies first, then priority (critical defect 1 = manual 1, due date breaks the tie), due date
    assert titles[0] == "Write the release notes" and titles[1] == "Fix critical defect: pytest"
    assert titles.index("Decide proposal: Add caching") < titles.index("Fix low defect: lint")
    assert titles[-1] == "Announce" and items[-1]["blocked_by"] == [manual["id"]]
    assert items[0]["overdue"]
    from agent_company.db import Notification

    with session() as s:  # overdue notification sent once
        assert s.query(Notification).filter(Notification.source == "action_item").count() == 1
    management.sync(p["id"])
    with session() as s:
        assert s.query(Notification).filter(Notification.source == "action_item").count() == 1
        s.query(Proposal).update({"status": "rejected"})
        s.query(Defect).update({"status": "closed"})
    management.sync(p["id"])
    assert [a["title"] for a in management.ranked_items(p["id"])] == ["Write the release notes", "Announce"]
    management.update_item(manual["id"], {"status": "done"})
    assert management.ranked_items(p["id"])[0]["blocked_by"] == []
    with pytest.raises(ValueError):
        management.update_item(later["id"], {"status": "finished"})


def test_insights_from_output_and_their_lifecycle():
    p = _project()
    gid = _goal(p["id"])
    output = ("Done.\n```json\n{\"files\": []}\n```\n"
              "```json\n{\"insights\": [{\"title\": \"Cache the parser\", \"description\": \"It is rebuilt per call\","
              " \"evidence\": [\"profile: 40% in build()\"], \"confidence\": 1.7},"
              " {\"description\": \"no title\"}]}\n```\n{\"insights\": [{\"title\": \"bare JSON is ignored\"}]}")
    assert management.extract_insights(gid, None, p["manager_id"], output) == 1
    [i] = management.list_insights(p["id"])
    assert i["title"] == "Cache the parser" and i["confidence"] == 1.0 and i["evidence"] == ["profile: 40% in build()"]
    with pytest.raises(ValueError):
        management.advance_insight(i["id"], "implemented", "admin")  # not accepted yet
    management.advance_insight(i["id"], "analyzed", "admin")
    out = management.advance_insight(i["id"], "accepted", "admin", decision="worth it", as_proposal=True)
    assert out["knowledge_id"] and out["proposal_id"] and out["decided_by"] == "admin"
    with session() as s:
        k = s.get(Lesson, out["knowledge_id"])
        assert k.project_id == p["id"] and f"insight:{i['id']}" in k.sources and k.status == "approved"
        assert s.get(Proposal, out["proposal_id"]).status == "pending"
    assert any(a["source"] == f"proposal:{out['proposal_id']}" for a in management.ranked_items(p["id"]))
    with pytest.raises(ValueError):
        management.advance_insight(i["id"], "rejected", "admin")  # accepted is decided
    assert management.advance_insight(i["id"], "implemented", "admin", result="merged in #9")["status"] == "implemented"


def test_secret_project_insight_is_withheld_from_knowledge():
    p = _project()
    projects.update(p["id"], {"classification": "secret"})
    gid = _goal(p["id"])
    with session() as s:
        s.add(Insight(project_id=p["id"], goal_id=gid, agent_id=p["manager_id"], title="Internal hostname"))
    with session() as s:
        iid = s.query(Insight).one().id
    out = management.advance_insight(iid, "accepted", "admin")
    with session() as s:
        assert s.get(Lesson, out["knowledge_id"]).status == "rejected"


def test_goal_time_and_progress_after_a_run():
    p = _project()
    gid = _goal(p["id"])
    loop.run_goal(gid)
    t = management.goal_time(gid)
    assert set(t) == {"active", "waiting", "blocked"} and t["blocked"] == 0
    prog = management.progress(p["id"])
    assert 0 <= prog["progress"] <= 1


def test_plan_api_flow():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        from agent_company import auth
        from agent_company.db import User

        secret = secrets.token_urlsafe(32)
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(secret)
        assert app.post("/api/auth/device", json={"device_secret": secret}).status_code == 200
        pid = app.post("/api/projects", json={"name": "Planned"}).json()["id"]
        m = app.post(f"/api/projects/{pid}/milestones", json={"name": "M1", "target_date": "2020-01-01",
                                                              "criteria": ["Docs reviewed"]}).json()
        assert m["overdue"]
        assert app.post(f"/api/milestones/{m['id']}/criteria/0", json={"met": True}).json()["criteria"][0]["met"]
        assert app.post(f"/api/milestones/{m['id']}/complete").json()["status"] == "completed"
        assert app.post(f"/api/projects/{pid}/milestones", json={"name": "M2", "bogus": 1}).status_code == 400
        r = app.post(f"/api/projects/{pid}/risks", json={"title": "Vendor API may change", "category": "external_dependency",
                                                         "severity": "high", "probability": 0.3}).json()
        assert app.patch(f"/api/risks/{r['id']}", json={"status": "mitigated"}).json()["status"] == "mitigated"
        a = app.post(f"/api/projects/{pid}/action-items", json={"title": "Pin the vendor SDK"}).json()
        assert app.patch(f"/api/action-items/{a['id']}", json={"priority": 9}).json()["priority"] == 5
        plan = app.get(f"/api/projects/{pid}/plan").json()
        assert plan["milestones"][0]["status"] == "completed" and plan["risks"][0]["id"] == r["id"]
        assert plan["action_items"][0]["title"] == "Pin the vendor SDK" and plan["progress"]["progress"] == 1.0
        assert app.get("/api/projects/9999/plan").status_code == 404
        actions = {x["action"] for x in app.get("/api/audit").json()["records"]}
        assert {"milestone_create", "milestone_complete", "risk_create", "action_item_update"} <= actions
