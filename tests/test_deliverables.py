"""AICO UI-DEL-01: a merged goal leaves one release record, stored before completion is reported."""

import hashlib
import secrets
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import api, deliverables, loop, prefs, projects
from agent_company.auth import DEFAULT_PERMISSIONS
from agent_company.db import Deliverable, Message, Notification, session

ADMIN = {"id": 1, "name": "admin", "is_admin": True, "permissions": dict(DEFAULT_PERMISSIONS)}


@pytest.fixture(autouse=True)
def users():
    from agent_company import auth

    auth.register("admin", secrets.token_urlsafe(32))


def wait(gid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        g = loop._goal(gid)
        if g.status not in {"queued", "planning", "running", "recovering"}:
            return g
        time.sleep(0.05)
    raise AssertionError("goal did not finish")


def _merged_goal(name="Rel"):
    p = projects.create(name, quality_commands=["test -f WORKLOG.md"])
    g = wait(projects.command(p["id"], ADMIN, "Write the worklog")["goal_id"])
    assert g.status == "awaiting_approval", g.result
    return p, g


def test_approve_stores_one_release_record_with_its_report():
    p, g = _merged_goal()
    out = loop.approve(g.id, actor="user:admin")
    rows = deliverables.list_deliverables(project_id=p["id"])
    assert len(rows) == 1
    d = deliverables.get(rows[0]["id"])
    assert d["goal_id"] == g.id and d["merge_commit"] == out["merge_commit"] and d["status"] == "ready"
    assert d["approved_by"] == "user:admin" and d["checksum"] == hashlib.sha256(d["report_md"].encode()).hexdigest()
    for section in ("## Overview", "## Requirements and fulfilment", "## Architecture", "## Team", "## Deployment notes",
                    "## Artifacts", "# Mission report"):
        assert section in d["report_md"], section
    assert set(d["acceptance"]) == {"verified", "partial", "failed"}
    with pytest.raises(ValueError):
        loop.approve(g.id)  # a second approve is refused, so no second record
    assert deliverables.record(g.id, out["merge_commit"], "again")["id"] == d["id"], "record is idempotent"
    with session() as s:
        assert len(s.scalars(select(Deliverable).where(Deliverable.goal_id == g.id)).all()) == 1
        assert any("approved and merged" in m.content for m in s.scalars(select(Message).where(Message.goal_id == g.id)))


def test_a_newer_release_supersedes_the_older_one_and_the_api_serves_them():
    p, g1 = _merged_goal("Two")
    loop.approve(g1.id)
    g2 = wait(projects.command(p["id"], ADMIN, "Write the worklog again")["goal_id"])
    loop.approve(g2.id)
    rows = deliverables.list_deliverables(project_id=p["id"])
    assert [r["status"] for r in rows] == ["ready", "superseded"]
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: ADMIN
    c = TestClient(app)
    assert [r["goal_id"] for r in c.get(f"/api/deliverables?project_id={p['id']}").json()] == [g2.id, g1.id]
    md = c.get(f"/api/deliverables/{rows[0]['id']}/download")
    assert md.status_code == 200 and md.text.startswith("# Completion report")
    member = {"id": 99, "name": "m", "is_admin": False, "permissions": {**DEFAULT_PERMISSIONS, "projects": []}}
    app.dependency_overrides[api.current_user] = lambda: member
    assert c.get("/api/deliverables").json() == []
    assert c.get(f"/api/deliverables/{rows[0]['id']}").status_code == 404


def test_completion_is_not_reported_when_the_record_cannot_be_stored(monkeypatch):
    p, g = _merged_goal("Fail")

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(deliverables, "record", broken)
    loop.approve(g.id)
    assert loop._goal(g.id).status == "approved", "the merge stands"
    with session() as s:
        assert not any("approved and merged" in m.content for m in s.scalars(select(Message).where(Message.goal_id == g.id)))
        titles = [n.title for n in s.scalars(select(Notification).where(Notification.goal_id == g.id))]
    assert any("release record was not stored" in t for t in titles)


def test_a_goal_merged_without_approval_records_its_release_before_the_notification():
    prefs.set_value("require_approval", False)
    p = projects.create("Auto", quality_commands=["test -f WORKLOG.md"])
    g = wait(projects.command(p["id"], ADMIN, "Write the worklog")["goal_id"])
    assert g.status == "approved", g.result
    end = time.time() + 10  # the status is written first, the record right after it, the notification last
    while time.time() < end and not (rows := deliverables.list_deliverables(project_id=p["id"])):
        time.sleep(0.05)
    assert len(rows) == 1 and rows[0]["approved_by"] == "auto (no approval required)" and rows[0]["merge_commit"]
    while time.time() < end:
        with session() as s:
            titles = [x.title for x in s.scalars(select(Notification).where(Notification.goal_id == g.id))]
        if any("was merged" in x for x in titles):
            break
        time.sleep(0.05)
    assert any("was merged" in x for x in titles)
