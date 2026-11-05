"""SPEC-01 delegated approval rights (S01-R-03) and versioned governance policies (S01-R-05)."""

import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import budgets, config, governance, loop, policies, prefs, projects, quality, recruitment
from agent_company.api import create_app
from agent_company.db import AuditEvent, Delegation, Goal, now, session


@pytest.fixture
def admin():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        yield c


def member(admin, name="thomas"):
    """A second, approved user without any rights beyond the defaults."""
    other = TestClient(admin.app)
    s2 = secrets.token_urlsafe(32)
    other.post("/api/auth/register", json={"name": name, "device_secret": s2})
    uid = next(u["id"] for u in admin.get("/api/users").json() if u["name"] == name)
    admin.post(f"/api/users/{uid}/approve")
    other.post("/api/auth/device", json={"device_secret": s2})
    return other, uid


def waiting_goal(project_id):
    gid = loop.create_goal("Write a hello world", quality_commands=["test -f WORKLOG.md"], project_id=project_id)
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    return gid


def later(days=1):
    return (now() + timedelta(days=days)).isoformat()


# --- S01-R-03 delegation -------------------------------------------------------------------------

def test_a_delegate_approves_only_the_delegated_project_while_the_grant_holds(admin):
    other, uid = member(admin)
    mine, theirs = projects.create("Delegated"), projects.create("Elsewhere")
    g1, g2, g3 = waiting_goal(mine["id"]), waiting_goal(theirs["id"]), waiting_goal(mine["id"])
    assert other.post(f"/api/goals/{g1}/approve").status_code == 403, "no delegation yet"
    assert other.get(f"/api/projects/{mine['id']}").status_code in {403, 404}

    r = admin.post("/api/delegations", json={"user_id": uid, "project_id": mine["id"], "expires_at": later()})
    assert r.status_code == 200 and r.json()["state"] == "active"
    did = r.json()["id"]
    assert other.get("/api/auth/me").json()["delegations"][0]["project_id"] == mine["id"]
    assert other.get(f"/api/projects/{mine['id']}").status_code == 200, "a delegate sees the delegated project"
    assert other.get(f"/api/goals/{g1}").status_code == 200
    assert other.get(f"/api/goals/{g2}").status_code in {403, 404}
    assert other.post(f"/api/goals/{g2}/approve").status_code == 403, "another project"
    assert other.post(f"/api/goals/{g1}/approve").json()["status"] == "approved"
    with session() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.action == "goal_approve")).first()
        assert ev.actor == "user:thomas" and ev.after["delegated"] and ev.severity == "high"

    admin.post(f"/api/delegations/{did}/revoke")
    assert other.post(f"/api/goals/{g3}/approve").status_code == 403, "revocation holds for the next action"
    assert other.post(f"/api/goals/{g3}/reject").status_code == 403
    assert other.get("/api/auth/me").json()["delegations"] == []
    assert other.post("/api/delegations", json={"user_id": uid, "project_id": mine["id"],
                                                "expires_at": later()}).status_code == 403


def test_an_expired_delegation_grants_nothing(admin):
    other, uid = member(admin)
    p = projects.create("Expiring")
    gid = waiting_goal(p["id"])
    did = policies.delegate("user:luca", uid, "approve_goals", p["id"], later())["id"]
    with session() as s:
        s.get(Delegation, did).expires_at = now() - timedelta(seconds=1)
    assert other.post(f"/api/goals/{gid}/approve").status_code == 403
    assert policies.list_delegations(uid)[0]["state"] == "expired"


def test_what_cannot_be_delegated(admin):
    _, uid = member(admin)
    p = projects.create("Rules")
    with pytest.raises(ValueError, match="agents never"):
        policies.delegate("user:luca", 99999, "approve_goals", p["id"], later())  # e.g. an agent's id
    with pytest.raises(ValueError, match="permission"):
        policies.delegate("user:luca", uid, "manage_models", p["id"], later())
    with pytest.raises(ValueError, match="past"):
        policies.delegate("user:luca", uid, "approve_goals", p["id"], later(-1))
    with pytest.raises(ValueError, match="at most"):
        policies.delegate("user:luca", uid, "approve_goals", p["id"], later(policies.MAX_DAYS + 1))
    with pytest.raises(ValueError, match="administrator"):
        policies.delegate("user:luca", 1, "approve_goals", p["id"], later())
    platform = next(x for x in projects.list_projects({"is_admin": True, "id": 1}) if x["protected"])
    with pytest.raises(ValueError, match="platform"):
        policies.delegate("user:luca", uid, "approve_goals", platform["id"], later())
    assert admin.post("/api/delegations", json={"user_id": uid, "project_id": p["id"],
                                                "expires_at": "tomorrow"}).status_code == 400


