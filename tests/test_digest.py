"""Mission digest after a goal (guides G-R-09)."""
import secrets
import time

from agent_company import auth, loop, projects
from agent_company.auth import DEFAULT_PERMISSIONS

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


def wait(gid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        g = loop._goal(gid)
        if g.status not in {"queued", "planning", "running", "recovering"}:
            return g
        time.sleep(0.05)
    raise AssertionError("goal did not finish")


def test_goal_outcome_posts_a_digest_with_tasks_tokens_cost_and_merge_state():
    auth.register("admin", secrets.token_urlsafe(32))
    p = projects.create("Digest", quality_commands=["test -f WORKLOG.md"])
    out = projects.command(p["id"], ADMIN, "Build a hello world script")
    g = wait(out["goal_id"])
    assert g.status == "awaiting_approval"
    d = loop.digest(g.id)
    assert d["tasks"] >= 1 and d["tasks_done"] == d["tasks"] and d["merge"] == "waiting for approval to merge"
    assert d["tokens"] > 0 and d["cost"] >= 0 and d["duration"] >= d["active"] >= 0
    result = [m for m in projects.messages(p["id"]) if m["kind"] == "result"][-1]["content"]
    assert result.splitlines()[1].startswith("Digest: ")
    assert f"{d['tasks_done']}/{d['tasks']} task(s) passed" in result and "tokens" in result and "approval" in result
    loop.approve(g.id)
    assert loop.digest(g.id)["merge"] == "merged"
