"""A model's usage limit: the router skips it until the reset and the role's fallbacks take the work; the agents are
not degraded for it and are not cloned (2026-11-06: a Claude session limit made 41 clones of the GM)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from agent_company import loop, registry, router, usage
from agent_company.db import Agent, ModelEntry, session

LIMIT = "You've hit your session limit · resets 9:50pm (Europe/Berlin)"


def _gm():
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == "general_manager", Agent.parent_id.is_(None))).first().id


def _two_models(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    router.set_route("general_manager", "Claude Opus 5.5")
    registry.update_agent(_gm(), {"model_pref": None})


def test_limit_messages_and_their_reset_times():
    at = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)  # 19:00 in Berlin
    assert router.is_limit_error(LIMIT)
    assert router.is_limit_error("You've hit your usage limit. Upgrade to Pro or try again in 2 hours 5 minutes.")
    assert router.is_limit_error('{"type":"usage_limit_reached"}')
    assert not router.is_limit_error("APIError: 500 from the provider")
    assert not router.is_limit_error("429 Too Many Requests"), "a plain 429 is transient: it is retried"
    assert router.limit_reset(LIMIT, at) == datetime(2026, 11, 6, 20, 50, tzinfo=UTC)
    assert router.limit_reset("try again in 2 hours 5 minutes", at) == at + timedelta(hours=2, minutes=5)
    assert router.limit_reset("resets 5am", at) == datetime(2026, 11, 7, 5, 0, tzinfo=UTC), "the next 5am"
    assert router.limit_reset("hit your limit", at) == at + timedelta(minutes=router.LIMIT_DEFAULT_MINUTES)
    assert router.limit_reset("You've hit your weekly limit · resets Nov 8 at 3pm (Europe/Berlin)", at) == \
        datetime(2026, 11, 8, 14, 0, tzinfo=UTC)
    assert router.limit_reset("resets Jan 2, 10am", datetime(2026, 12, 30, tzinfo=UTC)) == \
        datetime(2027, 1, 2, 10, 0, tzinfo=UTC)


def test_a_limited_model_is_skipped_until_its_reset_and_the_fallback_takes_the_work(monkeypatch):
    _two_models(monkeypatch)
    assert [c.name for c in router.resolve_chain("general_manager")][:1] == ["Claude Opus 5.5"]
    assert router.set_fallback("general_manager", ["Claude Sonnet 5.5"]) == ["Claude Sonnet 5.5"]
    assert [c.name for c in router.resolve_chain("general_manager")][:2] == ["Claude Opus 5.5", "Claude Sonnet 5.5"]
    usage.record(model_name="Claude Opus 5.5", kind="llm", ok=False, error=LIMIT, agent_id=_gm())
    chain = router.resolve_chain("general_manager")
    assert chain[0].name == "Claude Sonnet 5.5" and chain[0].reason == "fallback for general_manager"
    opus = next(m for m in router.list_models() if m["name"] == "Claude Opus 5.5")
    assert opus["limited_until"] and "usage limit reached" in opus["unavailable_reason"]
    assert registry.agent_detail(_gm())["health"] == "ok", "a usage limit does not degrade the agent"
    with session() as s:  # the reset passed
        s.scalars(select(ModelEntry).where(ModelEntry.name == "Claude Opus 5.5")).first().limited_until = \
            datetime.now(UTC) - timedelta(minutes=1)
    assert router.resolve("general_manager").name == "Claude Opus 5.5"
    with pytest.raises(ValueError):
        router.set_fallback("general_manager", ["Offline simulator"])


def test_without_a_fallback_the_simulator_does_not_stand_in_and_staffing_waits(monkeypatch):
    _two_models(monkeypatch)
    gm = _gm()
    for _ in range(3):
        usage.record(model_name="Claude Opus 5.5", kind="llm", ok=False, error=LIMIT, agent_id=gm)
    chain = router.resolve_chain("general_manager")
    assert chain[0].kind == "offline" and router.limit_blocked(chain)
    with session() as s:
        before = s.scalar(select(func.count()).select_from(Agent))
    with pytest.raises(registry.ModelLimitReached, match="usage limit"):
        registry.staff("general_manager")
    with session() as s:
        assert s.scalar(select(func.count()).select_from(Agent)) == before, "no clone for a model limit"
    from agent_company import executors

    with session() as s:
        agent = s.get(Agent, gm)
        s.expunge(agent)
    res, choice = loop._execute(agent, executors.Request(purpose="task", role="general_manager", system="",
                                                          prompt="x"), "task")
    assert not res.ok and "usage limit" in res.error and choice.name == "(usage limit)"


def test_clones_are_capped(monkeypatch):
    from agent_company import prefs

    prefs.set_value("max_clones", 1, actor="user:admin")
    dev = registry.staff("developer", goal_id=None)
    clone = registry.staff("developer", goal_id=None)  # the original is busy: one clone
    assert clone.parent_id == dev.id
    with pytest.raises(registry.CloneLimitReached):
        registry.staff("developer", goal_id=None)
    assert registry.clone_agent(dev.id)["id"], "an administrator's own clone is not capped"


def test_another_family_can_be_an_agent_cli(monkeypatch):
    """EL-R-03: Codex reviews what a Claude model wrote when no API model of another family is usable."""
    from agent_company.config import reset_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ENABLE_CODEX", "1")
    reset_settings()
    monkeypatch.setattr(router.shutil, "which", lambda exe: f"/usr/bin/{exe}" if exe == "codex" else None)
    router.set_route("research", "Claude Opus 5.5")
    chain = router.resolve_chain("research", avoid_provider="anthropic")
    assert chain[0].kind == "codex" and chain[0].reason == "different family from anthropic"


def test_another_family_already_among_the_fallbacks_goes_first(monkeypatch):
    """The research room on Codex with Claude fallbacks: the review of a Codex-written report runs on Claude."""
    from agent_company.config import reset_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ENABLE_CODEX", "1")
    reset_settings()
    monkeypatch.setattr(router.shutil, "which", lambda exe: f"/usr/bin/{exe}" if exe == "codex" else None)
    router.set_route("research", "Codex CLI")
    router.set_fallback("research", ["Claude Sonnet 5.5"])
    chain = router.resolve_chain("research", avoid_provider="openai")
    assert chain[0].name == "Claude Sonnet 5.5" and chain[0].reason.startswith("different family from openai")
    assert chain[1].name == "Codex CLI"


def test_a_cli_request_without_a_worktree_gets_a_scratch_folder(monkeypatch):
    from agent_company import executors
    from agent_company.config import reset_settings, settings

    monkeypatch.setenv("ENABLE_CODEX", "1")
    reset_settings()
    monkeypatch.setattr(router.shutil, "which", lambda exe: f"/usr/bin/{exe}" if exe == "codex" else None)
    router.set_route("research", "Codex CLI")
    seen = []

    class Fake:
        def run(self, req):
            seen.append(req.workdir)
            return executors.Result(text="ok", ok=True)

    monkeypatch.setattr(executors, "for_choice", lambda choice, timeout=0: Fake())
    req = executors.Request(purpose="report", role="research", system="", prompt="x")
    res, choice = router.run("research", req)
    assert res.ok and choice.name == "Codex CLI"
    assert seen[0] == settings().worktrees_dir / "_router-research" and seen[0].is_dir()
