import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agent_company import projects, schedules
from agent_company.api import create_app
from agent_company.db import Goal, Schedule, session


def test_intervals_parse_and_the_next_run_follows_them():
    t = datetime(2026, 11, 6, 10, 30, tzinfo=UTC)  # a Friday
    assert schedules.next_run("hourly", t) == datetime(2026, 11, 6, 11, 0, tzinfo=UTC)
    assert schedules.next_run("daily 06:00", t) == datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
    assert schedules.next_run("daily 11:15", t) == datetime(2026, 11, 6, 11, 15, tzinfo=UTC)
    assert schedules.next_run("weekly mon 09:00", t) == datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    assert schedules.next_run("weekly fri 10:30", t) == datetime(2026, 11, 13, 10, 30, tzinfo=UTC)
    assert schedules.normalise("  Daily   06:00") == "daily 06:00"
    for bad in ("every 5 seconds", "daily 25:00", "weekly someday 09:00", "", "* * * * *"):
        with pytest.raises(ValueError):
            schedules.parse(bad)


def _due(sid):
    with session() as s:
        s.get(Schedule, sid).next_run_at = datetime.now(UTC) - timedelta(minutes=1)


def test_a_due_schedule_creates_one_goal_and_waits_while_it_is_open(tmp_path):
    (tmp_path / "repo").mkdir()
    p = projects.create("Sched", path=str(tmp_path / "repo"))
    sc = schedules.create(p["id"], "Update the dependencies", "daily 06:00", "and fix what breaks", ["true"], "bugfix")
    assert schedules.tick() == [], "not due yet"
    _due(sc["id"])
    made = schedules.tick()
    assert len(made) == 1
    with session() as s:
        g = s.get(Goal, made[0])
        assert (g.origin, g.project_id, g.title, g.quality_commands) == ("schedule", p["id"], "Update the dependencies", ["true"])
        row = s.get(Schedule, sc["id"])
        assert row.last_goal_id == made[0] and schedules._aware(row.next_run_at) > datetime.now(UTC)
    _due(sc["id"])
    assert schedules.tick() == [] and "still queued" in schedules.list_schedules(p["id"])[0]["note"]
    # Paused, or the project paused: nothing; resumed after its goal ended: a new goal.
    with session() as s:
        s.get(Goal, made[0]).status = "approved"
    schedules.update(sc["id"], {"enabled": False})
    _due(sc["id"])
    assert schedules.tick() == []
    schedules.update(sc["id"], {"enabled": True})
    projects.set_status(p["id"], "paused", "test")
    _due(sc["id"])
    assert schedules.tick() == [] and "paused" in schedules.list_schedules(p["id"])[0]["note"]
    projects.set_status(p["id"], "active", "test")
    _due(sc["id"])
    assert len(schedules.tick()) == 1


def test_only_administrators_change_schedules_and_it_is_audited():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        pid = c.post("/api/projects", json={"name": "Web"}).json()["id"]
        r = c.post(f"/api/projects/{pid}/schedules", json={"title": "Nightly audit", "interval": "daily 02:00"})
        assert r.status_code == 200 and r.json()["interval"] == "daily 02:00"
        assert c.post(f"/api/projects/{pid}/schedules", json={"title": "x", "interval": "every minute"}).status_code == 400
        assert c.post(f"/api/projects/{pid}/schedules", json={"title": "x", "interval": "hourly", "checks": ["sudo reboot"]}).status_code == 400
        sid = r.json()["id"]
        assert c.patch(f"/api/schedules/{sid}", json={"enabled": False}).json()["enabled"] is False
        assert [x["title"] for x in c.get(f"/api/projects/{pid}/schedules").json()] == ["Nightly audit"]
        assert c.delete(f"/api/schedules/{sid}").status_code == 200
        acts = {a["action"] for a in c.get("/api/audit").json()["records"]}
        assert {"schedule_create", "schedule_update", "schedule_delete"} <= acts
