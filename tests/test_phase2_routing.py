"""Verification phase 2: model routing. Agents are assigned models; each request runs through the loop's `_execute`
(the path every task takes) to local OpenAI-compatible stubs, and the backend's own record (`Run.trace`) must prove
which model got the request and why. Report: docs/verification/phase-2-routing.md."""
import threading

from sqlalchemy import select
from test_phase1_models import GOOD, Stub

import pytest

from agent_company import executors, loop, registry, router
from agent_company.db import Agent, Run, session

PROMPT = "Calculate 125 × 48. Return: 1. your answer, 2. your model identifier, 3. your provider identifier."
# what a model says about itself is not evidence: model B claims to be someone else
REPLY_A = "1. 6000\n2. model-a\n3. stub-a"
REPLY_B = "1. 6000\n2. gpt-5\n3. openai"


@pytest.fixture
def stubs():
    a, b = Stub("model-a", REPLY_A), Stub("model-b", REPLY_B)
    router.add_model({"name": "Model A", "kind": "llm", "model_id": "openai/model-a", "base_url": a.url, "api_key": GOOD})
    router.add_model({"name": "Model B", "kind": "llm", "model_id": "openai/model-b", "base_url": b.url, "api_key": GOOD})
    yield a, b
    a.srv.shutdown()
    b.srv.shutdown()


def _agent(role, model=None):
    """The role's agent; `model` is what the agent card's model menu stores (Agent.model_pref)."""
    with session() as s:
        a = s.scalars(select(Agent).where(Agent.role == role).order_by(Agent.id)).first()
        if model is not None:
            a.model_pref = model
        s.flush()
        s.expunge(a)
        return a


def _ask(agent):
    req = executors.Request(purpose="verify", role=agent.role, system="You are a routing test.", prompt=PROMPT)
    return loop._execute(agent, req, "verify")


def _runs(agent):
    with session() as s:
        rows = s.scalars(select(Run).where(Run.agent_id == agent.id).order_by(Run.id)).all()
        return [(r.model_name, r.ok, dict(r.trace), r.error) for r in rows]


def _model(name):
    return next(m for m in router.list_models() if m["name"] == name)


# --- 2.1 - 2.3 assignments, requests and the backend's record --------------------------------------------------

def test_each_agent_runs_on_its_assigned_model_and_the_trace_proves_it(stubs):
    a, b = stubs
    research, developer, qa = _agent("research", "Model A"), _agent("developer", "Model B"), _agent("qa", "Model A")
    for agent, model, stub in ((research, "Model A", a), (developer, "Model B", b), (qa, "Model A", a)):
        res, choice = _ask(agent)
        assert res.ok and "6000" in res.text and choice.name == model
        [(name, ok, trace, _)] = _runs(agent)
        assert (name, ok) == (model, True)
        assert trace["requested"] == trace["model"] == model, "assigned = resolved"
        assert trace["model_id"] == f"openai/{stub.model}" and trace["base_url"] == stub.url
        assert trace["served"] == stub.model, "the provider's own answer names the model"
        assert trace["route"] == "agent preference" and router.trace_issues(trace) == []
    assert [c["model"] for c in a.calls] == ["model-a", "model-a"], "research and QA reached provider A only"
    assert [c["model"] for c in b.calls] == ["model-b"], "the developer reached provider B only"
    trace = _runs(developer)[0][2]
    assert "gpt-5" in REPLY_B and (trace["model_id"], trace["served"]) == ("openai/model-b", "model-b"), \
        "the developer's reply claims gpt-5; the backend's record shows what ran"


def test_a_role_route_is_the_assignment_when_the_agent_has_none(stubs):
    a, _ = stubs
    router.set_route("qa", "Model A")
    qa = _agent("qa", "")
    res, _ = _ask(qa)
    [(name, ok, trace, _)] = _runs(qa)
    assert res.ok and name == "Model A" and trace["requested"] == "Model A" and trace["route"] == "route for qa"


