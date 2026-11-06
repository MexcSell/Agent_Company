"""Research requests by publication date range and language (ASEAO06 S06-FR-04): the declared language of a page
or feed, the request's filters, what the filters leave out of the material and how the report says so."""

import secrets
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from agent_company import db, executors, research
from agent_company.api import create_app
from agent_company.db import Document, ResearchReport, session


def site(pages):
    """A mock site: path -> (body, headers)."""
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        if req.url.path not in pages:
            return httpx.Response(404, text="nope")
        body, headers = pages[req.url.path]
        return httpx.Response(200, content=body if isinstance(body, bytes) else body.encode(), headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))


HTML = {"content-type": "text/html; charset=utf-8"}


def doc(url, text, published=None, language=""):
    with session() as s:
        s.add(Document(url=url, title=url.rsplit("/", 1)[-1], text=text, source_url=url, flags=[],
                       category=research.classify_source(url), language=language,
                       published_at=datetime(*published, 12, tzinfo=timezone.utc) if published else None))


def capture_prompts(monkeypatch):
    seen = []
    real = executors.OfflineExecutor.run

    def run(self, req):
        if req.purpose == "report":
            seen.append(req.prompt)
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "run", run)
    return seen


# --- the declared language --------------------------------------------------------------------

def test_primary_language_takes_the_primary_subtag_or_nothing():
    assert research.primary_language("en-US") == "en"
    assert research.primary_language("de, en") == "de"
    assert research.primary_language("EN") == "en"
    assert research.primary_language("zh_Hant_TW") == "zh"
    for bad in ("*", "i-klingon", "", None, "x-foo", "english", "e1"):
        assert research.primary_language(bad) == ""


def test_the_language_comes_from_the_content_language_header_first():
    page = '<html lang="en"><title>Leitfaden</title><body><p>Agenten und Rollen</p></body></html>'
    c = site({"/": (page, HTML | {"content-language": "de-DE"})})
    pages = research.crawl("https://docs.example/", depth=0, client=c, allow_private=True)
    assert pages[0]["language"] == "de"
    research.store_pages(pages, "https://docs.example/")
    with session() as s:
        assert s.query(Document).filter_by(url="https://docs.example/").one().language == "de"


