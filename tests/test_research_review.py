"""Research reports with claim-level evidence, an independent review, knowledge transfer and research proposals
(ASEAO06 S06-R-03, R-04, R-06, R-07)."""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import blackboard, executors, loop, projects, research, router
from agent_company.api import create_app
from agent_company.db import Document, Goal, Lesson, Notification, Project, Proposal, ResearchReport, session


def doc(url, title, text):
    with session() as s:
        d = Document(url=url, title=title, text=text, source_url=url, category=research.classify_source(url), flags=[])
        s.add(d)
        s.flush()
        return d.id


def write_report(monkeypatch, text, review=None):
    """The author writes `text`; the reviewer answers `review` (default: the offline reviewer)."""
    real = executors.OfflineExecutor._run

    def run(self, req):
        if req.purpose == "report":  # {rabbit} and {nats} are the sources' numbers in the material
            n = {d["url"]: i for i, d in enumerate(req.data["documents"], 1)}
            return executors.Result(text=text.format(rabbit=n.get(RABBIT_URL, 9), nats=n.get(NATS_URL, 9)),
                                    input_tokens=1, output_tokens=1)
        if req.purpose == "report_review" and review is not None:
            return executors.Result(text=review(req), input_tokens=1, output_tokens=1)
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_run", run)


def research_items():
    with session() as s:
        return list(s.scalars(select(Lesson).where(Lesson.kind == "research")))


QUEUE = "RabbitMQ delivers messages at least once and supports publisher confirms for durable queues."
NATS = "NATS core offers at most once delivery; JetStream adds at least once delivery with acknowledgements."


RABBIT_URL, NATS_URL = "https://www.rabbitmq.com/docs/reliability", "https://docs.nats.io/jetstream"


def two_sources():
    doc(RABBIT_URL, "RabbitMQ reliability", QUEUE * 3)
    doc(NATS_URL, "NATS JetStream", NATS * 3)


def test_an_approved_report_keeps_only_backed_claims_and_feeds_goal_and_knowledge(monkeypatch):
    two_sources()
    write_report(monkeypatch, """## Objective
Which queue gives at least once delivery?
## Findings
1. RabbitMQ delivers at least once with publisher confirms [{rabbit}] "delivers messages at least once and supports publisher confirms".
2. NATS JetStream acknowledges messages for at least once delivery [{nats}] "JetStream adds at least once delivery".
3. Kafka is the fastest queue on the market [{rabbit}] "Kafka beats every other queue".
## Gaps
Throughput was not measured.
## Recommendation
Use RabbitMQ.
## Sources
[{rabbit}] RabbitMQ reliability - https://www.rabbitmq.com/docs/reliability - official documentation
""")
    gid = loop.create_goal("Pick a queue")
    rid = research.request_report("Which message queue gives at least once delivery?", goal_id=gid,
                                  keywords=["queue", "delivery"])
    out = research.run_report(rid)
    assert out["status"] == "done" and out["review"] == "approved"
    by_n = {c["n"]: c for c in out["claims"]}
    assert by_n[1]["support"] == "supported" and by_n[1]["evidence"][0]["match"] == "quote"
    assert by_n[1]["evidence"][0]["source_id"] and by_n[1]["evidence"][0]["location"] is not None
    assert by_n[3]["support"] == "unsupported" and by_n[3]["status"] == "unsupported"  # fabricated quote (AT-015)
    # the offline author and reviewer are one model family: agreed, but not independently verified
    assert by_n[1]["status"] == by_n[2]["status"] == "supported" and out["review_provider"] == "offline"
    assert out["reviewer_agent_id"] and out["reviewer_agent_id"] != out["agent_id"]
    assert "Kafka" not in out["approved_report"] and "Kafka" in out["report"]
    assert "same model family" in out["approved_report"] and "1 dropped" in out["approved_report"]
    with session() as s:
        stored = s.scalars(select(Document).where(Document.url == f"report://{rid}")).one()
        assert "Kafka" not in stored.text
    [entry] = [e for e in blackboard.read(gid, ["research"]) if e["key"] == f"report:{rid}"]
    assert "Kafka" not in entry["content"]
    items = research_items()
    assert len(items) == 2 and {i.evidence_class for i in items} == {"INFERENCE"}
    assert all(i.review_due_at is not None and f"report:{rid}" in i.sources for i in items)
    assert any("url:https://www.rabbitmq.com/docs/reliability" in i.sources for i in items)
    assert all(i.goal_id == gid and i.agent_id == out["agent_id"] for i in items)


