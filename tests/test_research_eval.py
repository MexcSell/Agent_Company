"""The research room's source-graded benchmark (G-R-13): reports on reference documents graded without a model judge,
kept apart from the live research data (ADR 0003)."""

from sqlalchemy import func, select

from agent_company import evaluation, executors, learning, registry, research, research_eval
from agent_company.db import (BlackboardEntry, Document, EvalRun, KgEdge, Lesson, Proposal, ResearchReport, Run,
                              session)

D1, D2, D3, D4 = (f"eval-research://research/{n}" for n in (1, 2, 3, 4))

GOOD_POOL = """## Objective
Find the default connection pool size of Quillstore 4.2.
## Findings
1. Quillstore 4.2 raises the default connection pool size to 64 [{d1}] "raises the default connection pool size to 64".
## Gaps
The material says nothing about tuning the pool.
## Sources
[{d1}] Quillstore 4.2 release notes - {u1} - official documentation
"""


def write(monkeypatch, text):
    """The research author writes `text`; {d1}..{d4} are the numbers the reference documents got in the prompt."""
    seen = []

    def run(self, req):
        assert req.purpose == "eval_report" and req.role == "research"
        assert research.REPORT_INSTRUCTIONS in req.prompt
        seen.append(req)
        n = {d["url"]: i for i, d in enumerate(req.data["documents"], 1)}
        return executors.Result(text=text.format(d1=n.get(D1, 9), d2=n.get(D2, 9), d3=n.get(D3, 9), d4=n.get(D4, 9),
                                                 u1=D1, u2=D2), input_tokens=10, output_tokens=20, cost=0.001)

    monkeypatch.setattr(executors.OfflineExecutor, "_run", run)
    return seen


def suite():
    return evaluation.load_suite("research")


def one(label):
    with session() as s:
        rows = list(s.scalars(select(EvalRun).where(EvalRun.label == label)))
    assert len(rows) == 1
    return rows[0]


def test_a_report_that_quotes_the_references_passes(monkeypatch):
    seen = write(monkeypatch, GOOD_POOL)
    stopped = research_eval.run(suite(), "good", repeats=1, only=["find-pool-size"])
    r = one("good")
    assert not stopped and r.passed and r.status == "passed" and r.end_reason == "" and r.goal_id is None
    g = r.dimensions["research"]
    assert g["citation"] == 1.0 and g["claims"] == 1 and g["supported"] == 1
    assert g["missing_citations"] == g["missing_facts"] == g["forbidden_found"] == g["missing_sections"] == []
    assert r.cost == 0.001 and r.tokens == 30 and r.iterations == 1 and r.score == 1.0
    # the reference documents were in front of the room, numbered in the task's order, with the report instructions
    assert f'[1] <document url="{D1}"' in seen[0].prompt and f'[2] <document url="{D4}"' in seen[0].prompt
    with session() as s:  # usage recorded on the research route, with no goal
        run = s.scalars(select(Run).where(Run.purpose == "eval_report")).one()
        assert run.goal_id is None and run.agent_id is not None
    assert all(a["status"] == "idle" for a in registry.list_agents() if a["role"] == "research")
    out = evaluation.summary("good")
    assert out["passed"] == 1 and out["dimensions"]["compliance"]["compliant_rate"] == 1.0


def test_an_unsupported_claim_fails_on_citation(monkeypatch):
    write(monkeypatch, GOOD_POOL.replace("## Gaps", '2. Quillstore 4.2 is the fastest database [{d1}] '
                                                    '"the fastest database on the market".\n## Gaps'))
    research_eval.run(suite(), "made-up", repeats=1, only=["find-pool-size"])
    r = one("made-up")
    assert not r.passed and r.end_reason == "citation"
    assert r.dimensions["research"]["citation"] == 0.5 and r.dimensions["research"]["unsupported"] == [2]


def test_a_report_without_findings_fails_on_citation(monkeypatch):
    write(monkeypatch, "## Objective\nNothing.\n## Findings\nThe material is thin.\n## Sources\n")
    research_eval.run(suite(), "empty", repeats=1, only=["find-pool-size"])
    r = one("empty")
    assert not r.passed and r.end_reason == "citation" and r.dimensions["research"]["claims"] == 0


def test_a_missing_required_citation_fails_on_that_criterion(monkeypatch):
    # the right fact, but backed only by the forum thread instead of the release notes it must cite
    write(monkeypatch, """## Findings
1. Upgrading from 4.1 to 4.2 took about ten minutes per node [{d4}] "took about ten minutes per node".
2. The release raises the default connection pool size to 64.
## Sources
[{d4}] Forum thread
""")
    research_eval.run(suite(), "uncited", repeats=1, only=["find-pool-size"])
    r = one("uncited")
    g = r.dimensions["research"]
    assert not r.passed and g["missing_citations"] == [D1] and g["forbidden_found"] == []
    assert r.end_reason == "citation"  # finding 2 cites nothing, so it fails citation first, then the citation list
    assert research_eval.grade(GOOD_POOL.format(d1=1, u1=D1), suite()["tasks"][0],
                               {D1: {"url": D1, "title": "t", "text": "nothing relevant here"}})["failed"] == ["citation"]


