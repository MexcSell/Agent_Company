"""mission.label_counts: report()'s task labels stored per finished goal (Goal.label_counts), read by the Simple
overview and RCA instead of rebuilding the report on every poll."""
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from agent_company import db, loop, mission, projects, rca, workflow
from agent_company.db import Goal, session

from test_mission_report import OK, gate, make_goal, task
from test_simple_mode import app, goal_row  # noqa: F401 - the app fixture


def finished_goal():
    """One verified, one partial and one failed task on an approved goal."""
    gid = make_goal("approved")
    task(gid, "build", acceptance=OK)
    task(gid, "docs")
    task(gid, "api", status="failed")
    gate(gid, 1, [{"check": "task build", "passed": True}])
    return gid


def stored(gid):
    with session() as s:
        return dict(s.get(Goal, gid).label_counts)


def counting(monkeypatch):
    calls = []
    real = mission.report

    def spy(gid):
        calls.append(gid)
        return real(gid)

    monkeypatch.setattr(mission, "report", spy)
    return calls


def test_finished_goal_stores_report_labels():  # (a) and (b)
    gid = finished_goal()
    assert stored(gid) == {}
    loop._notify_goal(gid, "approved")
    want = mission.report(gid)["tasks"]["labels"]
    assert want == {"verified": 1, "partial": 1, "failed": 1}
    assert stored(gid) == want  # (b) read back through a new session
    with session() as s:
        assert s.get(Goal, gid).label_counts == want


def test_old_goal_filled_on_first_read_then_served_from_the_row(monkeypatch):  # (c)
    gid = finished_goal()
    calls = counting(monkeypatch)
    assert mission.label_counts(gid) == {"verified": 1, "partial": 1, "failed": 1}
    assert calls == [gid] and stored(gid) == {"verified": 1, "partial": 1, "failed": 1}

    def boom(gid):
        raise AssertionError("report() must not run for stored counts")

    monkeypatch.setattr(mission, "report", boom)
    assert mission.label_counts(gid) == {"verified": 1, "partial": 1, "failed": 1}


def test_goal_without_tasks_is_cached_too(monkeypatch):
    gid = make_goal("failed")
    assert mission.label_counts(gid) == {"verified": 0, "partial": 0, "failed": 0}
    assert stored(gid) == {"verified": 0, "partial": 0, "failed": 0}
    monkeypatch.setattr(mission, "report", lambda g: (_ for _ in ()).throw(AssertionError("recomputed")))
    assert mission.label_counts(gid) == {"verified": 0, "partial": 0, "failed": 0}


def test_store_label_counts_is_fail_open(monkeypatch):
    gid = finished_goal()

    def boom(gid):
        raise RuntimeError("broken report")

    monkeypatch.setattr(mission, "report", boom)
    assert mission.store_label_counts(gid) is None  # never raises
    assert stored(gid) == {}


def test_simple_overview_reads_stored_counts(app, monkeypatch):  # (d)
    p = projects.create("Shop")
    gid = goal_row(p["id"], "approved", "merged")
    want = mission.report(gid)["tasks"]["labels"]
    mission.store_label_counts(gid)
    calls = counting(monkeypatch)
    for _ in range(2):
        d = app.get("/api/simple/overview").json()
        assert {c["id"]: c["labels"] for c in d["delivered"]}[gid] == want
    assert calls == []


def test_reopened_goal_clears_the_cache(monkeypatch):  # (e)
    gid = make_goal("failed")
    task(gid, "build", acceptance=OK)
    mission.store_label_counts(gid)
    assert stored(gid)
    assert workflow.transition(gid, "recovering", "resumed")  # failed → recovering: back in ACTIVE
    assert stored(gid) == {}
    with pytest.raises(ValueError):
        mission.label_counts(gid)


def test_final_move_clears_the_cache_and_refills_on_read():
    """awaiting_approval → approved runs workflow._tasks_follow, which may change task states: counted again."""
    gid = make_goal("awaiting_approval")
    task(gid, "build", acceptance=OK)
    mission.store_label_counts(gid)
    assert stored(gid)
    assert workflow.transition(gid, "approved", "approved by a person")
    assert stored(gid) == {}
    assert mission.label_counts(gid) == mission.report(gid)["tasks"]["labels"] == stored(gid)


def test_unknown_goal_raises_key_error():  # (f)
    with pytest.raises(KeyError):
        mission.label_counts(9999)


def test_add_missing_columns_adds_label_counts(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE goals (id INTEGER PRIMARY KEY, title VARCHAR(256), description TEXT, status VARCHAR(24))")
    con.execute("INSERT INTO goals (title, description, status) VALUES ('old', 'old', 'approved')")
    con.commit()
    con.close()
    eng = create_engine(f"sqlite:///{path}")
    db._add_missing_columns(eng)
    db._add_missing_columns(eng)
    assert "label_counts" in {c["name"] for c in inspect(eng).get_columns("goals")}
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT label_counts FROM goals").scalar() == "{}"  # the default: not cached


def test_rca_evidence_names_the_labels():  # (g)
    gid = finished_goal()
    text = rca.evidence([gid])["text"]
    assert "task labels: verified 1, partial 1, failed 1" in text
    running = make_goal("running")
    assert "task labels" not in rca.evidence([running])["text"]  # an active goal: no counts, still a pack