def test_a_reviewer_of_another_provider_verifies_claims_as_facts(monkeypatch):
    two_sources()
    write_report(monkeypatch, "## Findings\n1. RabbitMQ supports publisher confirms [{rabbit}] \"supports publisher confirms\".")
    other = router.Choice(name="other-family", kind="offline", model_id="x", reason="test")
    monkeypatch.setattr(type(other), "provider", property(lambda c: "openai" if c.name == "other-family" else "offline"))
    real = research.review_report
    monkeypatch.setattr(research, "review_report", lambda rid, **kw: real(rid, **{**kw, "reviewer_choice": other}))
    out = research.run_report(research.request_report("Does RabbitMQ confirm publishes?"))
    assert out["review"] == "approved" and out["review_provider"] == "openai"
    assert out["claims"][0]["status"] == "verified" and "same model family" not in out["approved_report"]
    assert [i.evidence_class for i in research_items()] == ["FACT"]


def test_disagreeing_sources_are_both_kept_as_unresolved(monkeypatch):
    two_sources()
    write_report(monkeypatch, """## Findings
1. Delivery is at least once [{rabbit}] "delivers messages at least once" [{nats}:contradicts] "NATS core offers at most once delivery".
2. RabbitMQ supports publisher confirms [{rabbit}] "supports publisher confirms".
""")
    out = research.run_report(research.request_report("Is delivery at least once?"))
    c1 = out["claims"][0]
    assert c1["support"] == "contested" and c1["status"] == "contested"
    assert {e["relationship"] for e in c1["evidence"]} == {"supports", "contradicts"}
    text = out["approved_report"]
    assert "## Uncertain" in text and "at most once" in text and "retrieved" in text
    [item] = research_items()  # the contested claim stays in the report only
    assert item.text.startswith("RabbitMQ supports publisher confirms")


def test_a_rejected_report_feeds_nothing(monkeypatch):
    two_sources()
    write_report(monkeypatch, "## Findings\n1. RabbitMQ supports publisher confirms [{rabbit}] \"supports publisher confirms\".",
                 review=lambda req: "C1: DISAGREE the excerpt is out of context\nVERDICT: REJECT one-sided material")
    gid = loop.create_goal("Queue")
    out = research.run_report(research.request_report("Does RabbitMQ confirm publishes?", goal_id=gid))
    assert out["review"] == "rejected" and out["claims"][0]["status"] == "unverified"
    assert out["review_note"] == "one-sided material" and out["approved_report"] == ""
    assert not [e for e in blackboard.read(gid, ["research"]) if e["key"].startswith("report:")]
    assert not research_items()
    assert research.search("Research report RabbitMQ confirm") == [] or all(
        not h["url"].startswith("report://") for h in research.search("Research report RabbitMQ confirm"))
    with pytest.raises(ValueError):  # settled: no second decision
        research.decide_report(out["id"], True, "user:admin")
    again = research.submit("Does RabbitMQ confirm publishes?", goal_id=gid)  # a rejected report answers nothing
    assert again["duplicate"] is False and again["id"] != out["id"]


def test_a_review_cut_off_by_a_restart_runs_again(monkeypatch):
    two_sources()
    write_report(monkeypatch, "## Findings\n1. RabbitMQ supports publisher confirms [{rabbit}] \"supports publisher confirms\".")
    monkeypatch.setattr(research, "review_report", lambda rid, **kw: {"id": rid})  # cut off before the review
    rid = research.run_report(research.request_report("Does RabbitMQ confirm publishes?"))["id"]
    monkeypatch.undo()
    with session() as s:
        s.get(ResearchReport, rid).review = "in_review"  # the restart came during the reviewer's model call
        waiting = research.request_report("A report already waiting for a person?")
        r = s.get(ResearchReport, waiting)
        r.status, r.review, r.review_note = "done", "in_review", "Waits for a person: no verdict"
    assert research.resume_reviews() == [rid]
    with session() as s:
        assert s.get(ResearchReport, rid).review == "approved"
        assert s.get(ResearchReport, waiting).review == "in_review"


def test_no_verdict_waits_for_a_person_whose_approval_verifies(monkeypatch):
    two_sources()
    write_report(monkeypatch, "## Findings\n1. RabbitMQ supports publisher confirms [{rabbit}] \"supports publisher confirms\".",
                 review=lambda req: "Looks fine to me.")
    out = research.run_report(research.request_report("Does RabbitMQ confirm publishes?"))
    assert out["review"] == "in_review" and "person" in out["review_note"]
    with session() as s:
        assert s.scalars(select(Notification).where(Notification.source == "research")).first() is not None
        assert s.scalars(select(Document).where(Document.url == f"report://{out['id']}")).first() is None
    with pytest.raises(ValueError):
        research.decide_report(out["id"], False, "user:admin", "")  # a rejection says why
    done = research.decide_report(out["id"], True, "user:admin")
    assert done["review"] == "approved" and done["review_provider"] == "person"
    assert done["claims"][0]["status"] == "verified" and [i.evidence_class for i in research_items()] == ["FACT"]


