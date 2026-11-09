"""Per-user daily token cap of the writing assistant (S04-R-09, spec 0065)."""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect, text

from agent_company import prefs, usage
from agent_company.db import _add_missing_columns


def spend(user_id, tokens=0, ok=True, input_tokens=0):
    usage.record(model_name="m", kind="llm", input_tokens=input_tokens, output_tokens=tokens, ok=ok,
                 error="" if ok else "boom", user_id=user_id, purpose="writing")


def used(user_id):
    return usage.user_tokens_since(user_id, usage.day_start(), "writing")


def test_under_the_cap_the_request_passes():
    prefs.set_value("writing_daily_tokens_per_user", 100)
    spend(1, 40)
    with usage.user_cap(1, estimate=10):
        pass
    assert used(1) == 40


def test_at_the_cap_or_with_an_estimate_reaching_it_the_request_is_refused():
    prefs.set_value("writing_daily_tokens_per_user", 100)
    spend(1, 100)
    with pytest.raises(usage.CapReached) as e:
        with usage.user_cap(1):
            pytest.fail("the guard let a capped user through")
    assert e.value.used == 100 and e.value.cap == 100
    assert e.value.reset_at == (usage.day_start() + timedelta(days=1)).isoformat()
    spend(2, 95)
    with pytest.raises(usage.CapReached):
        with usage.user_cap(2, estimate=5):
            pass
    spend(3, 99)
    with usage.user_cap(3):  # one token under the cap passes
        pass


def test_a_new_utc_day_resets_the_count(monkeypatch):
    prefs.set_value("writing_daily_tokens_per_user", 100)
    spend(1, 100)
    with pytest.raises(usage.CapReached):
        with usage.user_cap(1):
            pass
    real = usage.now()
    monkeypatch.setattr(usage, "now", lambda: real + timedelta(days=1))
    assert used(1) == 0
    with usage.user_cap(1):
        pass


def test_two_concurrent_requests_of_one_user_exactly_one_passes():
    prefs.set_value("writing_daily_tokens_per_user", 100)
    barrier = threading.Barrier(2)
    results = []

    def request():
        barrier.wait()
        try:
            with usage.user_cap(1, estimate=60):  # the request's max tokens; the second sees the first's 60
                spend(1, 60)
            results.append("ok")
        except usage.CapReached:
            results.append("refused")

    threads = [threading.Thread(target=request) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == ["ok", "refused"]
    assert used(1) == 60


def test_a_failed_runs_tokens_count_and_an_exception_releases_the_lock():
    prefs.set_value("writing_daily_tokens_per_user", 100)
    spend(1, 20, ok=False, input_tokens=30)
    assert used(1) == 50
    spend(1, 0, ok=False)
    assert used(1) == 50
    with pytest.raises(RuntimeError):
        with usage.user_cap(1):
            raise RuntimeError("model call failed")
    done = threading.Event()

    def again():
        with usage.user_cap(1):
            done.set()

    t = threading.Thread(target=again)
    t.start()
    t.join(5)
    assert done.is_set(), "the user's lock was not released after the exception"


def test_setting_zero_means_no_cap():
    prefs.set_value("writing_daily_tokens_per_user", 0)
    spend(1, 10**9)
    with usage.user_cap(1, estimate=10**6):
        pass


def test_users_are_isolated_and_agent_runs_never_count():
    prefs.set_value("writing_daily_tokens_per_user", 100)
    spend(1, 100)
    usage.record(model_name="m", kind="llm", output_tokens=500, purpose="writing")  # agent work, no user
    assert used(2) == 0
    with usage.user_cap(2):
        pass
    with pytest.raises(usage.CapReached):
        with usage.user_cap(1):
            pass


def test_a_runs_table_without_user_id_is_migrated(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text('CREATE TABLE runs (id INTEGER PRIMARY KEY, model_name VARCHAR(128), kind VARCHAR(16))'))
        conn.execute(text("INSERT INTO runs (model_name, kind) VALUES ('m', 'llm')"))
    _add_missing_columns(eng)
    insp = inspect(eng)
    assert "user_id" in {c["name"] for c in insp.get_columns("runs")}
    assert "ix_runs_user_id" in {i["name"] for i in insp.get_indexes("runs")}
    with eng.connect() as conn:
        assert conn.execute(text("SELECT user_id FROM runs")).scalar() is None