def test_the_language_comes_from_the_html_tag_then_the_meta_tag_and_is_never_guessed():
    c = site({"/fr": ('<html lang="fr-CA"><title>Guide</title><body><p>Les agents</p></body></html>', HTML),
              "/it": ('<html><head><meta http-equiv="Content-Language" content="it"><title>Guida</title></head>'
                      '<body><p>Gli agenti</p></body></html>', HTML),
              "/none": ("<html><title>Guide</title><body><p>This page is plainly written in English, every "
                        "word of it, but it declares no language at all.</p></body></html>", HTML)})
    lang = {u: research.crawl(f"https://docs.example{u}", depth=0, client=c, allow_private=True)[0]["language"]
            for u in ("/fr", "/it", "/none")}
    assert lang == {"/fr": "fr", "/it": "it", "/none": ""}
    research.store_pages(research.crawl("https://docs.example/none", depth=0, client=c, allow_private=True),
                         "https://docs.example/none")
    research.store_pages([{"url": "https://docs.example/none", "title": "Guide", "text": "changed"}], "x")
    with session() as s:  # a refetch that declares nothing keeps nothing invented
        assert s.query(Document).filter_by(url="https://docs.example/none").one().language == ""


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Neuigkeiten</title><language>de-de</language>
<item><title>Agenten testen Agenten</title><link>https://news.example/1</link><description>Eins</description></item>
<item><title>Zweite Meldung</title><link>https://news.example/2</link><description>Zwei</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">
<title>Releases</title>
<entry><title>1.2.0</title><link href="https://rel.example/1.2.0"/><updated>2026-10-30T12:00:00Z</updated>
<content>Adds flow checkpoints</content></entry>
<entry xml:lang="fr"><title>1.1.0</title><link href="https://rel.example/1.1.0"/><updated>2026-09-01T08:00:00Z</updated>
<content>Corrections</content></entry>
</feed>"""


def test_the_language_comes_from_a_feed():
    rss = research.crawl("https://news.example/feed", client=site({"/feed": (RSS, {"content-type": "application/rss+xml"})}),
                         allow_private=True)
    assert [p["language"] for p in rss] == ["de", "de"]
    atom = research.crawl("https://rel.example/feed",
                          client=site({"/feed": (ATOM, {"content-type": "application/atom+xml"})}), allow_private=True)
    assert {p["url"]: p["language"] for p in atom} == {"https://rel.example/1.2.0": "en",
                                                        "https://rel.example/1.1.0": "fr"}, "the entry's xml:lang first"
    plain = RSS.replace(b"<language>de-de</language>", b"")
    told = research.crawl("https://news.example/feed", allow_private=True, client=site(
        {"/feed": (plain, {"content-type": "application/rss+xml", "content-language": "nl"})}))
    assert {p["language"] for p in told} == {"nl"}, "else the response header"
    research.store_pages(atom, "https://rel.example/feed")
    with session() as s:
        assert s.query(Document).filter_by(url="https://rel.example/1.1.0").one().language == "fr"


# --- the filter -------------------------------------------------------------------------------

def cand(url, published=None, language=""):
    return {"url": url, "published_at": datetime(*published).isoformat() if published else None,
            "language": language}


def test_a_date_range_leaves_out_the_right_documents_and_counts_them():
    docs = [cand("a", (2025, 12, 31, 23, 59)), cand("b", (2026, 1, 1, 0, 0)), cand("c", (2026, 6, 30, 23, 0)),
            cand("d", (2026, 7, 1, 0, 0))]
    kept, counts = research.filter_material(docs, date(2026, 1, 1), date(2026, 6, 30))
    assert [d["url"] for d in kept] == ["b", "c"], "both bounds are inclusive"
    assert counts == {"left_out": 2, "undated": 0, "unknown_language": 0}
    assert all(d["labels"] == [] for d in kept)
    kept, counts = research.filter_material(docs, after=date(2026, 6, 30))  # an open-ended range
    assert [d["url"] for d in kept] == ["c", "d"] and counts["left_out"] == 2
    aware = [{"url": "e", "published_at": "2026-07-01T01:00:00+02:00", "language": ""}]  # 2026-06-30 in UTC
    assert [d["url"] for d in research.filter_material(aware, before=date(2026, 6, 30))[0]] == ["e"]


def test_a_language_filter_leaves_out_the_right_documents_and_counts_them():
    docs = [cand("de", language="de"), cand("en", language="en"), cand("fr", language="fr"),
            cand("old-fr", (2020, 1, 1), "fr")]
    kept, counts = research.filter_material(docs, languages=["de", "en"])
    assert [d["url"] for d in kept] == ["de", "en"] and counts["left_out"] == 2
    kept, counts = research.filter_material(docs, after=date(2026, 1, 1), languages=["de", "en"])
    assert counts["left_out"] == 2, "a document outside both filters counts once"


def test_undated_and_undeclared_documents_are_kept_and_labelled_only_under_their_filter():
    docs = [cand("undated", language="en"), cand("silent", (2026, 3, 1)), cand("neither")]
    kept, counts = research.filter_material(docs, after=date(2026, 1, 1))
    assert {d["url"]: d["labels"] for d in kept} == {"undated": ["published=unknown"], "silent": [],
                                                     "neither": ["published=unknown"]}
    assert counts == {"left_out": 0, "undated": 2, "unknown_language": 0}
    kept, counts = research.filter_material(docs, languages=["en"])
    assert {d["url"]: d["labels"] for d in kept} == {"undated": [], "silent": ["language=unknown"],
                                                     "neither": ["language=unknown"]}
    assert counts == {"left_out": 0, "undated": 0, "unknown_language": 2}
    kept, counts = research.filter_material(docs)
    assert len(kept) == 3 and all(d["labels"] == [] for d in kept) and not any(counts.values())


def test_run_report_gives_the_model_only_the_kept_documents_and_states_the_counts(monkeypatch):
    seen = capture_prompts(monkeypatch)
    words = "orchestration of agent teams " * 10
    doc("https://a.example/in-range-en", words, (2026, 3, 1), "en")
    doc("https://a.example/undated-en", words, None, "en")
    doc("https://a.example/in-range-silent", words, (2026, 4, 1), "")
    doc("https://a.example/too-old-en", words, (2025, 1, 1), "en")
    doc("https://a.example/in-range-de", words, (2026, 5, 1), "de")
    doc("https://a.example/too-new-de", words, (2026, 12, 1), "de")
    rid = research.request_report("How does orchestration of agent teams work?", published_after="2026-01-01",
                                  published_before="2026-06-30", languages=["en"])
    out = research.run_report(rid)
    assert out["status"] == "done"
    prompt = seen[0]
    for kept in ("in-range-en", "undated-en", "in-range-silent"):
        assert f"https://a.example/{kept}" in prompt
    for gone in ("too-old-en", "in-range-de", "too-new-de"):
        assert f"https://a.example/{gone}" not in prompt
    assert 'url="https://a.example/undated-en"' in prompt
    undated = next(ln for ln in prompt.splitlines() if "undated-en" in ln)
    silent = next(ln for ln in prompt.splitlines() if "in-range-silent" in ln)
    assert 'published="unknown"' in undated and 'language="en"' in undated and "language=\"unknown\"" not in undated
    assert 'language="unknown"' in silent and 'published="2026-04-01"' in silent
    line = ("Filters: published 2026-01-01..2026-06-30; languages en; 3 documents left out, 1 kept undated, "
            "1 kept with unknown language.")
    assert out["report"].rstrip().endswith(f"## Method\n{line}")
    assert line in prompt, "the model is told the filters too"
    assert {s["url"] for s in out["sources"]} == {f"https://a.example/{k}" for k in
                                                  ("in-range-en", "undated-en", "in-range-silent")}
    with session() as s:  # an approved report keeps its method line
        r = s.get(ResearchReport, rid)
        assert f"## Method\n{line}" in research._approved_text(r, [], 0, "test")


def test_a_request_without_filters_has_no_method_line_and_no_labels(monkeypatch):
    seen = capture_prompts(monkeypatch)
    doc("https://a.example/plain", "orchestration of agent teams " * 10)
    out = research.run_report(research.request_report("How does orchestration of agent teams work?"))
    assert "## Method" not in out["report"] and "unknown" not in seen[0] and "Filters:" not in seen[0]


def test_the_requests_own_urls_are_filtered_the_same_way(monkeypatch):
    seen = capture_prompts(monkeypatch)
    page = "<html><title>Orchestration</title><body><p>" + "orchestration of agent teams " * 10 + "</p></body></html>"
    c = site({"/guide": (page, HTML | {"content-language": "fr"})})
    doc("https://a.example/en", "orchestration of agent teams " * 10, None, "en")
    rid = research.request_report("How does orchestration of agent teams work?", ["https://fr.example/guide"],
                                  languages=["en"])
    out = research.run_report(rid, client=c, allow_private=True)
    with session() as s:
        assert s.query(Document).filter_by(url="https://fr.example/guide").one().language == "fr"
    assert "https://fr.example/guide" not in seen[0] and "https://a.example/en" in seen[0]
    assert "Filters: languages en; 1 documents left out, 0 kept undated, 0 kept with unknown language." in out["report"]


# --- the request ------------------------------------------------------------------------------

def test_invalid_ranges_and_subtags_are_refused():
    q = "Which agent framework fits a hierarchy?"
    for bad in ({"published_after": "2026-06-30", "published_before": "2026-01-01"}, {"languages": ["english"]},
                {"languages": ["e"]}, {"languages": ["en-US"]}, {"languages": ["e1"]},
                {"published_after": "not a date"}):
        with pytest.raises(ValueError):
            research.submit(q, **bad)
    out = research.submit(q, published_after="2026-01-01", published_before="2026-01-01", languages="DE, en, de")
    [r] = research.list_reports()
    assert r["id"] == out["id"] and r["languages"] == ["de", "en"]
    assert r["published_after"] == r["published_before"] == "2026-01-01", "a one-day range is fine"


def test_requests_that_differ_only_in_their_filters_are_not_duplicates():
    q = "Which message queue fits small teams?"
    plain = research.submit(q)
    german = research.submit(q, languages=["de"])
    assert german["duplicate"] is False and german["id"] != plain["id"]
    assert research.submit(q, languages="de") == {"id": german["id"], "duplicate": True}
    assert research.submit(q) == {"id": plain["id"], "duplicate": True}
    recent = research.submit(q, languages=["de"], published_after="2026-01-01")
    assert recent["duplicate"] is False and recent["id"] not in (plain["id"], german["id"])
    later = research.submit(q, languages=["de"], published_after=date(2026, 2, 1))
    assert later["duplicate"] is False
    assert research.submit(q, languages=["DE"], published_after="2026-02-01") == {"id": later["id"], "duplicate": True}


def test_the_api_takes_and_returns_the_filters(monkeypatch):
    monkeypatch.setattr(research, "run_report", lambda rid, **kw: None)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        body = {"question": "Which CI service suits a monorepo?", "published_after": "2026-01-01",
                "published_before": "2026-06-30", "languages": ["en", "DE"]}
        first = app.post("/api/research/requests", json=body).json()
        assert first["duplicate"] is False
        again = app.post("/api/research/requests", json={**body, "languages": "de,en"}).json()
        assert again == {"id": first["id"], "duplicate": True}
        for bad in ({"published_after": "2026-07-01"}, {"languages": ["english"]}, {"languages": "e"},
                    {"published_before": "soon"}):
            resp = app.post("/api/research/requests", json={**body, "question": "Something else entirely?", **bad})
            assert resp.status_code == 400, bad
        old = app.post("/api/research/requests", json={"question": "An older request without filters?"}).json()
        rows = {r["id"]: r for r in app.get("/api/research/requests").json()}
        r = rows[first["id"]]
        assert (r["published_after"], r["published_before"], r["languages"]) == ("2026-01-01", "2026-06-30",
                                                                                 ["de", "en"])
        assert rows[old["id"]]["languages"] == [] and rows[old["id"]]["published_after"] is None
        records = app.get("/api/audit?action=research_request").json()["records"]
        assert {str(first["id"]), str(old["id"])} <= {str(e.get("target")) for e in records}


def test_an_existing_database_gets_the_new_columns(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE documents (id INTEGER PRIMARY KEY, url VARCHAR(2048))"))
        conn.execute(text("CREATE TABLE research_reports (id INTEGER PRIMARY KEY, question TEXT)"))
        conn.execute(text("INSERT INTO documents (url) VALUES ('https://old.example/')"))
    db._add_missing_columns(eng)
    insp = inspect(eng)
    assert "language" in {c["name"] for c in insp.get_columns("documents")}
    assert {"published_after", "published_before", "languages"} <= {c["name"] for c in
                                                                    insp.get_columns("research_reports")}
    with eng.connect() as conn:
        assert conn.execute(text("SELECT language FROM documents")).scalar() == ""
    # a migrated row has no languages at all (a callable default gets no DDL default): read as any
    migrated = ResearchReport(id=1, question="An older request?", urls=[], sources=[], languages=None,
                              created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert research.report_dict(migrated)["languages"] == []
    assert research._filters(None, None, migrated.languages) == research._filters(None, None, [])
