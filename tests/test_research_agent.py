"""The research lead's tool loop: its model (through the router) plans, the research lead validates the plan and runs
its own tools, the documents found join the report's material, and the steps are kept on the report."""

import json

from agent_company import executors, research, research_agent, router
from agent_company.db import Document, ResearchReport, session


def doc(url, title, text):
    with session() as s:
        s.add(Document(url=url, title=title, text=text, source_url=url, category=research.classify_source(url),
                       flags=[]))


def test_a_plan_is_validated_against_the_registered_tools():
    plan = research_agent.parse_plan('Sure: {"sufficient": false, "reason": "need docs", "tasks": ['
                                     '{"action": "search_knowledge", "query": "vector databases"},'
                                     '{"action": "run_shell", "query": "rm -rf /"},'
                                     '{"action": "fetch_url", "url": "file:///etc/passwd"},'
                                     '{"action": "search_papers", "query": "x"}]}')
    assert plan["tasks"] == [{"action": "search_knowledge", "query": "vector databases"}]
    assert {r["why"] for r in plan["refused"]} == {"unknown tool", "bad url", "bad query"}
    assert research_agent.parse_plan("no json here") is None
    many = {"tasks": [{"action": "search_knowledge", "query": f"q{i} terms"} for i in range(9)]}
    assert len(research_agent.parse_plan(json.dumps(many))["tasks"]) == research_agent.MAX_TASKS


def test_the_simulator_never_plans():
    class A:
        id, name, model_pref = 5, "Research Lead", None

    assert research_agent.gather("Which queue?", [], A()) == {"documents": [], "steps": []}


def test_the_model_plans_the_research_lead_runs_its_tools_and_the_report_keeps_the_steps(monkeypatch):
    doc("https://docs.example/nats", "NATS overview", "nats message queue for small teams " * 10)
    plans = iter([json.dumps({"sufficient": False, "reason": "look up NATS",
                              "tasks": [{"action": "search_knowledge", "query": "nats queue"}]}),
                  json.dumps({"sufficient": True, "reason": "enough", "tasks": []})])
    prompts = []

    def fake_run(role, req, pref=None, **kw):
        prompts.append(req.purpose)
        if req.purpose == "research_plan":
            return executors.Result(text=next(plans), ok=True), router.Choice(name="Codex CLI", kind="codex",
                                                                               model_id="", reason="route")
        return executors.Result(text="## Findings\n1. NATS fits small teams [1: supports]", ok=True), \
            router.Choice(name="Codex CLI", kind="codex", model_id="", reason="route")

    monkeypatch.setattr(router, "run", fake_run)
    monkeypatch.setattr(router, "resolve", lambda *a, **k: router.Choice(name="Codex CLI", kind="codex", model_id="",
                                                                         reason="route"))

    def no_review(report_id, **kw):  # the independent review has its own tests
        with session() as s:
            return research.report_dict(s.get(ResearchReport, report_id))

    monkeypatch.setattr(research, "review_report", no_review)
    rid = research.submit("Which message queue for a small team?")["id"]
    out = research.run_report(rid)
    assert prompts == ["research_plan", "research_plan", "report"]
    steps = out["tool_steps"]
    assert steps[0]["results"][0]["action"] == "search_knowledge" and steps[0]["results"][0]["documents"]
    assert steps[1]["sufficient"] is True
    assert any(s["url"] == "https://docs.example/nats" for s in out["sources"])