def test_a_secret_project_keeps_its_claims_out_of_knowledge(monkeypatch):
    two_sources()
    with session() as s:
        p = Project(name="Classified", classification="secret")
        s.add(p)
        s.flush()
        pid = p.id
    write_report(monkeypatch, "## Findings\n1. RabbitMQ supports publisher confirms [{rabbit}] \"supports publisher confirms\".")
    out = research.run_report(research.request_report("Does RabbitMQ confirm publishes?", project_id=pid))
    assert out["review"] == "approved" and not research_items()


def test_an_approved_report_raises_a_research_proposal_on_the_normal_path(monkeypatch):
    two_sources()
    write_report(monkeypatch, """## Findings
1. RabbitMQ supports publisher confirms [{rabbit}] "supports publisher confirms".
## Recommendation
Turn confirms on.
PROPOSAL: Enable publisher confirms in the worker :: lost messages on restart, see [{rabbit}]
""")
    gid = loop.create_goal("Queue reliability")
    with session() as s:
        s.get(Goal, gid).status = "awaiting_approval"  # the goal is no longer running
    out = research.run_report(research.request_report("Does RabbitMQ confirm publishes?", goal_id=gid))
    assert out["review"] == "approved" and len(out["proposal_ids"]) == 1
    assert "PROPOSAL" not in out["approved_report"]
    with session() as s:
        p = s.get(Proposal, out["proposal_ids"][0])
        assert p.tag == "research" and p.report_id == out["id"] and p.goal_id == gid and p.status == "pending"
        assert "approved research report" in p.description and "publisher confirms" in p.description
    decided = projects.decide(out["proposal_ids"][0], False, "admin")
    assert decided["status"] == "rejected" and decided["tag"] == "research" and decided["report_id"] == out["id"]
    with session() as s:  # the same report approved again raises no second proposal
        s.get(ResearchReport, out["id"]).review = "in_review"
    again = research.decide_report(out["id"], True, "user:admin")
    assert again["proposal_ids"] == []


def test_a_report_without_a_goal_raises_no_proposal(monkeypatch):
    two_sources()
    write_report(monkeypatch, "## Findings\n1. RabbitMQ supports publisher confirms [{rabbit}] \"supports publisher confirms\".\n"
                              "## Recommendation\nPROPOSAL: Enable confirms :: see [{rabbit}]\n")
    out = research.run_report(research.request_report("Does RabbitMQ confirm publishes?"))
    assert out["review"] == "approved" and out["proposal_ids"] == []


def test_the_api_lets_an_administrator_decide_a_waiting_report(monkeypatch):
    two_sources()
    write_report(monkeypatch, "## Findings\n1. RabbitMQ supports publisher confirms [{rabbit}] \"supports publisher confirms\".",
                 review=lambda req: "no verdict")
    rid = research.run_report(research.request_report("Does RabbitMQ confirm publishes?"))["id"]
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        [r] = app.get("/api/research/requests").json()
        assert r["review"] == "in_review" and r["claims"][0]["support"] == "supported"
        assert app.post(f"/api/research/requests/{rid}/review", json={"approve": False}).status_code == 400
        out = app.post(f"/api/research/requests/{rid}/review", json={"approve": True}).json()
        assert out["review"] == "approved" and out["reviewed_by"] == "user:admin"
        assert app.post("/api/research/requests/999/review", json={"approve": True}).status_code == 404


def test_ui_res_01_the_design_sections_are_asked_for_kept_and_listed():
    from agent_company import research
    from agent_company.db import ResearchReport, now

    for name in research.REPORT_SECTIONS:
        assert f"## {name}" in research.REPORT_INSTRUCTIONS
    text = ("## Objective\nPick a queue.\n## Scope\nOnly Python libraries.\n## Findings\n1. A is fast [1] \"fast\"\n"
            "## Comparison\nnone\n## Risks\nA is young.\n## Implementation impact\nOne module changes.\n"
            "## Confidence\nmedium: two sources.\n## Sources\n[1] A - https://a.example - docs")
    r = ResearchReport(question="Which queue?", report=text, review="draft", sources=[], claims=[], created_at=now())
    assert research.report_dict(r)["sections"] == ["Objective", "Scope", "Findings", "Risks",
                                                   "Implementation impact", "Confidence", "Sources"]
    out = research._approved_text(r, [], 0, "qa")
    assert out.index("## Scope") < out.index("## Findings") < out.index("## Risks") < out.index("## Sources")
    assert "## Comparison" not in out, "an empty section is not carried over"
