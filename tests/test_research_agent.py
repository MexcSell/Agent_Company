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


def test_search_papers_stores_each_openalex_result_under_its_landing_page():
    import httpx

    def handler(request):
        assert request.url.host == "api.openalex.org" and request.url.params["search"] == "verbal reinforcement"
        return httpx.Response(200, json={"results": [
            {"id": "https://openalex.org/W1", "title": "Reflexion: Language Agents with Verbal Reinforcement Learning",
             "publication_date": "2023-03-20", "abstract_inverted_index": {"Large": [0], "agents": [2], "language": [1]},
             "primary_location": {"landing_page_url": "https://arxiv.org/abs/2303.11366"}},
            {"id": "x", "title": "", "primary_location": {}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = research_agent.TOOLS["search_papers"][2]({"query": "verbal reinforcement"}, {"client": client})
    assert len(out["documents"]) == 1
    with session() as s:
        d = s.get(Document, out["documents"][0])
        assert d.url == "https://arxiv.org/abs/2303.11366" and "Large language agents" in d.text
        assert d.published_at.year == 2023


def test_the_planner_sees_the_passages_that_answer_and_what_it_fetched(monkeypatch):
    """V16: the planner saw the first 400 characters of each document (sqlite.org's navigation), so it fetched the
    same pages again and judged them partial."""
    nav = "\n".join(f"SQLite Home Menu About Documentation Download License Support {i}" for i in range(40))
    doc("https://sqlite.org/wal.html", "Write-Ahead Logging",
        nav + "\n\nIn WAL mode readers do not block writers and a writer does not block readers: concurrent readers "
              "see the last committed snapshot while the checkpoint copies the WAL back.\n" + nav)
    fetched: list[int] = []

    def fake_fetch(args, ctx):  # a page whose words the search does not rank
        with session() as s:
            d = Document(url=args["url"], title="Changelog", text="Release history 3.45 3.46", source_url=args["url"],
                         category="docs", flags=[])
            s.add(d)
            s.flush()
            fetched.append(d.id)
        return {"documents": list(fetched), "titles": ["Changelog"]}

    monkeypatch.setitem(research_agent.TOOLS, "fetch_url", ("url", lambda v: True, fake_fetch))
    plans = iter([json.dumps({"sufficient": False, "reason": "release notes",
                              "tasks": [{"action": "fetch_url", "url": "https://sqlite.org/changes.html"}]}),
                  json.dumps({"sufficient": True, "reason": "enough", "tasks": []})])
    prompts = []

    def fake_run(role, req, pref=None, **kw):
        prompts.append(req.prompt)
        return executors.Result(text=next(plans), ok=True), router.Choice(name="Codex CLI", kind="codex",
                                                                           model_id="", reason="route")

    monkeypatch.setattr(router, "run", fake_run)
    monkeypatch.setattr(router, "resolve", lambda *a, **k: router.Choice(name="Codex CLI", kind="codex", model_id="",
                                                                         reason="route"))

    class A:
        id, name, model_pref = 5, "Research Lead", None

    research_agent.gather("How does SQLite WAL mode handle concurrent readers?", [], A(), rounds=2)
    assert "concurrent readers see the last committed snapshot" in prompts[0]
    assert 'url="https://sqlite.org/wal.html"' in prompts[0] and "characters" in prompts[0]
    assert f'<document id="{fetched[0]}"' in prompts[1], "a page the loop fetched is shown in the next round"
    assert "fetch_url https://sqlite.org/changes.html -> 1 document(s)" in prompts[1]


def test_passages_keep_document_order_within_the_budget():
    text = "intro line\nalpha beta here\nnothing\nbeta only\n" + "alpha beta gamma " * 100
    out = research_agent._passages(text, ["alpha", "beta"], budget=400)
    assert len(out) <= 400 + 10 and out.index("alpha beta here") < out.index("beta only")
    assert "intro line" not in out
    assert research_agent._passages("no match at all", ["zeta"]) == "no match at all"