def test_the_run_detail_shows_the_trace(stubs):
    dev = _agent("developer", "Model B")
    _ask(dev)
    with session() as s:
        run_id = s.scalars(select(Run.id).where(Run.agent_id == dev.id)).first()
    d = registry.agent_run(dev.id, run_id)
    assert d["trace"]["model"] == "Model B" and d["trace"]["served"] == "model-b" and d["trace_issues"] == []


# --- 2.4 routing failures ----------------------------------------------------------------------------------------

def _dev_on_b_with_fallback_a():
    router.set_route("developer", "Model B")
    router.set_fallback("developer", ["Model A"])
    return _agent("developer", "")


def test_a_disabled_model_moves_the_work_to_the_configured_fallback(stubs):
    a, b = stubs
    dev = _dev_on_b_with_fallback_a()
    router.update_model(_model("Model B")["id"], {"enabled": False})
    res, choice = _ask(dev)
    assert res.ok and choice.name == "Model A" and not b.calls
    [(name, ok, trace, _)] = _runs(dev)
    assert name == "Model A" and trace["requested"] == "Model B" and trace["route"] == "fallback for developer"
    assert router.trace_issues(trace) == [], "an explicitly configured fallback explains the difference"


def test_invalid_credentials_fail_on_the_model_then_the_fallback_takes_it(stubs):
    a, b = stubs
    dev = _dev_on_b_with_fallback_a()
    router.update_model(_model("Model B")["id"], {"api_key": "sk-wrong-0123456789abcdef"})
    res, choice = _ask(dev)
    assert res.ok and choice.name == "Model A"
    runs = _runs(dev)
    assert [(n, ok) for n, ok, _, _ in runs] == [("Model B", False), ("Model A", True)]
    assert "AuthenticationError" in runs[0][3] and runs[0][2]["model"] == "Model B" and runs[0][2]["served"] == ""
    assert runs[1][2]["route"] == "fallback for developer" and runs[1][2]["requested"] == "Model B"


def test_an_unreachable_model_is_retried_then_the_fallback_takes_it(stubs):
    a, _ = stubs
    dev = _dev_on_b_with_fallback_a()
    router.update_model(_model("Model B")["id"], {"base_url": "http://127.0.0.1:1/v1"})
    res, choice = _ask(dev)
    assert res.ok and choice.name == "Model A"
    runs = _runs(dev)
    tries = [r for r in runs if r[0] == "Model B"]
    assert len(tries) == loop.settings().retry_max_attempts and not any(ok for _, ok, _, _ in tries)
    assert runs[-1][:2] == ("Model A", True) and runs[-1][2]["route"] == "fallback for developer"


def test_without_a_fallback_a_failing_model_fails_the_work(stubs):
    a, b = stubs
    router.set_route("developer", "Model B")
    router.set_fallback("developer", [])
    dev = _agent("developer", "")
    router.update_model(_model("Model B")["id"], {"api_key": "sk-wrong-0123456789abcdef"})
    res, choice = _ask(dev)
    assert not res.ok and choice.name == "Model B" and not a.calls
    assert [(n, ok) for n, ok, _, _ in _runs(dev)] == [("Model B", False)], "no other model, not the simulator"


def test_without_a_fallback_a_disabled_model_leaves_the_most_suitable_model_and_the_trace_says_so(stubs):
    """V7, the owner's decision of 2026-11-09: with no configured model usable, the catalog's most suitable usable
    model for the role takes the work, not the simulator; the trace names why."""
    a, _ = stubs
    router.set_route("developer", "Model B")
    router.set_fallback("developer", [])
    dev = _agent("developer", "")
    router.update_model(_model("Model B")["id"], {"enabled": False})
    res, choice = _ask(dev)
    assert res.ok and choice.name == "Model A" and [c["model"] for c in a.calls] == ["model-a"]
    [(name, ok, trace, _)] = _runs(dev)
    assert (name, ok) == ("Model A", True) and trace["requested"] == "Model B"
    assert trace["route"].startswith("most suitable for developer (lead tier;") and "Model B: disabled" in trace["route"]
    assert router.trace_issues(trace) == [], "the owner's rule explains it"


