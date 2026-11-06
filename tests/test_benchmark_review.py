"""G-R-14: the scheduled benchmark review runs only with a schedule and a budget, compares with its previous run and
notifies the tasks that no longer pass."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agent_company import evaluation, governance, prefs
from agent_company.db import EvalRun, Notification, session


@pytest.fixture
def fake_suite(monkeypatch):
    """run_suite that records one run per task with the outcomes in `plan` (the next list each call)."""
    plan, calls = [], []

    def run_suite(suite, label, repeats=3, only=None, trial=None, budget_usd=0.0):
        calls.append({"suite": suite, "label": label, "repeats": repeats, "budget_usd": budget_usd})
        with session() as s:
            for task, ok in plan.pop(0).items():
                s.add(EvalRun(label=label, suite=suite, task=task, repeat=1, passed=ok, cost=0.01))
        return evaluation.summary(label)

    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    return plan, calls


def test_nothing_runs_without_a_schedule_and_a_budget(fake_suite):
    plan, calls = fake_suite
    assert evaluation.scheduled_review() is None, "off by default"
    prefs.set_value("benchmark_schedule", "weekly")
    assert evaluation.review_due() is False and evaluation.scheduled_review() is None, "a budget of 0 never runs"
    assert evaluation.scheduled_review(force=True) is None, "not even when forced"
    assert calls == []


def test_a_due_review_runs_once_per_interval_and_names_the_regressions(fake_suite):
    plan, calls = fake_suite
    prefs.set_value("benchmark_schedule", "weekly")
    prefs.set_value("benchmark_budget_usd", 0.5)
    plan += [{"a": True, "b": True}, {"a": True, "b": False}]
    first = evaluation.scheduled_review()
    assert first["previous"] is None and first["summary"]["passed"] == 2
    assert calls[0]["budget_usd"] == 0.5 and calls[0]["repeats"] == 1 and calls[0]["suite"] == "smoke"
    assert evaluation.scheduled_review() is None, "not due again within the week"
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert evaluation.review_due(later) is True
    second = evaluation.scheduled_review(later)
    assert second["previous"] == first["label"] and second["regressions"] == ["b"]
    with session() as s:
        notes = s.scalars(select(Notification).where(Notification.source == "evaluation")
                          .order_by(Notification.id)).all()
        assert [n.level for n in notes] == ["info", "warning"] and all(n.admin_only for n in notes)
        assert "no longer passing: b" in notes[-1].body


def test_an_emergency_stop_skips_the_review(fake_suite, monkeypatch):
    plan, calls = fake_suite
    prefs.set_value("benchmark_schedule", "daily")
    prefs.set_value("benchmark_budget_usd", 1)
    monkeypatch.setattr(governance, "stopped", lambda **kw: "E5 emergency stop by admin: test")
    assert evaluation.scheduled_review() is None and calls == []


def test_the_settings_are_validated():
    with pytest.raises(ValueError):
        prefs.set_value("benchmark_schedule", "hourly")
    with pytest.raises(ValueError):
        prefs.set_value("benchmark_suite", "no-such-suite")
    with pytest.raises(ValueError):
        prefs.set_value("benchmark_suite", "../etc/passwd")
    with pytest.raises(ValueError):
        prefs.set_value("benchmark_budget_usd", 5000)
    prefs.set_value("benchmark_suite", "adversarial")
