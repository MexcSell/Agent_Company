"""The offline simulator can be switched off on the Models page: it then never stands in, work with no real model
usable fails with the reason, a usage limit still pauses the goal, and Test still runs it (it sends nothing)."""

import pytest
from sqlalchemy import select

from agent_company import executors, registry, router, usage
from agent_company.db import Agent, ModelEntry, session

LIMIT = "You've hit your session limit · resets 9:50pm (Europe/Berlin)"


def _sim() -> int:
    with session() as s:
        return s.scalars(select(ModelEntry.id).where(ModelEntry.kind == "offline")).first()


def _gm() -> int:
    with session() as s:
        return s.scalars(select(Agent.id).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first()


def _only_opus(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    router.set_route("general_manager", "Claude Opus 5.5")
    registry.update_agent(_gm(), {"model_pref": None})


def test_switched_off_the_simulator_never_stands_in(monkeypatch):
    assert router.simulator_enabled(), "on by default"
    _only_opus(monkeypatch)
    router.update_model(_sim(), {"enabled": False})
    assert not router.simulator_enabled()
    assert [c.kind for c in router.resolve_chain("general_manager")] == ["llm"], "no simulator at the end"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(router.NoModel, match="offline simulator is switched off"):
        router.resolve_chain("general_manager")
    router.update_model(_sim(), {"enabled": True})
    assert [c.kind for c in router.resolve_chain("general_manager")] == ["offline"]


def test_a_usage_limit_still_pauses_with_the_simulator_off(monkeypatch):
    _only_opus(monkeypatch)
    router.update_model(_sim(), {"enabled": False})
    for _ in range(3):
        usage.record(model_name="Claude Opus 5.5", kind="llm", ok=False, error=LIMIT, agent_id=_gm())
    assert router.limit_blocked(router.resolve_chain("general_manager"))
    with pytest.raises(registry.ModelLimitReached, match="usage limit"):
        registry.staff("general_manager")


def test_the_executor_refuses_while_off_and_test_still_runs_it():
    sim = router.resolve_chain("developer")[-1]  # still on: the choice to run it with
    router.update_model(_sim(), {"enabled": False})
    res = executors.for_choice(sim, 60).run(executors.Request(purpose="task", role="developer", system="", prompt="x"))
    assert not res.ok and "switched off" in res.error
    out = router.test_model(_sim())
    assert out["ok"] and out["status"]["ok"]
    assert router.model_info(next(m for m in _models() if m.kind == "offline"))["status"] == "inactive"


def _models() -> list[ModelEntry]:
    with session() as s:
        rows = list(s.scalars(select(ModelEntry)))
        for m in rows:
            s.expunge(m)
        return rows