def test_with_no_real_model_usable_the_simulator_stands_in_and_the_trace_says_so(stubs):
    router.set_route("developer", "Model B")
    router.set_fallback("developer", [])
    dev = _agent("developer", "")
    for name in ("Model A", "Model B"):
        router.update_model(_model(name)["id"], {"enabled": False})
    res, choice = _ask(dev)
    assert choice.kind == "offline"
    [(_, _, trace, _)] = _runs(dev)
    assert trace["provider"] == "offline" and trace["requested"] == "Model B" and "Model B: disabled" in trace["route"]
    assert router.trace_issues(trace) == [f"the offline simulator stood in for Model B ({trace['route']})"]


def test_a_limited_model_without_a_fallback_leaves_the_most_suitable_model_instead_of_a_pause(stubs):
    router.set_route("developer", "Model B")
    router.set_fallback("developer", [])
    router.mark_limited("Model B", "You've hit your session limit · resets 9:50pm")
    chain = router.resolve_chain("developer")
    assert chain[0].name == "Model A" and router.limit_blocked(chain) == ""
    router.mark_limited("Model A", "You've hit your session limit · resets 9:50pm")
    assert router.limit_blocked(router.resolve_chain("developer")), "every model limited: the goal still pauses"


def test_two_agents_on_different_models_at_the_same_time(stubs):
    a, b = stubs
    research, dev = _agent("research", "Model A"), _agent("developer", "Model B")
    gate = threading.Barrier(2)
    out = {}

    def ask(agent):
        gate.wait()
        out[agent.role] = _ask(agent)

    threads = [threading.Thread(target=ask, args=(x,)) for x in (research, dev)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert out["research"][1].name == "Model A" and out["developer"][1].name == "Model B"
    assert {c["model"] for c in a.calls} == {"model-a"} and {c["model"] for c in b.calls} == {"model-b"}
    for agent, model, served in ((research, "Model A", "model-a"), (dev, "Model B", "model-b")):
        [(name, ok, trace, _)] = _runs(agent)
        assert ok and name == trace["requested"] == model and trace["served"] == served


def test_a_provider_answering_as_another_model_is_flagged(stubs):
    a, _ = stubs
    a.served = "model-z"
    research = _agent("research", "Model A")
    res, _ = _ask(research)
    [(_, ok, trace, _)] = _runs(research)
    assert ok and trace["served"] == "model-z"
    assert router.trace_issues(trace) == ["the provider answered as model-z, not model-a"]


def test_trace_issues_name_an_unexplained_substitution():
    base = {"requested": "Model B", "model": "Model A", "model_id": "openai/model-a", "provider": "local"}
    assert router.trace_issues({**base, "route": "fallback for developer"}) == []
    assert router.trace_issues({**base, "route": "escalation for developer"}) == []
    assert router.trace_issues({**base, "route": "different family from anthropic (route for qa)"}) == []
    assert router.trace_issues({**base, "route": "route for developer"}) == [
        "Model A ran instead of the assigned Model B without a configured fallback (route for developer)"]
    assert router.trace_issues({**base, "requested": "Model A", "served": "model-a-2026-01-01"}) == [], "dated id"


def test_claude_code_reports_the_model_that_answered():
    stream = "\n".join([
        '{"type":"system","subtype":"init","model":"claude-opus-5-5"}',
        '{"type":"assistant","message":{"model":"claude-opus-5-5-20261101","content":[{"type":"text","text":"6000"}]}}',
        '{"type":"result","result":"6000","usage":{}}'])
    assert executors.claude_served(stream) == "claude-opus-5-5-20261101"
    assert executors.claude_served(stream.split("\n")[0]) == "claude-opus-5-5", "no answer: the session's model"