# --- S01-R-05 policy versions --------------------------------------------------------------------

def test_a_governance_setting_is_versioned_and_restored(admin):
    assert admin.put("/api/settings/goal_budget_usd", json={"value": 5}).status_code == 200
    assert admin.put("/api/settings/require_approval", json={"value": False}).status_code == 200
    admin.put("/api/settings/max_parallel", json={"value": 3})  # not a governance setting: no version
    vs = admin.get("/api/policy/versions").json()
    assert [v["version"] for v in vs] == [3, 2, 1]
    assert vs[0]["author"] == "user:luca" and vs[0]["values"]["require_approval"] is False
    assert vs[2]["note"].startswith("state before") and vs[2]["values"]["goal_budget_usd"] == 0
    assert admin.post("/api/policy/restore", json={"version": 1}).json()["version"] == 4
    assert config.settings().goal_budget_usd == 0 and config.settings().require_approval is True
    admin.post("/api/policy/restore", json={"version": 3})
    assert config.settings().goal_budget_usd == 5 and config.settings().require_approval is False
    assert admin.post("/api/policy/restore", json={"version": 99}).status_code == 404
    assert admin.get("/api/policy/versions?scope=elsewhere").status_code == 400
    prefs.clear("goal_budget_usd", actor="user:luca")
    assert policies.versions()[0]["note"] == "goal_budget_usd back to the .env value"


def test_a_project_lowers_the_goal_budget_but_cannot_raise_it_above_the_company_limit(admin):
    prefs.set_value("goal_budget_usd", 5)
    p = projects.create("Frugal")
    r = admin.put(f"/api/projects/{p['id']}/policy", json={"goal_budget_usd": 2})
    assert r.status_code == 200 and r.json()["effective"]["goal_budget_usd"] == 2
    r = admin.put(f"/api/projects/{p['id']}/policy", json={"goal_budget_usd": 8})
    assert r.status_code == 400 and "company limit" in r.json()["detail"]
    gid = loop.create_goal("Cheap goal", quality_commands=[], project_id=p["id"])
    elsewhere = loop.create_goal("Other goal", quality_commands=[])
    with session() as s:
        assert budgets.limits(s.get(Goal, gid))["cost_usd"] == 2
        assert budgets.limits(s.get(Goal, elsewhere))["cost_usd"] == 5
    prefs.set_value("goal_budget_usd", 1)  # a company limit lowered later still wins
    with session() as s:
        assert budgets.limits(s.get(Goal, gid))["cost_usd"] == 1
    assert [v["version"] for v in policies.versions(f"project:{p['id']}")] == [2, 1]
    policies.restore(f"project:{p['id']}", 1, "user:luca")
    assert policies.project_rules(p["id"])["own"] == {}


def test_without_a_company_limit_any_project_limit_is_stricter():
    p = projects.create("Capped")
    assert policies.set_project_rules(p["id"], {"goal_token_budget": 1000}, "user:luca")["own"] == {"goal_token_budget": 1000}
    with pytest.raises(ValueError, match="may set"):
        policies.set_project_rules(p["id"], {"max_parallel": 1}, "user:luca")


def test_project_approval_rules_are_only_stricter(monkeypatch):
    config.set_overrides({"require_approval": False, "recruit_requires_approval": "permanent_only"})
    p = projects.create("Careful")
    with pytest.raises(ValueError, match="weaker"):
        policies.set_project_rules(p["id"], {"recruit_requires_approval": "never"}, "user:luca")
    rules = policies.set_project_rules(p["id"], {"require_approval": True, "recruit_requires_approval": "always"},
                                       "user:luca")
    assert rules["effective"] == {**rules["effective"], "require_approval": True, "recruit_requires_approval": "always"}
    gid = loop.create_goal("Careful goal", quality_commands=[], project_id=p["id"])
    other = loop.create_goal("Loose goal", quality_commands=[])
    assert quality._require_approval(gid) and not quality._require_approval(other)
    with session() as s:
        assert recruitment.needs_approval("clone", gid, s) and not recruitment.needs_approval("clone", other, s)


def test_company_command_rules_add_to_the_built_in_ones():
    assert governance.check_command("make deploy-prod") is None
    prefs.set_value("blocked_commands", ["deploy-prod"], actor="user:luca")
    assert "company rule" in governance.check_command("make deploy-prod")
    assert governance.check_command("sudo ls"), "built-in rules stay"
    with pytest.raises(ValueError, match="regular expression"):
        prefs.set_value("blocked_commands", ["(unclosed"])
    with pytest.raises(ValueError, match="quality command refused"):
        loop.create_goal("Deploy", quality_commands=["make deploy-prod"])
