import httpx

from agent_company import research

PAGES = {
    "/": '<html><title>Home</title><body><p>Agent orchestration guide</p><a href="/a">A</a>'
         '<a href="/b#x">B</a><a href="https://other.example/">ext</a><script>no()</script></body></html>',
    "/a": '<title>A</title><p>CrewAI teams and roles</p><a href="/c">C</a>',
    "/b": '<title>B</title><p>LangGraph state machines</p>',
    "/c": '<title>C</title><p>deep page</p>',
    "/private": '<title>P</title>',
}


def client(disallow=""):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=f"User-agent: *\nDisallow: {disallow}\n" if disallow else "")
        body = PAGES.get(req.url.path)
        if body is None:
            return httpx.Response(404, text="nope")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_crawl_follows_links_on_same_host_to_depth():
    pages = research.crawl("https://docs.example/", depth=1, client=client(), allow_private=True)
    assert [p["url"] for p in pages] == ["https://docs.example/", "https://docs.example/a",
                                         "https://docs.example/b"]
    assert "no()" not in pages[0]["text"] and "Agent orchestration" in pages[0]["text"]
    deeper = research.crawl("https://docs.example/", depth=2, client=client(), allow_private=True)
    assert "https://docs.example/c" in [p["url"] for p in deeper]
    capped = research.crawl("https://docs.example/", depth=2, max_pages=2, client=client(), allow_private=True)
    assert len(capped) == 2


def test_robots_and_private_addresses_are_respected():
    pages = research.crawl("https://docs.example/", depth=1, client=client("/a"), allow_private=True)
    blocked = [p for p in pages if p.get("error")]
    assert blocked and blocked[0]["url"].endswith("/a")
    local = research.crawl("http://127.0.0.1:9/", client=client(), allow_private=False)
    assert local[0]["error"] == "private or unresolvable address"


def test_redirects_go_through_the_same_checks(monkeypatch):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        if req.url.host == "docs.example" and req.url.path == "/moved":
            return httpx.Response(302, headers={"location": "/a"})
        if req.url.host == "docs.example" and req.url.path == "/evil":
            return httpx.Response(302, headers={"location": "http://metadata.internal/latest"})
        if req.url.host == "metadata.internal":
            raise AssertionError("private host must not be fetched")
        return httpx.Response(200, text=PAGES.get(req.url.path, ""), headers={"content-type": "text/html"})

    monkeypatch.setattr(research, "_is_public_host", lambda h: h == "docs.example")
    c = httpx.Client(transport=httpx.MockTransport(handler))
    moved = research.crawl("https://docs.example/moved", depth=0, client=c, allow_private=False)
    assert moved[-1]["url"] == "https://docs.example/a" and "CrewAI" in moved[-1]["text"]
    evil = research.crawl("https://docs.example/evil", depth=0, client=c, allow_private=False)
    assert "redirect" in evil[0]["error"]
    off_host = research.crawl("https://docs.example/evil", depth=0, client=c, allow_private=False, same_host=False)
    assert off_host[-1]["error"] == "private or unresolvable address"


def test_collect_store_and_search():
    r = research.collect("https://docs.example/", client=client(), allow_private=True)
    assert r["stored"] == 3
    hits = research.search("crewai roles")
    assert hits and hits[0]["url"].endswith("/a")
    assert research.search("zz") == []


def test_rotation_is_round_robin():
    research.add_source("https://docs.example/", depth=0)
    research.add_source("https://docs.example/b", depth=0)
    roster = research.rotation_roster()
    assert len(roster) >= 2
    first = research.rotation_tick(client=client(), allow_private=True)
    second = research.rotation_tick(client=client(), allow_private=True)
    assert first["agent_id"] == roster[0] and second["agent_id"] == roster[1]
    assert {first["url"], second["url"]} == {"https://docs.example/", "https://docs.example/b"}
    assert research.rotation_tick(min_age_seconds=3600, client=client(), allow_private=True) is None
