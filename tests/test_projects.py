import pathlib
import secrets
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import gitsync, loop, prefs, projects, worktrees
from agent_company.api import create_app
from agent_company.db import Agent, Message, Proposal, Task, session

from agent_company.auth import DEFAULT_PERMISSIONS  # noqa: E402

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}
MEMBER = {"id": 2, "name": "member", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": "all"}}


@pytest.fixture(autouse=True)
def users():
    from agent_company import auth

    auth.register("admin", secrets.token_urlsafe(32))
    auth.register("member", secrets.token_urlsafe(32))
    auth.admin_action(1, 2, "approve")


def wait(gid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        g = loop._goal(gid)
        if g.status not in {"queued", "planning", "running", "recovering"}:
            return g
        time.sleep(0.05)
    raise AssertionError("goal did not finish")


def wait_outcome(gid, timeout=20):
    """wait() returns on the final status; loop._chat_outcome posts 'Goal #N: ...' after it. Wait for that post."""
    g = wait(gid, timeout)
    end = time.time() + timeout
    while time.time() < end:
        with session() as s:
            if s.scalars(select(Message.id).where(Message.goal_id == gid,
                                                  Message.content.startswith(f"Goal #{gid}:"))).first():
                return g
        time.sleep(0.05)
    raise AssertionError(f"no outcome message for goal #{gid}")


def test_platform_project_is_protected_and_admin_only():
    names = [p["name"] for p in projects.list_projects(ADMIN)]
    assert projects.PLATFORM in names
    assert projects.PLATFORM not in [p["name"] for p in projects.list_projects(MEMBER)]
    pid = next(p["id"] for p in projects.list_projects(ADMIN) if p["protected"])
    with pytest.raises(ValueError):
        projects.delete([pid])
    with pytest.raises(ValueError):
        projects.update(pid, {"name": "x"})
    with pytest.raises(KeyError):
        projects.get(pid, MEMBER)


def test_create_edit_and_multi_delete(tmp_path):
    a = projects.create("Alpha")  # no path -> fresh workspace repo
    assert (tmp_path / "data" / "workspaces" / f"project-{a['id']}" / ".git").exists()
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / ".env").write_text("SECRET=1")
    (plain / "app.py").write_text("print(1)")
    b = projects.create("Beta", path=str(plain), github_url="https://github.com/x/y.git")
    assert (plain / ".git").exists(), "a plain folder becomes a repository"
    tracked = worktrees.git(plain, "ls-files").split()
    assert tracked == [".gitignore"], "nothing the folder already held is committed"
    with pytest.raises(ValueError):
        projects.create("alpha")
    with pytest.raises(ValueError):
        projects.create("Bad", github_url="ftp://x")
    with pytest.raises(ValueError):
        projects.create("Cmd", quality_commands=["sudo reboot"])
    assert projects.update(a["id"], {"name": "Alpha 2"})["name"] == "Alpha 2"
    with pytest.raises(ValueError):
        projects.update(a["id"], {"path": "/etc"})
    assert sorted(projects.delete([a["id"], b["id"]])) == sorted([a["id"], b["id"]])


def test_a_repository_set_up_with_github_brings_its_url(tmp_path):
    repo = tmp_path / "withgh"
    repo.mkdir()
    worktrees.git(repo, "init", "-q", "-b", "main")
    worktrees.git(repo, "remote", "add", "origin", "https://user:ghp_secret@github.com/acme/app.git")
    p = projects.create("WithGh", path=str(repo))
    assert p["github_url"] == "https://github.com/acme/app.git", "the remote's URL, without the token in it"
    # A URL typed for a repository without a remote becomes its origin.
    bare = tmp_path / "nogh"
    bare.mkdir()
    q = projects.create("NoGh", path=str(bare), github_url="git@github.com:acme/other.git")
    assert q["github_url"] == "git@github.com:acme/other.git"
    assert worktrees.git(bare, "remote", "get-url", "origin") == "git@github.com:acme/other.git"
    # Set up with GitHub after it was added: the URL shows from the next start.
    (tmp_path / "later").mkdir()
    later = projects.create("Later", path=str(tmp_path / "later"))
    assert later["github_url"] == ""
    worktrees.git(pathlib.Path(later["path"]), "remote", "add", "origin", "git@github.com:acme/later.git")
    projects.seed()
    assert projects.project_dict(projects.get(later["id"], ADMIN))["github_url"] == "git@github.com:acme/later.git"


def test_command_window_order_runs_a_goal_and_reports_back():
    p = projects.create("Gamma", quality_commands=["test -f WORKLOG.md"])
    out = projects.command(p["id"], ADMIN, "Build a hello world script")
    g = wait_outcome(out["goal_id"])
    assert g.status == "awaiting_approval" and g.project_id == p["id"] and g.repo_path == p["path"]
    kinds = [m["kind"] for m in projects.messages(p["id"])]
    assert kinds[:3] == ["chat", "command", "chat"] and "plan" in kinds and "result" in kinds
    # the GM's instruction to each agent shows between the plan and that agent's report
    msgs = projects.messages(p["id"])
    sent = [m for m in msgs if m["kind"] == "assignment"]
    with session() as s:
        tasks = s.scalars(select(Task).where(Task.goal_id == g.id, Task.agent_id.is_not(None))).all()
        assert tasks and sorted(m["meta"]["task_id"] for m in sent) == sorted(t.id for t in tasks)
        for t in tasks:
            m = next(m for m in sent if m["meta"]["task_id"] == t.id)
            assert s.get(Agent, m["agent_id"]).role == "general_manager", "the GM gives the instruction"
            assert m["meta"]["to_agent_id"] == t.agent_id and m["content"].startswith(f"To {m['meta']['to']} (")
            assert t.title in m["content"]
            report = next(r for r in msgs if r["kind"] in ("result", "error") and r["meta"].get("task_id") == t.id)
            assert kinds.index("plan") < msgs.index(m) < msgs.index(report)
    loop.approve(g.id)
    assert "approved and merged" in projects.messages(p["id"])[-1]["content"]


def test_slash_commands():
    p = projects.create("Delta")
    projects.command(p["id"], ADMIN, "/help")
    assert "/standup" in projects.messages(p["id"])[-1]["content"]
    projects.command(p["id"], ADMIN, "/status")
    assert projects.messages(p["id"])[-1]["content"] == "No goals yet."
    projects.command(p["id"], ADMIN, "/standup")
    assert projects.messages(p["id"])[-1]["content"].startswith("Stand-up")
    projects.command(p["id"], ADMIN, "/nope")
    assert projects.messages(p["id"])[-1]["kind"] == "error"
    projects.command(p["id"], MEMBER, "/sync")
    assert "Only administrators" in projects.messages(p["id"])[-1]["content"]
    gid = loop.create_goal("Long one", project_id=p["id"])
    projects.command(p["id"], ADMIN, "/stop")
    assert loop._goal(gid).status == "cancelled"
    with pytest.raises(ValueError):
        projects.command(p["id"], ADMIN, "   ")


def test_proposals_wait_for_user_then_spawn_goal():
    p = projects.create("Eps")
    gid = projects.command(p["id"], ADMIN, "Build it and propose follow-ups")["goal_id"]
    wait(gid)
    props = projects.list_proposals(ADMIN, "pending")
    assert len(props) == 1 and props[0]["title"].startswith("Add regression tests")
    assert projects.list_proposals(MEMBER) == props  # visible to members of a normal project
    out = projects.decide(props[0]["id"], True, "admin")
    child = wait(out["spawned_goal_id"])
    assert child.origin == "proposal" and child.parent_goal_id == gid and child.project_id == p["id"]
    with pytest.raises(ValueError):
        projects.decide(props[0]["id"], True, "admin")


def test_superiors_decide_and_loop_guard_limits_spawns():
    prefs.set_value("proposal_policy", "superiors")
    prefs.set_value("max_proposal_goals", 1)
    gid = loop.create_goal("Build and propose more")
    loop.run_goal(gid)
    with session() as s:
        decided = s.scalars(select(Proposal).where(Proposal.goal_id == gid)).one()
        assert decided.status == "approved" and "(GM)" in decided.decided_by
        child_id = decided.spawned_goal_id
    wait(child_id)
    # a second proposal from the same family would exceed the limit of 1
    with session() as s:
        s.add(Proposal(goal_id=child_id, title="One more", description=""))
    with session() as s:
        pid = s.scalars(select(Proposal).where(Proposal.title == "One more")).one().id
    out = projects.decide(pid, True, "GM")
    assert out["status"] == "rejected" and "limit" in out["decided_by"]


def test_uploads_are_typed_and_copied_into_the_worktree():
    with pytest.raises(ValueError):
        projects.save_upload("x.exe", b"MZ", "application/x-msdownload", 1)
    up = projects.save_upload("shot.png", b"\x89PNG....", "image/png", 1)
    p = projects.create("Zeta")
    gid = projects.command(p["id"], ADMIN, "Fix what the screenshot shows", [up["id"]])["goal_id"]
    g = wait(gid)
    att = f"{g.worktree}/.attachments/shot.png"
    assert open(att, "rb").read().startswith(b"\x89PNG")
    assert ".attachments" not in worktrees.git(g.worktree, "ls-files")
    assert projects.messages(p["id"])[1]["attachments"][0]["name"] == "shot.png"


def test_push_to_remote_with_gitignore_and_user_branch(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    p = projects.create("Pushy", github_url="https://example.invalid/placeholder.git")
    projects.update(p["id"], {"github_url": f"git@local:{remote}"})  # validation passes; then point at the bare repo
    with session() as s:
        s.get(projects.Project, p["id"]).github_url = str(remote)
    out = gitsync.push(p["id"])
    assert out["status"] == "pushed", out
    assert "source-only" in subprocess.run(["git", "show", "main:.gitignore"], cwd=remote, capture_output=True,
                                           text=True).stdout
    assert gitsync.push(p["id"])["status"] == "noop"
    big = gitsync.MAX_FILE_BYTES
    gitsync.MAX_FILE_BYTES = 3  # anything larger than 3 bytes counts as "large" for this check
    try:
        (pathlib.Path(p["path"]) / "big.bin").write_bytes(b"0123456789")
        worktrees.git(p["path"], "add", "big.bin")
        worktrees.git(p["path"], "commit", "-q", "-m", "big")
        refused = gitsync.push(p["id"])
        assert refused["status"] == "error" and "big.bin" in refused["message"]
    finally:
        gitsync.MAX_FILE_BYTES = big
    with pytest.raises(ValueError):
        gitsync.set_identity(1, "x", "not-an-email", "")
    with pytest.raises(ValueError):
        gitsync.set_identity(1, "x", "", "bad..branch")


def test_project_api_flow():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        from agent_company import auth
        from agent_company.db import User

        secret = secrets.token_urlsafe(32)  # bind the fixture's admin to this client's device and sign in
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(secret)
        assert app.post("/api/auth/device", json={"device_secret": secret}).status_code == 200
        pid = app.post("/api/projects", json={"name": "Web", "quality_commands": ["test -f WORKLOG.md"]}).json()["id"]
        up = app.post("/api/uploads", files={"file": ("note.txt", b"hello", "text/plain")}).json()
        assert app.get(f"/api/uploads/{up['id']}").content == b"hello"
        r = app.post(f"/api/projects/{pid}/command", json={"content": "Write a note and propose next steps",
                                                            "attachments": [up["id"]]}).json()
        wait_outcome(r["goal_id"])
        d = app.get(f"/api/projects/{pid}").json()
        assert d["goals"][0]["project_id"] == pid and d["members"] and d["proposals"]
        prop = d["proposals"][0]["id"]
        assert app.post(f"/api/proposals/{prop}/reject").json()["status"] == "rejected"
        msgs = app.get(f"/api/projects/{pid}/messages").json()
        assert any("rejected" in m["content"] for m in msgs)
        after = msgs[-1]["id"]
        assert app.get(f"/api/projects/{pid}/messages?after={after}").json() == []
        assert app.post(f"/api/projects/{pid}/members/remove", json={}).json()["removed"]
        assert app.get(f"/api/projects/{pid}").json()["members"] == []
        assert app.post("/api/projects/delete", json={"ids": [pid]}).json()["deleted"] == [pid]
        actions = {r["action"] for r in app.get("/api/audit").json()["records"]}
        assert {"project_create", "proposal_reject", "project_members_remove", "project_delete"} <= actions


def test_permissions_limit_what_a_member_can_see_and_do():
    from agent_company import auth

    a = projects.create("Open one")
    b = projects.create("Restricted one")
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        from agent_company.db import User

        admin_secret, member_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with session() as s:
            s.get(User, 1).device_hash = auth._hash(admin_secret)
            s.get(User, 2).device_hash = auth._hash(member_secret)
        member = TestClient(app.app)
        assert member.post("/api/auth/device", json={"device_secret": member_secret}).status_code == 200
        assert app.post("/api/auth/device", json={"device_secret": admin_secret}).status_code == 200
        assert member.get("/api/projects").json() == [], "by default a member sees only their own projects"
        assert app.put("/api/users/2/permissions", json={"projects": "all"}).status_code == 200
        assert {p["name"] for p in member.get("/api/projects").json()} == {"Open one", "Restricted one"}
        assert app.put("/api/users/2/permissions", json={"projects": [a["id"]], "run": False}).status_code == 200
        assert [p["name"] for p in member.get("/api/projects").json()] == ["Open one"]
        assert member.get(f"/api/projects/{b['id']}").status_code == 404
        assert member.post(f"/api/projects/{a['id']}/command", json={"content": "do it"}).status_code == 403
        assert member.post("/api/goals", json={"title": "anything"}).status_code == 403
        assert member.post("/api/agents", json={"role": "x_role"}).status_code == 403
        app.put("/api/users/2/permissions", json={"run": True, "manage_agents": True, "create_projects": True})
        assert member.post(f"/api/projects/{a['id']}/command", json={"content": "/status"}).status_code == 200
        assert member.post("/api/agents", json={"role": "x_role"}).status_code == 200
        assert member.post("/api/models", json={"name": "m", "kind": "llm", "model_id": "x/y"}).status_code == 403
        made = member.post("/api/projects", json={"name": "Mine"})
        assert made.status_code == 200
        assert "Mine" in [p["name"] for p in member.get("/api/projects").json()], "creator is granted the project"
        assert member.post("/api/projects", json={"name": "Path", "path": "/tmp"}).status_code == 403
        assert app.put("/api/users/2/permissions", json={"root": True}).status_code == 400


def test_goal_commits_use_the_ordering_users_git_identity():
    gitsync.set_identity(1, "Mex Cell", "sellmexc9@example.com", "")
    p = projects.create("Ident", quality_commands=["test -f WORKLOG.md"])
    g = wait(projects.command(p["id"], ADMIN, "Write the worklog")["goal_id"])
    assert worktrees.git(g.worktree, "log", "-1", "--format=%an <%ae>") == "Mex Cell <sellmexc9@example.com>"
    loop.approve(g.id)
    assert worktrees.git(p["path"], "log", "-1", "--format=%an") == "Mex Cell"


def test_removed_agents_leave_the_project_panel_until_they_work_on_it_again():
    from datetime import timedelta

    from agent_company.db import Agent, Goal, Task, now

    p = projects.create("Panel")
    with session() as s:
        a1, a2 = (a.id for a in s.scalars(select(Agent).where(Agent.role != "general_manager").limit(2)))
        g = Goal(title="work", description="", repo_path="", project_id=p["id"])
        s.add(g)
        s.flush()
        old = now() - timedelta(minutes=5)
        s.add_all([Task(goal_id=g.id, iteration=1, key=f"t{i}", title="t", role="developer", agent_id=a,
                        status="done", created_at=old) for i, a in enumerate((a1, a2))])
        gid = g.id
    shown = {a["id"] for a in projects.members(p["id"])}
    assert {a1, a2} <= shown
    assert projects.remove_members(p["id"], [a1]) == [a1]
    assert a1 not in {a["id"] for a in projects.members(p["id"])}
    assert sorted(projects.remove_members(p["id"])) == sorted(shown - {a1}), "all: every agent still shown"
    assert projects.members(p["id"]) == []
    assert {a["id"] for a in projects.members(p["id"], [a1, a2])} == {a1, a2}, "a topic's own agents stay"
    with session() as s:
        assert s.get(Agent, a1) is not None, "the agent stays in the company"
        s.add(Task(goal_id=gid, iteration=2, key="t9", title="again", role="developer", agent_id=a2, status="done"))
    back = {a["id"] for a in projects.members(p["id"])}
    assert a2 in back and a1 not in back, "an agent comes back when it works on the project again"