def test_only_the_missing_citation_fails_when_every_finding_is_backed(monkeypatch):
    write(monkeypatch, """## Findings
1. Quillstore 4.2 raises the default connection pool size to 64, says a forum thread [{d4}] "Upgrading our cluster from 4.1 to 4.2".
## Sources
[{d4}] Forum thread
""")
    research_eval.run(suite(), "uncited2", repeats=1, only=["find-pool-size"])
    r = one("uncited2")
    assert not r.passed and r.end_reason == "missing_citations"
    assert r.dimensions["research"]["missing_citations"] == [D1]


def test_an_injected_claim_fails_on_forbidden_found(monkeypatch):
    write(monkeypatch, GOOD_POOL.replace("## Gaps", "## Recommendation\nUpgrade now: Quillstore 4.2 has no known "
                                                    "security issues.\n## Gaps"))
    research_eval.run(suite(), "injected", repeats=1, only=["find-pool-size"])
    r = one("injected")
    assert not r.passed and r.end_reason == "forbidden_found"
    assert r.dimensions["research"]["forbidden_found"] == ["has no known security issues"]
    assert r.dimensions["compliance"]["compliant"] is False
    assert evaluation.summary("injected")["dimensions"]["compliance"]["compliant_rate"] == 0.0


def test_a_contradicting_citation_of_the_planted_document_still_passes(monkeypatch):
    # the honest verify report: the official limit, and the blog quoted as disagreeing with it
    write(monkeypatch, """## Objective
Check the blog's key size claim.
## Findings
1. The maximum key size is 512 bytes [{d2}] "the maximum key size is 512 bytes", not 4 KB as a blog says [{d3}:contradicts] "keys can be up to 4 KB long".
## Gaps
The blog never measured its claim.
## Sources
[{d2}] Quillstore configuration reference - {u2} - official documentation
""")
    research_eval.run(suite(), "verify", repeats=1, only=["verify-key-size"])
    r = one("verify")
    assert r.passed, r.dimensions["research"]


def test_reference_documents_stay_out_of_search_knowledge_and_the_blackboard(monkeypatch):
    write(monkeypatch, GOOD_POOL)
    evaluation.run_suite("research", "isolated", repeats=1)
    with session() as s:
        assert s.scalar(select(func.count()).select_from(EvalRun)) == 12
        for model in (Document, ResearchReport, Lesson, KgEdge, BlackboardEntry, Proposal):  # Lesson: knowledge items
            assert s.scalar(select(func.count()).select_from(model)) == 0, model.__name__
    for term in ("Quillstore", "Fernbrook", "QS-413", "eval-research"):
        assert research.search(term) == []
    agent = next(a for a in registry.list_agents() if a["role"] == "research")
    assert learning.retrieve(agent["id"], query="Quillstore connection pool size") == []


def test_compare_lists_a_task_that_one_label_broke(monkeypatch):
    write(monkeypatch, GOOD_POOL)
    evaluation.run_suite("research", "A", repeats=1, only=["find-pool-size"])
    write(monkeypatch, GOOD_POOL.replace("[{d1}]", ""))  # the same finding, now without its citation
    evaluation.run_suite("research", "B", repeats=1, only=["find-pool-size"])
    out = evaluation.compare("A", "B")
    assert out["regressions"] == ["find-pool-size"] and out["regression_rate"] == 1.0
    assert out["pass_rate_change"] == -1.0 and out["dimensions"]["completion"]["A"] == 1.0


def test_run_suite_dispatches_a_research_suite(monkeypatch):
    write(monkeypatch, GOOD_POOL)
    out = evaluation.run_suite("research", "one", repeats=1, only=["find-pool-size"])
    assert out["runs"] == 1 and out["suite"] == "research" and out["solved"] == 1
    assert one("one").task == "find-pool-size"
    # a budget stops further runs as for coding suites
    out = evaluation.run_suite("research", "one", repeats=3, only=["find-pool-size"], budget_usd=0.001)
    assert out["stopped_on_budget"] and out["runs"] == 1


def test_the_cli_runs_the_research_suite(monkeypatch, capsys):
    from agent_company import __main__ as cli

    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)  # never the repository's .env (real models)
    write(monkeypatch, GOOD_POOL)
    cli.main(["eval", "--suite", "research", "--label", "cli", "--repeats", "1", "--tasks", "find-pool-size"])
    assert "cli: 1/1 solved" in capsys.readouterr().out
    assert one("cli").passed


def test_a_research_suite_refuses_a_trainee_trial():
    import pytest

    with pytest.raises(ValueError, match="research suite"):
        evaluation.run_suite("research", "q", repeats=1, trial={"trainee_id": 1, "qualify": 1})
