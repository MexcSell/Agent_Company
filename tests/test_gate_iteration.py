"""S04-R-12: gate events carry their iteration as a column; older rows fall back to data and the message."""
import sqlite3

from sqlalchemy import create_engine, inspect, select

from agent_company import db, loop, mission
from agent_company.db import Event, Goal, Task, event_iteration, log_event, session

CHECKS = [{"check": "task build", "passed": False, "detail": "x"}]


def make_goal(status="failed"):
    with session() as s:
        g = Goal(title="Gate", description="", status=status, iteration=3)
        s.add(g)
        s.flush()
        return g.id


def add_event(gid, message, data, iteration=None):
    with session() as s:
        e = Event(kind="gate", goal_id=gid, message=message, data=data, iteration=iteration)
        s.add(e)
        s.flush()
        return e.id


def get(eid):
    with session() as s:
        e = s.get(Event, eid)
        return e, event_iteration(e)


def test_log_event_stores_iteration_column_and_data():
    gid = make_goal()
    log_event("gate", "iteration 2: quality gate passed", goal_id=gid, iteration=2, passed=True, checks=[])
    with session() as s:
        e = s.scalars(select(Event).where(Event.goal_id == gid)).one()
        assert e.iteration == 2 and e.data["iteration"] == 2 and event_iteration(e) == 2


def test_log_event_without_iteration_leaves_column_null():
    log_event("goal", "no iteration here")
    with session() as s:
        e = s.scalars(select(Event).where(Event.message == "no iteration here")).one()
        assert e.iteration is None and "iteration" not in e.data


def test_column_wins_over_data_and_message():
    gid = make_goal()
    eid = add_event(gid, "iteration 9: quality gate failed", {"iteration": 8, "checks": []}, iteration=7)
    assert get(eid)[1] == 7
    eid = add_event(gid, "iteration 9: quality gate failed", {"iteration": 8, "checks": []})
    assert get(eid)[1] == 8


def test_old_row_falls_back_to_message_in_report_and_trace():
    gid = make_goal()
    with session() as s:
        s.add(Task(goal_id=gid, iteration=3, key="build", title="Build", role="developer", status="done",
                   writes=True, gate={"acceptance": [{"type": "command", "passed": True}]}))
    eid = add_event(gid, "iteration 3: quality gate failed", {"passed": False, "checks": CHECKS})
    e, it = get(eid)
    assert e.iteration is None and it == 3
    with session() as s:
        assert mission._gates(s, gid) == {3: CHECKS}
    item = next(i for i in mission.report(gid)["tasks"]["items"] if i["key"] == "build")
    assert item["iteration"] == 3 and item["label"] == "failed"
    assert [g["iteration"] for g in loop.trace(gid)["gates"]] == [3]


def test_no_iteration_anywhere_is_none_and_skipped():
    gid = make_goal()
    eid = add_event(gid, "quality gate failed", {"passed": False, "checks": CHECKS})
    assert get(eid)[1] is None
    with session() as s:
        assert mission._gates(s, gid) == {}
    mission.report(gid)  # no error


def test_trace_has_iteration_for_each_gate():
    gid = make_goal()
    log_event("gate", "iteration 1: quality gate failed", goal_id=gid, iteration=1, passed=False, checks=CHECKS)
    log_event("gate", "iteration 2: quality gate passed", goal_id=gid, iteration=2, passed=True, checks=[])
    gates = loop.trace(gid)["gates"]
    assert [g["iteration"] for g in gates] == [1, 2]


def test_add_missing_columns_adds_iteration(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, goal_id INTEGER, agent_id INTEGER, kind VARCHAR(32), "
                "message TEXT, data JSON, created_at DATETIME)")
    con.execute("INSERT INTO events (kind, message, data) VALUES ('gate', 'iteration 4: quality gate', '{}')")
    con.commit()
    con.close()
    eng = create_engine(f"sqlite:///{path}")
    db._add_missing_columns(eng)
    db._add_missing_columns(eng)  # idempotent: the index exists, checkfirst skips it
    assert "iteration" in {c["name"] for c in inspect(eng).get_columns("events")}
    with eng.connect() as c:
        assert "ix_events_iteration" in {r[1] for r in c.exec_driver_sql("PRAGMA index_list(events)")}
        row = c.exec_driver_sql("SELECT iteration, message, data FROM events").one()
    assert row[0] is None  # no backfill
    old = Event(kind="gate", message=row[1], data={}, iteration=row[0])
    assert event_iteration(old) == 4  # falls back to the message
    old.data = {"iteration": 5}
    assert event_iteration(old) == 5  # and before that to data
    eng.dispose()


def test_fresh_database_has_the_same_index_name():
    with session() as s:
        names = {r[1] for r in s.connection().exec_driver_sql("PRAGMA index_list(events)")}
    assert "ix_events_iteration" in names
