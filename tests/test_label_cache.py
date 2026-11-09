"""mission.label_counts: report()'s task labels stored per finished goal (Goal.label_counts), read by the Simple
overview and RCA instead of rebuilding the report on every poll."""
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, select

from agent_company import db, loop, mission, projects, rca, workflow
from agent_company.db import Event, Goal, session

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
    assert stored(gid) == {}  # the notification no longer builds report(): run_goal stores after the outcome message
    loop._store_label_counts(gid)
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


def test_failed_store_still_returns_the_counts(monkeypatch):
    """A first read whose write fails (e.g. SQLite 'database is locked') returns the computed counts, logs the error."""
    gid = finished_goal()

    def locked(gid, counts):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mission, "_write", locked)
    assert mission.label_counts(gid) == {"verified": 1, "partial": 1, "failed": 1}
    assert stored(gid) == {}
    with session() as s:
        assert s.scalars(select(Event.message).where(Event.goal_id == gid, Event.kind == "error",
                                                     Event.message.contains("not stored"))).first()


def test_failed_store_and_failed_log_still_return_the_counts(monkeypatch):
    gid = finished_goal()
    monkeypatch.setattr(mission, "_write", lambda g, c: (_ for _ in ()).throw(RuntimeError("database is locked")))
    monkeypatch.setattr(mission, "log_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("locked too")))
    assert mission.label_counts(gid) == {"verified": 1, "partial": 1, "failed": 1}


def test_overview_survives_a_raising_label_count(app, monkeypatch):
    p = projects.create("Shop")
    gid = goal_row(p["id"], "approved", "merged")

    def locked(gid):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mission, "label_counts", locked)
    r = app.get("/api/simple/overview")
    assert r.status_code == 200
    assert {c["id"]: c["labels"] for c in r.json()["delivered"]}[gid] == {}


def test_store_skipped_for_a_goal_active_at_write_time():
    """A goal resumed between its finish and the store gets no counts."""
    gid = finished_goal()
    with session() as s:
        s.get(Goal, gid).status = "running"
    mission._write(gid, {"verified": 1, "partial": 1, "failed": 1})  # the write itself checks the status
    assert stored(gid) == {}
    mission.store_label_counts(gid)  # report() refuses an active goal; fail-open, nothing stored
    assert stored(gid) == {}


def test_store_skipped_when_the_goal_resumes_during_the_count(monkeypatch):
    gid = finished_goal()
    real = mission.report

    def resumed_meanwhile(g):
        out = real(g)
        with session() as s:
            s.get(Goal, g).status = "recovering"
        return out

    monkeypatch.setattr(mission, "report", resumed_meanwhile)
    assert mission.label_counts(gid) == {"verified": 1, "partial": 1, "failed": 1}
    assert stored(gid) == {}


def test_counts_are_stored_after_the_outcome_message(monkeypatch):
    """run_goal posts 'Goal #N: ...' first; report() for the counts never delays it (the test_projects race)."""
    p = projects.create("Order", quality_commands=["test -f WORKLOG.md"])
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"], project_id=p["id"])
    calls = []
    post, store = projects.post_for_goal, mission.store_label_counts

    def post_spy(goal_id, content, *a, **k):
        if content.startswith(f"Goal #{goal_id}:"):
            calls.append("outcome")
        return post(goal_id, content, *a, **k)

    def store_spy(goal_id):
        calls.append("store")
        return store(goal_id)

    monkeypatch.setattr(projects, "post_for_goal", post_spy)
    monkeypatch.setattr(mission, "store_label_counts", store_spy)
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    assert calls == ["outcome", "store"]
    assert stored(gid) == mission.report(gid)["tasks"]["labels"]
    assert any(m["content"].startswith(f"Goal #{gid}:") for m in projects.messages(p["id"]))


def test_a_goal_that_stays_active_is_not_stored(monkeypatch):
    """A paused goal (here: its token budget is used) waits for a resume: no counts, label_counts refuses it."""
    from agent_company import usage

    calls = []
    monkeypatch.setattr(mission, "store_label_counts", lambda g: calls.append(g))
    gid = loop.create_goal("Tokens", quality_commands=["test -f WORKLOG.md"], budget_tokens=100)
    usage.record(model_name="offline", kind="api", output_tokens=150, goal_id=gid)
    assert loop.run_goal(gid)["status"] == "paused"
    assert calls == [] and stored(gid) == {}
    with pytest.raises(ValueError):
        mission.label_counts(gid)


def test_a_raising_store_does_not_change_the_outcome(monkeypatch):
    def boom(goal_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mission, "store_label_counts", boom)
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval" == loop._goal(gid).status
    assert stored(gid) == {}
    with session() as s:
        assert s.scalars(select(Event.message).where(Event.goal_id == gid, Event.kind == "error",
                                                     Event.message.contains("database is locked"))).first()


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
